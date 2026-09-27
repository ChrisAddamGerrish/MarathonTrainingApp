"""Tests for the Strava import: converting activities, the import rules, syncing and connecting.

Strava itself is never called: strava._request is replaced with a fake.

Run from the project root:   .venv\\Scripts\\python.exe -m unittest tests.test_strava -v
"""
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from tests import OWNER_ID, TEST_DB, TMP, owner_session, owner_tenant, signed_in_client

from sqlalchemy import delete, select

from backend.app.core import config
from backend.app.core.database import engine, init_db
from backend.app.models.models import ActivityLog, StravaImport, TrainingPlan
from backend.app.repository import repository as repo
from backend.app.services import strava

# Week 2 of the plan: Tue has a bike session and an optional run, Wed two strength sessions,
# Thu a run. Nothing is logged that week in the database copy the tests use.
WEEK2 = [date(2026, 9, 21 + i) for i in range(7)]
TUE, WED, THU, SAT, SUN = WEEK2[1], WEEK2[2], WEEK2[3], WEEK2[5], WEEK2[6]


def setUpModule():
    if Path(config.DB_PATH) != TEST_DB or config.STRAVA_TOKEN_FILE.parent != TMP:
        raise RuntimeError(f"Tests must use the temp files, but the app is using {config.DB_PATH}")
    init_db()


def tearDownModule():
    engine.dispose()


def strava_activity(strava_id, day, sport="Run", **extra):
    """An activity as Strava's /athlete/activities lists it."""
    return {"id": strava_id, "name": f"{sport} {strava_id}", "sport_type": sport, "type": sport,
            "start_date_local": f"{day.isoformat()}T07:00:00Z", "start_date": f"{day.isoformat()}T11:00:00Z",
            "distance": 8046.72, "moving_time": 2700, "elapsed_time": 3000, **extra}


def plan_ids(day, category):
    with owner_session() as s:
        return list(s.scalars(select(TrainingPlan.plan_id).where(
            TrainingPlan.week == 2, TrainingPlan.day == day.strftime("%a"), TrainingPlan.category == category,
        ).order_by(TrainingPlan.entry_order)))


class WeekTwoCleanup(unittest.TestCase):
    """Leaves week 2, the import records and the Strava files as it found them."""

    def tearDown(self):
        with owner_session() as s:
            s.execute(delete(ActivityLog).where(ActivityLog.activity_date.between(WEEK2[0], WEEK2[-1])))
            s.execute(delete(StravaImport))
            s.commit()
        config.STRAVA_TOKEN_FILE.unlink(missing_ok=True)
        config.STRAVA_CONFIG_FILE.unlink(missing_ok=True)
        strava.connect_errors.clear()

    def run_import(self, activity):
        with owner_session() as s:
            return repo.import_strava_activity(s, activity["id"], strava.to_activity(activity))

    def activity(self, activity_id):
        with owner_session() as s:
            return repo.get_activity(s, activity_id)


class ConversionTests(unittest.TestCase):
    def test_run_uses_moving_time_and_miles(self):
        values = strava.to_activity(strava_activity(1, THU))
        self.assertEqual(values["category"], "Run")
        self.assertEqual(values["activity_date"], THU)
        self.assertEqual(values["distance_mi"], 5.0)
        self.assertEqual(values["duration_min"], 45.0)  # moving_time, not elapsed_time
        self.assertIn("strava.com/activities/1", values["notes"])
        self.assertNotIn("plan_id", values)

    def test_race_ride_and_strength(self):
        self.assertEqual(strava.to_activity(strava_activity(2, THU, workout_type=1))["category"], "Race")
        ride = strava.to_activity(strava_activity(3, TUE, "VirtualRide", kilojoules=612.44))
        self.assertEqual((ride["category"], ride["output_kj"]), ("Bike", 612.4))
        self.assertEqual(strava.to_activity(strava_activity(4, WED, "WeightTraining", distance=0))["distance_mi"], None)

    def test_stretches_warm_ups_and_mobility_are_stretch(self):
        for name, sport in [("5 min Lower Body Stretch", "Workout"), ("5 min Pre-Run Warm Up", "Workout"),
                            ("10 min Post-Run Cool Down", "Run"), ("Mobility Flow", "Yoga"),
                            ("Full Body Stretching", "WeightTraining")]:
            with self.subTest(name=name):
                self.assertEqual(strava.to_activity(strava_activity(7, THU, sport, name=name))["category"], "Stretch")
        # A strength class that merely mentions stretching in passing isn't a stretch.
        self.assertEqual(strava.to_activity(strava_activity(8, THU, "WeightTraining", name="Stretchy bands"))["category"],
                         "Strength")

    def test_unsupported_sports_are_skipped(self):
        for sport in ("Walk", "Yoga", "Swim"):
            self.assertIsNone(strava.to_activity(strava_activity(5, THU, sport)))

    def test_local_date_is_used(self):
        late = strava_activity(6, THU, start_date_local="2026-09-24T23:30:00Z", start_date="2026-09-25T03:30:00Z")
        self.assertEqual(strava.to_activity(late)["activity_date"], THU)


