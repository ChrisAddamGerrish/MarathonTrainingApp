"""Import workouts from Strava into the activity log.

Setup (once, on the server): create an API application at https://www.strava.com/settings/api and
put its Client ID and Client Secret in strava.env (see strava.env.example). Each user then connects
their own Strava account: new users do it as the last step of registering (the app can't be used
until they have), and it's shown on the Activity log page afterwards.

Strava limits how many athletes one API application can connect (1, the app's owner, until Strava
raises it). Past that, connecting fails with Strava's error, shown on the connect page.

Syncing is manual: nothing is fetched until you click "Sync now" on the Activity log page. Each
sync asks for activities since shortly before the newest one already seen, never earlier than the
Monday of the week you connected. repository.import_strava_activity decides what each one becomes.

Beyond the log's own columns, each imported workout gets its Strava metrics (activity_metrics):
the summary fields come with the activity list at no extra cost; the detailed activity (mile
splits, laps, best efforts, description, calories, device) and time in each heart-rate zone (from
the heart-rate stream) take a request or two per workout, so each sync fetches them for at most
DETAIL_BUDGET workouts, newest first, and older ones fill in over later syncs (see enrich). Shoes
and bikes (with their Strava mileage) and the athlete's zones need the profile:read_all
permission; a connection made before it was asked for has to be renewed ("Allow more access").

The same workout often reaches Strava twice (a watch and Peloton both upload the ride). Recordings
of the same kind whose times overlap by more than half are treated as one workout: only the most
complete (has distance, then output) becomes an entry, and the others are tied to it.

The OAuth tokens and sync state live in config.STRAVA_TOKEN_FILE (git-ignored), keyed by user id,
not in marathon.db: secrets stay out of the database file.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.core import config
from backend.app.core.database import Tenant, user_session
from backend.app.core.errors import AppError, ConflictError
from backend.app import repository as repo
from backend.app.schemas import ActivityIn
from backend.app.services.planning import RUN_CATEGORIES

log = logging.getLogger("marathon.strava")

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
DEAUTHORIZE_URL = "https://www.strava.com/oauth/deauthorize"
API = "https://www.strava.com/api/v3"
ACTIVITIES_URL = f"{API}/athlete/activities"
ACTIVITY_URL = f"{API}/activities"  # /{id} (detailed), /{id}/streams
ATHLETE_URL = f"{API}/athlete"  # with profile:read_all: shoes and bikes, with their distances
ZONES_URL = f"{API}/athlete/zones"
# activity:read_all includes activities you've set to private ("Only You"), which is how many
# people record; profile:read_all is for shoes, bikes and heart-rate zones.
SCOPE = "activity:read_all,profile:read_all"
PROFILE_SCOPE = "profile:read_all"

# Strava sport types the log has a category for. Anything else (walks, yoga, swims...) is skipped,
# unless its name says it's a stretch (see STRETCH).
# Peloton uploads core and other classes as "Workout"; the plan counts core as Strength.
SPORT_CATEGORIES = {
    "Run": "Run", "TrailRun": "Run", "VirtualRun": "Run",
    "Ride": "Bike", "VirtualRide": "Bike", "GravelRide": "Bike", "MountainBikeRide": "Bike",
    "EBikeRide": "Bike", "EMountainBikeRide": "Bike",
    "Rowing": "Row", "VirtualRow": "Row",
    "WeightTraining": "Strength", "Crossfit": "Strength", "Workout": "Strength",
}
RACE_WORKOUT_TYPE = 1  # Strava's workout_type for a run marked as a race
# Stretching, warm-ups, cool-downs and mobility are logged as Stretch whatever sport type they
# were uploaded as (Peloton sends them as "Workout", like its strength classes). The plan has no
# Stretch sessions, so they never count toward a planned session.
STRETCH = re.compile(r"\b(stretch|stretching|warm[ -]?up|cool[ -]?down|mobility)\b", re.IGNORECASE)
METERS_PER_MILE = 1609.344
# Re-check this far back on every sync, for activities uploaded late (e.g. a watch synced later).
OVERLAP = timedelta(days=3)
PAGE_SIZE = 100
# Detailed activities fetched per sync (each also needs its heart-rate stream when it has one).
# Strava allows 100 requests per 15 minutes, so this leaves room for the list, athlete and zones.
DETAIL_BUDGET = 25
FEET_PER_METER = 3.28084
MPH_PER_MPS = 2.236936
# Gaps in a heart-rate stream longer than this (a pause) count as this long.
MAX_STREAM_GAP_S = 10
# Heart rates below this are a strap or watch losing contact, not a reading: they're left out of
# zone time, and an average below it (Strava averages the dropouts in) counts as no average.
MIN_REAL_HR = 30


class StravaError(AppError):
    """Strava refused a request or couldn't be reached. http_status is Strava's own answer, if any."""

    status_code = 502

    def __init__(self, detail: str, http_status: Optional[int] = None):
        super().__init__(detail)
        self.http_status = http_status


# Why each user's last attempt to connect failed; shown until their next attempt.
connect_errors: dict[int, str] = {}


# --------------------------------------------------------------------------
# Settings and saved state
# --------------------------------------------------------------------------


def client_settings() -> Optional[tuple[str, str]]:
    values = config.read_env_file(config.STRAVA_CONFIG_FILE)
    client_id, secret = values.get("STRAVA_CLIENT_ID"), values.get("STRAVA_CLIENT_SECRET")
    return (client_id, secret) if client_id and secret else None


# One lock around every read-modify-write of the token file (all users share it).
_file_lock = threading.RLock()


def _load_all() -> dict[str, dict[str, Any]]:
    """{user id (as text): that user's tokens and sync state}."""
    try:
        data = json.loads(config.STRAVA_TOKEN_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if "access_token" in data:  # the single-user format: it was user 1's (the database's owner)
        return {"1": data}
    return data.get("users", {})


def _load_state(user_id: int) -> Optional[dict[str, Any]]:
    return _load_all().get(str(user_id))


def _save_state(user_id: int, state: Optional[dict[str, Any]]) -> None:
    """Save (or with None, forget) one user's state. Write then rename, so a crash never leaves
    half a token file behind."""
    with _file_lock:
        everyone = _load_all()
        if state is None:
            everyone.pop(str(user_id), None)
        else:
            everyone[str(user_id)] = state
        tmp = config.STRAVA_TOKEN_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"users": everyone}, indent=2), encoding="utf-8")
        os.replace(tmp, config.STRAVA_TOKEN_FILE)


