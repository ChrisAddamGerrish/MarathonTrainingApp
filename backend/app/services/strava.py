"""Import workouts from Strava into the activity log.

Setup (once):
  1. Create an API application at https://www.strava.com/settings/api and put its Client ID and
     Client Secret in strava.env (see strava.env.example).
  2. Click "Connect with Strava" on the Activity log page and approve access on Strava's site.

Syncing is manual: nothing is fetched until you click "Sync now" on the Activity log page. Each
sync asks for activities since shortly before the newest one already seen, never earlier than the
Monday of the week you connected. repository.import_strava_activity decides what each one becomes.

The same workout often reaches Strava twice (a watch and Peloton both upload the ride). Recordings
of the same kind whose times overlap by more than half are treated as one workout: only the most
complete (has distance, then output) becomes an entry, and the others are tied to it.

The OAuth tokens live in config.STRAVA_TOKEN_FILE (git-ignored), not marathon.db, because the
database is committed to git.
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
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.core import config
from backend.app.core.database import SessionLocal
from backend.app.core.errors import AppError, ConflictError
from backend.app.repository import repository as repo
from backend.app.schemas.schemas import ActivityIn
from backend.app.services.planning import RUN_CATEGORIES

log = logging.getLogger("marathon.strava")

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
DEAUTHORIZE_URL = "https://www.strava.com/oauth/deauthorize"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
# read_all includes activities you've set to private ("Only You"), which is how many people record.
SCOPE = "activity:read_all"

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


class StravaError(AppError):
    """Strava refused a request or couldn't be reached."""

    status_code = 502


# Why the last attempt to connect failed; shown on the Activity log page until the next attempt.
connect_error: Optional[str] = None


# --------------------------------------------------------------------------
# Settings and saved state
# --------------------------------------------------------------------------


def client_settings() -> Optional[tuple[str, str]]:
    values = config.read_env_file(config.STRAVA_CONFIG_FILE)
    client_id, secret = values.get("STRAVA_CLIENT_ID"), values.get("STRAVA_CLIENT_SECRET")
    return (client_id, secret) if client_id and secret else None


def _load_state() -> Optional[dict[str, Any]]:
    try:
        return json.loads(config.STRAVA_TOKEN_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _save_state(state: dict[str, Any]) -> None:
    # Write then rename, so a crash never leaves half a token file behind.
    tmp = config.STRAVA_TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, config.STRAVA_TOKEN_FILE)


def is_connected() -> bool:
    return _load_state() is not None


def status() -> dict[str, Any]:
    state = _load_state() or {}
    return {
        "configured": client_settings() is not None,
        "connected": bool(state),
        "athlete": state.get("athlete_name"),
        "sync_start": state.get("sync_start"),
        "last_sync": state.get("last_sync"),
        "last_result": state.get("last_result"),
        "last_error": state.get("last_error") or connect_error,
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
            raise StravaError("Strava's rate limit was reached; the next sync will try again.") from e
        if e.code == 401:
            raise StravaError("Strava no longer accepts this app's access (it may have been revoked). "
                              "Disconnect, then connect again.") from e
        raise StravaError(f"Strava refused the request (HTTP {e.code}): {detail}") from e
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


def connect(code: str, granted_scope: str, today: date) -> dict[str, Any]:
    """Finish connecting: trade the code from Strava's redirect for tokens and save them."""
    global connect_error
    try:
        if not {"activity:read", "activity:read_all"} & set(granted_scope.split(",")):
            raise StravaError("Strava access to your activities wasn't granted. "
                              "Connect again and leave \"View data about your activities\" ticked.")
        client_id, secret = client_settings() or (None, None)
        if not client_id:
            raise ConflictError("Strava isn't set up: add STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET to strava.env.")
        tokens = _request(TOKEN_URL, form={"client_id": client_id, "client_secret": secret, "code": code,
                                           "grant_type": "authorization_code"})
    except AppError as e:
        connect_error = e.detail
        raise
    athlete = tokens.get("athlete") or {}
    state = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_at": tokens["expires_at"],
        "athlete_id": athlete.get("id"),
        "athlete_name": " ".join(filter(None, [athlete.get("firstname"), athlete.get("lastname")])) or None,
        # Import from the Monday of the week you connect in.
        "sync_start": (today - timedelta(days=today.weekday())).isoformat(),
        "newest_start": None,
        "last_sync": None,
        "last_result": None,
        "last_error": None,
    }
    _save_state(state)
    connect_error = None
    log.info("Connected to Strava as %s (athlete %s); importing from %s",
             state["athlete_name"], state["athlete_id"], state["sync_start"])
    return status()


def disconnect() -> dict[str, Any]:
    """Revoke the app's access on Strava (best effort) and forget the tokens. Imported activities stay."""
    state = _load_state()
    if state:
        try:
            _request(DEAUTHORIZE_URL, form={"access_token": state["access_token"]})
        except StravaError as e:
            log.warning("Strava deauthorize failed (tokens forgotten anyway): %s", e.detail)
        config.STRAVA_TOKEN_FILE.unlink(missing_ok=True)
        log.info("Disconnected from Strava")
    return status()


def _access_token(state: dict[str, Any]) -> str:
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
    _save_state(state)
    log.info("Strava access token refreshed")
    return state["access_token"]


# --------------------------------------------------------------------------
# Syncing
# --------------------------------------------------------------------------


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


def sync(session_factory: Callable[[], Session] = SessionLocal) -> dict[str, Any]:
    """Fetch recent Strava activities and import any new ones. Returns counts per outcome."""
    with _sync_lock:
        state = _load_state()
        if state is None:
            raise ConflictError("Strava isn't connected.")
        sync_start = date.fromisoformat(state["sync_start"])
        # Local midnight on the start Monday; Strava's `after` is a Unix time.
        after = int(datetime.combine(sync_start, datetime.min.time()).astimezone().timestamp())
        if state.get("newest_start"):
            after = max(after, state["newest_start"] - int(OVERLAP.total_seconds()))

        counts = {"created": 0, "matched": 0, "duplicates": 0, "known": 0, "skipped": 0}
        try:
            token = _access_token(state)
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

            with session_factory() as session:
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
        except AppError as e:
            state["last_error"] = e.detail
            _save_state(state)
            log.warning("Strava sync failed: %s", e.detail)
            raise

        if activities:
            newest = max(_epoch(a["start_date"]) for a in activities)
            state["newest_start"] = max(state.get("newest_start") or 0, newest)
        state.update(last_sync=datetime.now().astimezone().isoformat(timespec="seconds"),
                     last_result=counts, last_error=None)
        _save_state(state)
        log.info("Strava sync: %s new, %s matched to entries already logged, %s second recordings, "
                 "%s already imported, %s skipped", counts["created"], counts["matched"], counts["duplicates"],
                 counts["known"], counts["skipped"])
        return counts