class ImportRuleTests(WeekTwoCleanup):
    def test_new_activity_is_created_and_linked_to_the_plan(self):
        result = self.run_import(strava_activity(10, THU))
        self.assertEqual(result["outcome"], "created")
        self.assertEqual(result["plan_id"], plan_ids(THU, "Run")[0])
        self.assertEqual(self.activity(result["activity_id"])["plan_id"], result["plan_id"])

    def test_same_activity_is_never_imported_twice(self):
        self.run_import(strava_activity(11, THU))
        self.assertEqual(self.run_import(strava_activity(11, THU))["outcome"], "known")

    def test_deleted_import_stays_deleted(self):
        created = self.run_import(strava_activity(12, THU))
        with owner_session() as s:
            repo.delete_activity(s, created["activity_id"])
        self.assertEqual(self.run_import(strava_activity(12, THU))["outcome"], "known")

    def test_workout_already_logged_by_hand_is_matched_not_duplicated(self):
        with owner_session() as s:
            mine = repo.create_activity(s, {"activity_date": THU, "category": "Run", "actual_session": "my run",
                                            "distance_mi": 5, "duration_min": 44, "output_kj": None,
                                            "plan_id": None, "notes": None})
        result = self.run_import(strava_activity(13, THU))
        self.assertEqual((result["outcome"], result["activity_id"]), ("matched", mine["activity_id"]))
        self.assertEqual(self.activity(mine["activity_id"])["actual_session"], "my run")  # left alone
        # A second run that day is a different workout, so it is added.
        self.assertEqual(self.run_import(strava_activity(14, THU))["outcome"], "created")

    def test_two_strength_sessions_fill_in_plan_order(self):
        upper, core = plan_ids(WED, "Strength")
        first = self.run_import(strava_activity(20, WED, "WeightTraining"))
        second = self.run_import(strava_activity(21, WED, "Workout"))
        self.assertEqual((first["plan_id"], second["plan_id"]), (upper, core))
        # Both of Wednesday's sessions are done, so a third makes up Monday's (open in this copy).
        self.assertEqual(self.run_import(strava_activity(22, WED, "WeightTraining"))["plan_id"], "W2-Mon")

    def test_short_extra_does_not_pass_for_a_missed_session(self):
        # 20 minutes is under half of Monday's 60-minute target.
        self.run_import(strava_activity(23, WED, "WeightTraining"))
        self.run_import(strava_activity(24, WED, "WeightTraining"))
        self.assertIsNone(self.run_import(strava_activity(25, WED, "WeightTraining", moving_time=1200))["plan_id"])

    def test_optional_session_only_when_nothing_required_is_open(self):
        (optional_run,) = plan_ids(TUE, "Run")
        self.assertEqual(self.run_import(strava_activity(30, TUE))["plan_id"], optional_run)

    def test_skipped_session_is_not_linked(self):
        (thu_run,) = plan_ids(THU, "Run")
        with owner_session() as s:
            repo.skip_session(s, thu_run, "sick")
        try:
            self.assertIsNone(self.run_import(strava_activity(31, THU))["plan_id"])
        finally:
            with owner_session() as s:
                repo.unskip_session(s, thu_run)

    def test_session_moved_to_a_later_day_is_linked(self):
        # Saturday's bike ride done on Sunday, when the plan has no ride.
        self.assertEqual(self.run_import(strava_activity(33, SUN, "Ride"))["plan_id"], "W2-Sat")

    def test_extra_workout_never_takes_a_later_days_slot(self):
        # An extra run on Wednesday must leave Thursday's run for Thursday.
        self.assertIsNone(self.run_import(strava_activity(34, WED))["plan_id"])
        self.assertEqual(self.run_import(strava_activity(35, THU))["plan_id"], plan_ids(THU, "Run")[0])

    def test_optional_sessions_are_not_used_for_moved_days(self):
        # Tuesday's optional overflow run is open, but a run on Thursday takes Thursday's slot, and
        # a second one isn't pushed back onto the optional run.
        self.run_import(strava_activity(36, THU))
        self.assertIsNone(self.run_import(strava_activity(37, THU))["plan_id"])

    def test_imports_are_recorded_in_history(self):
        created = self.run_import(strava_activity(32, THU))
        with owner_session() as s:
            entries = repo.history_entries(s, created["activity_id"], 10)
        self.assertEqual([e["action"] for e in entries], ["INSERT"])