def has_profile_access(state: dict[str, Any]) -> bool:
    return PROFILE_SCOPE in (state.get("scope") or "").split(",")


def is_connected(user_id: int) -> bool:
    return _load_state(user_id) is not None


def status(user_id: int) -> dict[str, Any]:
    state = _load_state(user_id) or {}
    return {
        "configured": client_settings() is not None,
        "connected": bool(state),
        "athlete": state.get("athlete_name"),
        "sync_start": state.get("sync_start"),
        "last_sync": state.get("last_sync"),
        "last_result": state.get("last_result"),
        "last_error": state.get("last_error") or connect_errors.get(user_id),
        # Shoes, bikes and heart-rate zones need profile:read_all (connections from before it was
        # asked for don't have it).
        "profile_access": bool(state) and has_profile_access(state),
        "details_note": state.get("details_note"),
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _request(url: str, *, form: Optional[dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(url, data=data)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        log.warning("Strava HTTP %s from %s: %s", e.code, url.split("?")[0], detail)
        if e.code == 429:
            raise StravaError("Strava's rate limit was reached; the next sync will try again.", 429) from e
        if e.code == 401:
            raise StravaError("Strava no longer accepts this app's access (it may have been revoked). "
                              "Disconnect, then connect again.", 401) from e
        raise StravaError(f"Strava refused the request (HTTP {e.code}): {detail}", e.code) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise StravaError(f"Couldn't reach Strava: {getattr(e, 'reason', e)}") from e
    return json.loads(body) if body else None


# --------------------------------------------------------------------------
# Connecting
# --------------------------------------------------------------------------


def authorize_url(redirect_uri: str, state: str) -> str:
    settings = client_settings()
    if settings is None:
        raise ConflictError("Strava isn't set up: add STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET to strava.env.")
    query = urllib.parse.urlencode({
        "client_id": settings[0], "redirect_uri": redirect_uri, "response_type": "code",
        "approval_prompt": "auto", "scope": SCOPE, "state": state,
    })
    return f"{AUTHORIZE_URL}?{query}"


def connect(user_id: int, code: str, granted_scope: str, today: date) -> dict[str, Any]:
    """Finish connecting: trade the code from Strava's redirect for tokens and save them.
    A Strava account can only be connected to one user."""
    try:
        if not {"activity:read", "activity:read_all"} & set(granted_scope.split(",")):
            raise StravaError("Strava access to your activities wasn't granted. "
                              "Connect again and leave \"View data about your activities\" ticked.")
        client_id, secret = client_settings() or (None, None)
        if not client_id:
            raise ConflictError("Strava isn't set up: add STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET to strava.env.")
        tokens = _request(TOKEN_URL, form={"client_id": client_id, "client_secret": secret, "code": code,
                                           "grant_type": "authorization_code"})
        athlete = tokens.get("athlete") or {}
        others = {uid for uid, st in _load_all().items() if st.get("athlete_id") == athlete.get("id")}
        if athlete.get("id") is not None and others - {str(user_id)}:
            raise ConflictError("That Strava account is already connected to another user of this app.")
    except AppError as e:
        connect_errors[user_id] = e.detail
        raise
    previous = _load_state(user_id) or {}
    state = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_at": tokens["expires_at"],
        "scope": granted_scope,
        "athlete_id": athlete.get("id"),
        "athlete_name": " ".join(filter(None, [athlete.get("firstname"), athlete.get("lastname")])) or None,
        # Import from the Monday of the week you connect in.
        "sync_start": (today - timedelta(days=today.weekday())).isoformat(),
        "newest_start": None,
        "last_sync": None,
        "last_result": None,
        "last_error": None,
    }
    if previous.get("athlete_id") == state["athlete_id"]:
        # Connecting again (e.g. to allow more access): carry on syncing from where it was.
        for key in ("sync_start", "newest_start", "last_sync", "last_result"):
            state[key] = previous.get(key)
    _save_state(user_id, state)
    connect_errors.pop(user_id, None)
    log.info("User %s connected to Strava as %s (athlete %s); importing from %s",
             user_id, state["athlete_name"], state["athlete_id"], state["sync_start"])
    return status(user_id)


def disconnect(user_id: int) -> dict[str, Any]:
    """Revoke the app's access on Strava (best effort) and forget the tokens. Imported activities stay."""
    state = _load_state(user_id)
    if state:
        try:
            _request(DEAUTHORIZE_URL, form={"access_token": state["access_token"]})
        except StravaError as e:
            log.warning("Strava deauthorize failed (tokens forgotten anyway): %s", e.detail)
        _save_state(user_id, None)
        log.info("User %s disconnected from Strava", user_id)
    return status(user_id)


def _access_token(user_id: int, state: dict[str, Any]) -> str:
    """A valid access token, refreshing it (Strava's last about 6 hours) when it is about to expire."""
    if state["expires_at"] > time.time() + 300:
        return state["access_token"]
    client_id, secret = client_settings() or (None, None)
    if not client_id:
        raise ConflictError("Strava isn't set up: add STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET to strava.env.")
    tokens = _request(TOKEN_URL, form={"client_id": client_id, "client_secret": secret,
                                       "refresh_token": state["refresh_token"], "grant_type": "refresh_token"})
    state.update(access_token=tokens["access_token"], refresh_token=tokens["refresh_token"],
                 expires_at=tokens["expires_at"])
    _save_state(user_id, state)
    log.info("Strava access token refreshed for user %s", user_id)
    return state["access_token"]


# --------------------------------------------------------------------------
# Syncing
# --------------------------------------------------------------------------


def _num(value: Any, factor: float = 1, digits: int = 1) -> Optional[float]:
    return round(value * factor, digits) if isinstance(value, (int, float)) and value is not False else None


def _is_run(a: dict[str, Any]) -> bool:
    return SPORT_CATEGORIES.get(a.get("sport_type") or a.get("type")) == "Run"


def summary_metrics(a: dict[str, Any]) -> dict[str, Any]:
    """activity_metrics values from the fields every Strava activity (summary or detailed) has."""
    cadence = a.get("average_cadence")
    route = a.get("map") or {}
    return {
        "strava_id": a["id"],
        "start_time": (a.get("start_date_local") or "")[11:16] or None,
        "elapsed_min": _num(a.get("elapsed_time"), 1 / 60),
        "elevation_gain_ft": _num(a.get("total_elevation_gain"), FEET_PER_METER, 0),
        "avg_hr": _num(a.get("average_heartrate"), digits=0) if (a.get("average_heartrate") or 0) >= MIN_REAL_HR else None,
        "max_hr": _num(a.get("max_heartrate"), digits=0),
        # Strava counts a run's cadence per leg; steps per minute is twice that.
        "avg_cadence": _num(cadence, 2 if _is_run(a) else 1, 0),
        "avg_watts": _num(a.get("average_watts"), digits=0),
        "weighted_avg_watts": _num(a.get("weighted_average_watts"), digits=0),
        "max_watts": _num(a.get("max_watts"), digits=0),
        "avg_speed_mph": _num(a.get("average_speed"), MPH_PER_MPS, 2),
        "max_speed_mph": _num(a.get("max_speed"), MPH_PER_MPS, 2),
        "suffer_score": _num(a.get("suffer_score"), digits=0),
        "pr_count": a.get("pr_count"),
        "trainer": a.get("trainer"),
        "gear_id": a.get("gear_id"),
        "polyline": route.get("polyline") or route.get("summary_polyline") or None,
    }


def _split(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "distance_mi": _num(entry.get("distance"), 1 / METERS_PER_MILE, 2),
        "moving_s": entry.get("moving_time"),
        "elapsed_s": entry.get("elapsed_time"),
        "elevation_ft": _num(entry.get("elevation_difference", entry.get("total_elevation_gain")), FEET_PER_METER, 0),
        "avg_hr": _num(entry.get("average_heartrate"), digits=0),
    }


def detail_metrics(d: dict[str, Any]) -> dict[str, Any]:
    """activity_metrics values from Strava's detailed activity (GET /activities/{id})."""
    return {
        **summary_metrics(d),
        "description": (d.get("description") or "").strip() or None,
        "calories": _num(d.get("calories"), digits=0),
        "device_name": d.get("device_name"),
        "splits": [{"mile": s.get("split"), **_split(s)} for s in d.get("splits_standard") or []] or None,
        "laps": [{"name": lap.get("name"), **_split(lap), "max_hr": _num(lap.get("max_heartrate"), digits=0)}
                 for lap in d.get("laps") or []] or None,
        "best_efforts": [{"name": e.get("name"), "distance_m": e.get("distance"), "elapsed_s": e.get("elapsed_time"),
                          "moving_s": e.get("moving_time"), "pr_rank": e.get("pr_rank")}
                         for e in d.get("best_efforts") or []] or None,
    }


def zone_seconds(times: list[int], heart_rates: list[float], zones: list[dict[str, Any]]) -> list[int]:
    """Seconds spent in each heart-rate zone, from a stream's time and heartrate data."""
    totals = [0] * len(zones)
    for i, hr in enumerate(heart_rates):
        if hr is None or hr < MIN_REAL_HR:
            continue
        step = min(times[i + 1] - times[i], MAX_STREAM_GAP_S) if i + 1 < len(times) else 1
        for n, z in enumerate(zones):
            if hr >= z["min"] and (z["max"] == -1 or hr < z["max"]):
                totals[n] += step
                break
    return totals


def to_activity(a: dict[str, Any]) -> Optional[dict[str, Any]]:
    """activity_log values for a Strava activity (as listed by the API), or None to skip it."""
    sport = a.get("sport_type") or a.get("type")
    category = "Stretch" if STRETCH.search(a.get("name") or "") else SPORT_CATEGORIES.get(sport)
    if category is None:
        return None
    if category == "Run" and a.get("workout_type") == RACE_WORKOUT_TYPE:
        category = "Race"
    distance = a.get("distance") or 0
    moving = a.get("moving_time") or 0
    values = ActivityIn(
        activity_date=a["start_date_local"][:10],  # the date where you were, not UTC
        category=category,
        actual_session=(a.get("name") or "").strip() or sport,
        distance_mi=round(distance / METERS_PER_MILE, 2) if distance else None,
        duration_min=round(moving / 60, 1) if moving else None,  # moving time: stops don't count
        output_kj=round(a["kilojoules"], 1) if a.get("kilojoules") else None,
        notes=f"Imported from Strava: https://www.strava.com/activities/{a['id']}",
    ).model_dump()
    del values["plan_id"]  # chosen by the import (auto-link), not by Strava
    return values


def _epoch(iso_utc: str) -> int:
    return int(datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).timestamp())


def _span(a: dict[str, Any]) -> tuple[int, int]:
    start = _epoch(a["start_date"])
    return start, start + max(a.get("elapsed_time") or a.get("moving_time") or 0, 60)


def _same_workout(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two recordings overlapping for more than half of the shorter one."""
    (start_a, end_a), (start_b, end_b) = _span(a), _span(b)
    return min(end_a, end_b) - max(start_a, start_b) > 0.5 * min(end_a - start_a, end_b - start_b)


def _kind(category: str) -> str:
    return "Run" if category in RUN_CATEGORIES else category


def group_recordings(items: list[tuple[dict, dict]]) -> list[list[tuple[dict, dict]]]:
    """Group (Strava activity, log values) pairs that are the same workout recorded more than once."""
    groups: list[list[tuple[dict, dict]]] = []
    for a, values in sorted(items, key=lambda item: item[0]["start_date"]):
        for group in groups:
            if _kind(group[0][1]["category"]) == _kind(values["category"]) and any(
                    _same_workout(a, other) for other, _ in group):
                group.append((a, values))
                break
        else:
            groups.append([(a, values)])
    return groups


def _completeness(item: tuple[dict, dict]) -> tuple:
    values = item[1]
    return values["distance_mi"] is not None, values["output_kj"] is not None, values["duration_min"] or 0


_sync_lock = threading.Lock()


def sync(tenant: Tenant, session_factory: Callable[[Tenant], Session] = user_session) -> dict[str, Any]:
    """Fetch the user's recent Strava activities and import any new ones. Returns counts per outcome."""
    user_id = tenant.user_id
    with _sync_lock:
        state = _load_state(user_id)
        if state is None:
            raise ConflictError("Strava isn't connected.")
        sync_start = date.fromisoformat(state["sync_start"])
        # Local midnight on the start Monday; Strava's `after` is a Unix time.
        after = int(datetime.combine(sync_start, datetime.min.time()).astimezone().timestamp())
        if state.get("newest_start"):
            after = max(after, state["newest_start"] - int(OVERLAP.total_seconds()))

        counts: dict[str, Any] = {"created": 0, "matched": 0, "duplicates": 0, "known": 0, "skipped": 0}
        try:
            token = _access_token(user_id, state)
            activities, page = [], 1
            while True:
                batch = _request(f"{ACTIVITIES_URL}?after={after}&per_page={PAGE_SIZE}&page={page}", token=token)
                activities += batch
                if len(batch) < PAGE_SIZE:
                    break
                page += 1

            items = []
            for a in activities:
                try:
                    values = to_activity(a)
                except ValidationError as e:
                    log.warning("Strava activity %s skipped: %s", a.get("id"), e)
                    values = None
                if values is None or values["activity_date"] < sync_start:
                    counts["skipped"] += 1
                else:
                    items.append((a, values))

            with session_factory(tenant) as session:
                for group in group_recordings(items):
                    best, best_values = max(group, key=_completeness)
                    seen = repo.strava_import_for(session, [a["id"] for a, _ in group])
                    if seen is None:
                        result = repo.import_strava_activity(session, best["id"], best_values)
                        counts[result["outcome"]] += 1
                        entry = result["activity_id"]
                    else:
                        entry = seen["activity_id"]
                    for a, _ in group:
                        if seen is None and a is best:
                            continue
                        outcome = repo.tie_strava_duplicate(session, a["id"], entry, best_values)["outcome"]
                        counts["duplicates" if outcome == "duplicate" else outcome] += 1
                    repo.save_metrics(session, entry, summary_metrics(best))
            state["details_note"] = None
            counts.update(_refresh_athlete(tenant, state, token, session_factory))
            counts.update(enrich(tenant, state, token, session_factory))
        except AppError as e:
            state["last_error"] = e.detail
            _save_state(user_id, state)
            log.warning("Strava sync failed for user %s: %s", user_id, e.detail)
            raise

        if activities:
            newest = max(_epoch(a["start_date"]) for a in activities)
            state["newest_start"] = max(state.get("newest_start") or 0, newest)
        state.update(last_sync=datetime.now().astimezone().isoformat(timespec="seconds"),
                     last_result=counts, last_error=None)
        _save_state(user_id, state)
        log.info("Strava sync for user %s: %s new, %s matched to entries already logged, %s second recordings, "
                 "%s already imported, %s skipped", user_id, counts["created"], counts["matched"],
                 counts["duplicates"], counts["known"], counts["skipped"])
        return counts


# --------------------------------------------------------------------------
# Details, zones and gear
# --------------------------------------------------------------------------


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enrich(tenant: Tenant, state: dict[str, Any], token: str,
           session_factory: Callable[[Tenant], Session] = user_session,
           budget: Optional[int] = None) -> dict[str, Any]:
    """Fetch the detailed activity (and heart-rate zone time) of up to `budget` imported workouts
    that don't have it yet, newest first. Hitting Strava's rate limit stops early without failing
    the sync; the rest wait for a later one. Returns {"details": fetched, "details_pending": left}."""
    fetched, note = 0, None
    with session_factory(tenant) as session:
        todo = repo.activities_needing_details(session)
        zones = repo.hr_zones(session)
        for activity_id, strava_id in todo[:budget or DETAIL_BUDGET]:
            try:
                detail = _request(f"{ACTIVITY_URL}/{strava_id}?include_all_efforts=false", token=token)
                values = {**detail_metrics(detail), "details_fetched_at": _now_utc()}
                if zones and detail.get("has_heartrate"):
                    streams = _request(f"{ACTIVITY_URL}/{strava_id}/streams?keys=time,heartrate&key_by_type=true",
                                       token=token) or {}
                    times, hrs = (streams.get("time") or {}).get("data"), (streams.get("heartrate") or {}).get("data")
                    if times and hrs:
                        values["hr_zone_seconds"] = zone_seconds(times, hrs, zones)
            except StravaError as e:
                if e.http_status == 404:  # gone from Strava (or private to someone else): don't ask again
                    values = {"strava_id": strava_id, "details_fetched_at": _now_utc()}
                else:
                    note = e.detail if e.http_status != 429 else \
                        "Strava's rate limit was reached; the remaining details come with a later sync."
                    break
            repo.save_metrics(session, activity_id, values)
            fetched += 1
        pending = len(todo) - fetched
    if note:
        state["details_note"] = note
    if fetched:
        log.info("Strava details for user %s: %s fetched, %s still to do", tenant.user_id, fetched, pending)
    return {"details": fetched, "details_pending": pending}


def _gear_items(athlete: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"gear_id": g["id"], "kind": kind, "name": g.get("name") or g.get("nickname") or g["id"],
             "distance_mi": round((g.get("distance") or 0) / METERS_PER_MILE, 1),
             "is_primary": bool(g.get("primary")), "retired": bool(g.get("retired"))}
            for kind, key in (("shoe", "shoes"), ("bike", "bikes")) for g in athlete.get(key) or []]


def _refresh_athlete(tenant: Tenant, state: dict[str, Any], token: str,
                     session_factory: Callable[[Tenant], Session] = user_session) -> dict[str, Any]:
    """Refresh the athlete's heart-rate / power zones and shoes and bikes (2 requests). Without
    profile:read_all there's nothing to do; if Strava refuses anyway, the sync carries on."""
    if not has_profile_access(state):
        return {"gear": None}
    try:
        zones = _request(ZONES_URL, token=token) or {}
        athlete = _request(ATHLETE_URL, token=token) or {}
    except StravaError as e:
        if e.http_status in (401, 403):  # the permission was taken back on Strava's side
            state["scope"] = ",".join(x for x in (state.get("scope") or "").split(",") if x != PROFILE_SCOPE)
        else:
            state["details_note"] = e.detail
        return {"gear": None}
    items = _gear_items(athlete)
    with session_factory(tenant) as session:
        repo.save_zones(session, ((zones.get("heart_rate") or {}).get("zones")),
                        ((zones.get("power") or {}).get("zones")))
        repo.save_gear(session, items)
    return {"gear": len(items)}