class FakeStrava:
    """Stands in for strava._request: answers the token and activity-list endpoints."""

    def __init__(self, activities=(), fail=None):
        self.activities, self.fail, self.calls = list(activities), fail, []

    def __call__(self, url, *, form=None, token=None):
        self.calls.append((url, form, token))
        if self.fail:
            raise strava.StravaError(self.fail)
        if url == strava.TOKEN_URL:
            return {"access_token": "access-2", "refresh_token": "refresh-2", "expires_at": int(time.time()) + 21600,
                    "athlete": {"id": 42, "firstname": "Test", "lastname": "Runner"}}
        if url == strava.DEAUTHORIZE_URL:
            return {}
        query = parse_qs(urlparse(url).query)
        page, size = int(query["page"][0]), int(query["per_page"][0])
        return self.activities[(page - 1) * size: page * size]


def write_settings():
    config.STRAVA_CONFIG_FILE.write_text("STRAVA_CLIENT_ID=12345\nSTRAVA_CLIENT_SECRET=shh\n", encoding="utf-8")


def write_tokens(**overrides):
    state = {"access_token": "access-1", "refresh_token": "refresh-1", "expires_at": int(time.time()) + 3600,
             "athlete_id": 42, "athlete_name": "Test Runner", "sync_start": "2026-09-21", "newest_start": None,
             "last_sync": None, "last_result": None, "last_error": None, **overrides}
    strava._save_state(OWNER_ID, state)


def signed_in_without_strava():
    """A signed-in client for the owner, whose (faked) Strava connection is then removed."""
    client = signed_in_client()
    strava._save_state(OWNER_ID, None)
    return client


class SyncTests(WeekTwoCleanup):
    def test_sync_imports_and_counts(self):
        write_settings()
        write_tokens()
        fake = FakeStrava([strava_activity(40, THU), strava_activity(41, TUE, "Ride"),
                           strava_activity(42, TUE, "Walk"), strava_activity(43, date(2026, 9, 20))])
        with mock.patch.object(strava, "_request", fake):
            counts = strava.sync(owner_tenant())
            again = strava.sync(owner_tenant())
        self.assertEqual(counts, {"created": 2, "matched": 0, "duplicates": 0, "known": 0, "skipped": 2})  # walk; before start
        self.assertEqual(again["known"], 2)
        self.assertEqual(fake.calls[0][2], "access-1")
        status = strava.status(OWNER_ID)
        self.assertEqual(status["last_result"], again)
        self.assertIsNone(status["last_error"])

    def test_two_recordings_of_one_ride_become_one_entry(self):
        write_settings()
        write_tokens()
        watch = strava_activity(70, TUE, "Ride", name="Morning Ride", distance=0, elapsed_time=3700,
                                start_date=f"{TUE}T10:58:00Z")
        peloton = strava_activity(71, TUE, "VirtualRide", distance=30545, kilojoules=640.2, elapsed_time=3600)
        with mock.patch.object(strava, "_request", FakeStrava([watch, peloton])):
            counts = strava.sync(owner_tenant())
        self.assertEqual((counts["created"], counts["duplicates"]), (1, 1))
        with owner_session() as s:
            rides = [a for a in repo.activity_rows(s) if a["activity_date"] == TUE.isoformat()]
        self.assertEqual(len(rides), 1)
        self.assertEqual(rides[0]["actual_session"], "VirtualRide 71")  # the copy with distance and output
        self.assertEqual(rides[0]["plan_id"], "W2-Tue-Bike")

    def test_second_recording_arriving_later_fills_in_the_entry(self):
        write_settings()
        write_tokens()
        watch = strava_activity(72, TUE, "Ride", name="Morning Ride", distance=0, elapsed_time=3700)
        peloton = strava_activity(73, TUE, "VirtualRide", distance=30545, kilojoules=640.2, elapsed_time=3600)
        with mock.patch.object(strava, "_request", FakeStrava([watch])):
            strava.sync(owner_tenant())
        with mock.patch.object(strava, "_request", FakeStrava([watch, peloton])):
            counts = strava.sync(owner_tenant())
        self.assertEqual((counts["known"], counts["duplicates"], counts["created"]), (1, 1, 0))
        with owner_session() as s:
            (ride,) = [a for a in repo.activity_rows(s) if a["activity_date"] == TUE.isoformat()]
        self.assertEqual((ride["actual_session"], ride["distance_mi"], ride["output_kj"]), ("Morning Ride", 18.98, 640.2))

    def test_stretches_and_warm_ups_are_logged_as_stretch_and_not_linked(self):
        write_settings()
        write_tokens()
        warm_up = strava_activity(76, WED, "Workout", name="5 min Pre-Run Warm Up", elapsed_time=300,
                                  moving_time=300, start_date=f"{WED}T10:50:00Z")
        upper = strava_activity(77, WED, "WeightTraining", name="30 min Upper Body", start_date=f"{WED}T11:00:00Z")
        with mock.patch.object(strava, "_request", FakeStrava([warm_up, upper])):
            strava.sync(owner_tenant())
        with owner_session() as s:
            links = {a["actual_session"]: a["plan_id"] for a in repo.activity_rows(s)
                     if a["activity_date"] == WED.isoformat()}
        self.assertEqual(links, {"5 min Pre-Run Warm Up": None, "30 min Upper Body": "W2-Wed-Upper"})
        with owner_session() as s:
            categories = {a["actual_session"]: a["category"] for a in repo.activity_rows(s)
                          if a["activity_date"] == WED.isoformat()}
        self.assertEqual(categories["5 min Pre-Run Warm Up"], "Stretch")

    def test_back_to_back_workouts_are_both_kept(self):
        write_settings()
        write_tokens()
        first = strava_activity(74, THU, elapsed_time=2700, start_date=f"{THU}T11:00:00Z")
        second = strava_activity(75, THU, elapsed_time=900, start_date=f"{THU}T11:46:00Z")
        with mock.patch.object(strava, "_request", FakeStrava([first, second])):
            self.assertEqual(strava.sync(owner_tenant())["created"], 2)

    def test_expired_token_is_refreshed_first(self):
        write_settings()
        write_tokens(expires_at=int(time.time()) - 10)
        fake = FakeStrava()
        with mock.patch.object(strava, "_request", fake):
            strava.sync(owner_tenant())
        self.assertEqual(fake.calls[0][0], strava.TOKEN_URL)
        self.assertEqual(fake.calls[0][1]["refresh_token"], "refresh-1")
        self.assertEqual(fake.calls[1][2], "access-2")
        self.assertEqual(strava._load_state(OWNER_ID)["refresh_token"], "refresh-2")

    def test_pages_are_followed(self):
        write_settings()
        write_tokens()
        fake = FakeStrava([strava_activity(100 + i, THU, "Walk") for i in range(strava.PAGE_SIZE + 5)])
        with mock.patch.object(strava, "_request", fake):
            self.assertEqual(strava.sync(owner_tenant())["skipped"], strava.PAGE_SIZE + 5)
        self.assertEqual(len(fake.calls), 2)

    def test_later_syncs_start_near_the_newest_activity(self):
        write_settings()
        write_tokens(newest_start=int(time.time()))
        fake = FakeStrava()
        with mock.patch.object(strava, "_request", fake):
            strava.sync(owner_tenant())
        after = int(parse_qs(urlparse(fake.calls[0][0]).query)["after"][0])
        self.assertAlmostEqual(after, time.time() - strava.OVERLAP.total_seconds(), delta=60)

    def test_failure_is_saved_for_the_page(self):
        write_settings()
        write_tokens()
        with mock.patch.object(strava, "_request", FakeStrava(fail="Strava is down")):
            with self.assertRaises(strava.StravaError):
                strava.sync(owner_tenant())
        self.assertEqual(strava.status(OWNER_ID)["last_error"], "Strava is down")

    def test_sync_without_connection_is_refused(self):
        self.assertEqual(signed_in_without_strava().post("/api/strava/sync").status_code, 409)


class ConnectTests(WeekTwoCleanup):
    def test_routes_need_sign_in(self):
        from fastapi.testclient import TestClient

        from backend.app.main import app
        self.assertEqual(TestClient(app).get("/api/strava/status").status_code, 401)

    def test_status_before_setup(self):
        status = signed_in_without_strava().get("/api/strava/status").json()
        self.assertEqual((status["configured"], status["connected"]), (False, False))

    def test_connect_then_callback_saves_tokens_without_syncing(self):
        write_settings()
        client = signed_in_client()
        response = client.get("/api/strava/connect", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        target = urlparse(response.headers["location"])
        query = {k: v[0] for k, v in parse_qs(target.query).items()}
        self.assertEqual(target.netloc, "www.strava.com")
        self.assertEqual((query["client_id"], query["scope"]), ("12345", "activity:read_all"))
        self.assertEqual(query["redirect_uri"], "http://testserver/api/strava/callback")

        fake = FakeStrava([strava_activity(50, THU)])
        with mock.patch.object(strava, "_request", fake):
            response = client.get("/api/strava/callback", follow_redirects=False,
                                  params={"state": query["state"], "code": "abc", "scope": "read,activity:read_all"})
        self.assertEqual(response.headers["location"], "/#/log")
        status = client.get("/api/strava/status").json()
        self.assertTrue(status["connected"])
        self.assertEqual(status["athlete"], "Test Runner")
        self.assertIsNone(status["last_sync"])  # syncing is manual
        self.assertEqual([url for url, _, _ in fake.calls], [strava.TOKEN_URL])
        self.assertEqual(fake.calls[0][1]["code"], "abc")

    def test_callback_with_unknown_state_is_refused(self):
        write_settings()
        client = signed_in_without_strava()
        with mock.patch.object(strava, "_request", FakeStrava()) as fake:
            client.get("/api/strava/callback", params={"state": "made-up", "code": "abc",
                                                                   "scope": "activity:read_all"})
        self.assertEqual(fake.calls, [])
        self.assertFalse(strava.is_connected(OWNER_ID))
        self.assertIn("Connect again", strava.status(OWNER_ID)["last_error"])

    def test_missing_activity_permission_is_explained(self):
        write_settings()
        client = signed_in_without_strava()
        state = parse_qs(urlparse(client.get("/api/strava/connect", follow_redirects=False)
                                  .headers["location"]).query)["state"][0]
        client.get("/api/strava/callback", params={"state": state, "code": "abc", "scope": "read"})
        self.assertFalse(strava.is_connected(OWNER_ID))
        self.assertIn("View data about your activities", strava.status(OWNER_ID)["last_error"])

    def test_disconnect_forgets_tokens_but_keeps_imports(self):
        write_settings()
        write_tokens()
        with mock.patch.object(strava, "_request", FakeStrava([strava_activity(60, THU)])):
            strava.sync(owner_tenant())
            status = signed_in_client().post("/api/strava/disconnect").json()
        self.assertFalse(status["connected"])
        with owner_session() as s:
            self.assertEqual(s.scalar(select(ActivityLog.category).where(ActivityLog.activity_date == THU)), "Run")


if __name__ == "__main__":
    unittest.main()
