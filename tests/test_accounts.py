r"""Tests for accounts: registering with invites, the Strava step, and keeping each user's data apart.

Run from the project root:   .venv\Scripts\python.exe -m unittest tests.test_accounts -v
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tests import FIXTURE_DB, OWNER_ID, ROOT, TEST_DB, TMP, signed_in_client

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.api.routes import auth as auth_routes
from backend.app.core import config
from backend.app.core.database import SessionLocal, engine, init_db
from backend.app.core.migrations import migrate_to_multi_user
from backend.app.main import app
from backend.app.models import Invite, User
from backend.app.services import accounts, strava

START = "2026-10-05"  # a Monday
_counter = iter(range(1000))


def setUpModule():
    if Path(config.DB_PATH) != TEST_DB:
        raise RuntimeError(f"Tests must use the temp database, but the app is using {config.DB_PATH}")
    init_db()


def tearDownModule():
    engine.dispose()


def invite(admin_client):
    response = admin_client.post("/api/auth/invites")
    assert response.status_code == 201, response.text
    return response.json()["code"]


def register(client, code, username=None, password="a long enough password", plan_start=START, weeks=12):
    return client.post("/api/auth/register", json={
        "invite": code, "username": username or f"runner{next(_counter)}", "password": password,
        "plan_start": plan_start, "weeks": weeks})


def user_id(username):
    with SessionLocal() as s:
        return s.scalar(select(User.user_id).where(User.username == username))


def connect_strava(uid, athlete_id=None):
    strava._save_state(uid, {"access_token": "t", "refresh_token": "t", "expires_at": 9999999999,
                             "athlete_id": athlete_id or 1000 + uid, "athlete_name": f"Athlete {uid}",
                             "sync_start": START, "newest_start": None, "last_sync": None, "last_result": None,
                             "last_error": None})


def new_user(admin_client, **kwargs):
    """A registered, Strava-connected second user and a client signed in as them."""
    client = TestClient(app)
    name = f"runner{next(_counter)}"
    response = register(client, invite(admin_client), username=name, **kwargs)
    assert response.status_code == 201, response.text
    connect_strava(user_id(name))
    return client, user_id(name)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        auth_routes._failures.clear()
        self.admin = signed_in_client()

    def test_register_signs_in_and_asks_for_strava(self):
        client = TestClient(app)
        response = register(client, invite(self.admin), username="newbie")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json(), {"authenticated": True, "user": "newbie", "configured": True,
                                           "is_admin": False, "strava_connected": False})
        self.assertEqual(client.get("/api/data").status_code, 403)  # not until Strava is connected
        self.assertEqual(client.get("/api/strava/status").status_code, 200)
        self.assertEqual(client.get("/api/strava/connect", follow_redirects=False).status_code, 409)  # not set up here

        connect_strava(user_id("newbie"))
        data = client.get("/api/data").json()
        self.assertEqual((len(data["weeks"]), data["plan"], data["activities"]), (12, [], []))
        self.assertEqual(data["summary"]["plan_start"], START)
        self.assertEqual(data["summary"]["race_date"], "2026-12-27")  # Sunday of week 12

    def test_an_empty_plan_can_be_filled_in(self):
        client, _ = new_user(self.admin)
        response = client.post("/api/plan/weeks/1/sessions", json={
            "day": "Mon", "category": "Run", "planned_session": "Easy 3", "target_distance_mi": 3})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["plan_id"], "W1-Mon")  # the owner has a W1-Mon too; ids are per user
        self.assertEqual(client.put("/api/plan/weeks/2", json={"week_type": "Peak"}).status_code, 200)
        self.assertEqual([w["week_type"] for w in client.get("/api/data").json()["weeks"][:2]], ["Normal", "Peak"])

    def test_invites_are_single_use(self):
        code = invite(self.admin)
        self.assertEqual(register(TestClient(app), code).status_code, 201)
        response = register(TestClient(app), code)
        self.assertEqual(response.status_code, 422)
        self.assertIn("invite code isn't valid", response.json()["detail"])

    def test_expired_or_made_up_invites_are_refused(self):
        code = invite(self.admin)
        with SessionLocal() as s:
            s.get(Invite, accounts._code_hash(code)).expires_at = "2000-01-01T00:00:00Z"
            s.commit()
        self.assertEqual(register(TestClient(app), code).status_code, 422)
        self.assertEqual(register(TestClient(app), "made-up-code").status_code, 422)

    def test_bad_details_are_refused_and_keep_the_invite(self):
        code = invite(self.admin)
        for kwargs, status in [({"username": "tester"}, 409), ({"username": "TESTER"}, 409),
                               ({"username": "no spaces"}, 422), ({"password": "short"}, 422),
                               ({"plan_start": "2026-10-06"}, 422), ({"weeks": 0}, 422), ({"weeks": 53}, 422)]:
            auth_routes._failures.clear()
            with self.subTest(**kwargs):
                self.assertEqual(register(TestClient(app), code, **kwargs).status_code, status)
        self.assertEqual(register(TestClient(app), code).status_code, 201)

    def test_guessing_invite_codes_is_slowed_down(self):
        client = TestClient(app)
        for _ in range(auth_routes.MAX_FAILURES):
            register(client, "guess")
        self.assertEqual(register(client, invite(self.admin)).status_code, 429)

    def test_only_admins_create_invites(self):
        client, _ = new_user(self.admin)
        self.assertEqual(client.post("/api/auth/invites").status_code, 403)
        self.assertEqual(TestClient(app).post("/api/auth/invites").status_code, 401)

    def test_new_user_can_sign_in(self):
        name = f"runner{next(_counter)}"
        register(TestClient(app), invite(self.admin), username=name)
        client = TestClient(app)
        response = client.post("/api/auth/login", json={"username": name, "password": "a long enough password"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["strava_connected"])


class StravaStepTests(unittest.TestCase):
    def setUp(self):
        auth_routes._failures.clear()
        self.admin = signed_in_client()

    def test_disconnecting_means_connecting_again_before_using_the_app(self):
        client, uid = new_user(self.admin)
        with mock.patch.object(strava, "_request", return_value=None):
            client.post("/api/strava/disconnect")
        self.assertEqual(client.get("/api/data").status_code, 403)
        self.assertFalse(client.get("/api/auth/session").json()["strava_connected"])

    def test_a_strava_account_connects_to_one_user_only(self):
        _, uid = new_user(self.admin)
        other_client, other = new_user(self.admin)
        strava._save_state(other, None)
        taken = strava._load_state(uid)["athlete_id"]
        config.STRAVA_CONFIG_FILE.write_text("STRAVA_CLIENT_ID=1\nSTRAVA_CLIENT_SECRET=s\n", encoding="utf-8")
        try:
            tokens = {"access_token": "a", "refresh_token": "r", "expires_at": 9999999999,
                      "athlete": {"id": taken, "firstname": "Same", "lastname": "Person"}}
            with mock.patch.object(strava, "_request", return_value=tokens):
                with self.assertRaises(Exception):
                    strava.connect(other, "code", "activity:read_all", date(2026, 10, 5))
            self.assertFalse(strava.is_connected(other))
            self.assertIn("already connected to another user", strava.status(other)["last_error"])
        finally:
            config.STRAVA_CONFIG_FILE.unlink(missing_ok=True)

    def test_the_callback_only_completes_for_the_user_who_started_it(self):
        _, uid = new_user(self.admin)
        other_client, other = new_user(self.admin)
        from backend.app.api.routes import strava as strava_routes
        strava_routes._pending["started-by-uid"] = (9999999999, uid)
        with mock.patch.object(strava, "_request") as fake:
            other_client.get("/api/strava/callback", params={"state": "started-by-uid", "code": "c",
                                                              "scope": "activity:read_all"})
        fake.assert_not_called()


class IsolationTests(unittest.TestCase):
    """A second user sees and changes only their own data, whatever ids they try."""

    @classmethod
    def setUpClass(cls):
        auth_routes._failures.clear()
        cls.owner = signed_in_client()
        cls.owner_data = cls.owner.get("/api/data").json()
        cls.client, cls.uid = new_user(cls.owner)

    def test_the_data_route_shows_only_their_own(self):
        data = self.client.get("/api/data").json()
        self.assertEqual(data["plan"], [])
        self.assertFalse({a["activity_id"] for a in data["activities"]}
                         & {a["activity_id"] for a in self.owner_data["activities"]})
        owners = {e["history_id"] for e in self.owner.get("/api/history?limit=1000").json()}
        self.assertFalse(owners & {e["history_id"] for e in self.client.get("/api/history?limit=1000").json()})

    def test_someone_elses_ids_are_not_found(self):
        a = self.owner_data["activities"][0]
        body = {k: a[k] for k in ("activity_date", "category", "actual_session", "distance_mi", "duration_min",
                                  "output_kj", "notes")}
        plan_id = self.owner_data["plan"][0]["plan_id"]
        history_id = self.owner.get("/api/history?limit=1").json()[0]["history_id"]
        for method, path, json in [
            ("PUT", f"/api/activities/{a['activity_id']}", body),
            ("DELETE", f"/api/activities/{a['activity_id']}", None),
            ("POST", f"/api/history/{history_id}/revert", None),
            ("PUT", f"/api/plan/{plan_id}/skip", {}),
            ("DELETE", f"/api/plan/{plan_id}", None),
            ("PUT", f"/api/plan/{plan_id}", {"day": "Mon", "category": "Rest", "planned_session": "x"}),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path, json=json).status_code, 404)
        self.assertEqual(self.client.post("/api/activities", json={**body, "plan_id": plan_id}).status_code, 422)
        self.assertEqual(self.owner.get("/api/data").json(), self.owner_data)

    def test_restores_only_touch_their_own_data(self):
        client, _ = new_user(self.owner)  # its own user: this leaves a plan behind
        rows = "Plan ID,Week,Day,Week type,Category,Session\r\nW1-Tue,1,Tue,Normal,Run,Easy 4\r\n"
        result = client.post("/api/restore/plan?apply=true", content=rows,
                             headers={"Content-Type": "text/csv"}).json()
        self.assertEqual((result["added"], result["removed"]), (1, 0))
        # A workouts backup naming the owner's activity ids gets new ids instead of taking theirs.
        a = self.owner_data["activities"][0]
        csv = f"Activity ID,Date,Category,Session\r\n{a['activity_id']},{a['activity_date']},Run,Copied\r\n"
        result = client.post("/api/restore/workouts?apply=true", content=csv,
                             headers={"Content-Type": "text/csv"}).json()
        self.assertEqual((result["added"], result["removed"]), (1, 0))
        mine = client.get("/api/data").json()["activities"]
        self.assertEqual(len(mine), 1)
        self.assertNotEqual(mine[0]["activity_id"], a["activity_id"])
        self.assertEqual(self.owner.get("/api/data").json(), self.owner_data)

    def test_strava_details_are_theirs_only(self):
        from tests import owner_session
        from backend.app import repository as repo

        a = self.owner_data["activities"][0]
        with owner_session() as s:
            repo.save_metrics(s, a["activity_id"], {"strava_id": 1, "avg_hr": 150, "description": "private"})
            repo.save_gear(s, [{"gear_id": "g9", "kind": "shoe", "name": "Mine", "distance_mi": 10.0,
                                "is_primary": True, "retired": False}])
        try:
            self.assertEqual(self.owner.get(f"/api/activities/{a['activity_id']}/strava").json()["description"],
                             "private")
            self.assertEqual(self.client.get(f"/api/activities/{a['activity_id']}/strava").status_code, 404)
            self.assertEqual(self.client.put("/api/strava/gear/g9", json={"replace_at_mi": 1}).status_code, 404)
            self.assertEqual(self.client.get("/api/data").json()["gear"], [])
        finally:
            with owner_session() as s:
                from sqlalchemy import delete
                from backend.app.models import ActivityMetrics, Gear
                s.execute(delete(ActivityMetrics))
                s.execute(delete(Gear))
                s.commit()

    def test_new_workouts_and_their_history_are_theirs(self):
        created = self.client.post("/api/activities", json={"activity_date": "2026-10-06", "category": "Run",
                                                            "actual_session": "Mine"}).json()
        self.assertEqual(created["week"], 1)  # their plan starts 2026-10-05
        self.assertEqual([e["activity_id"] for e in self.client.get("/api/history").json()][:1],
                         [created["activity_id"]])
        self.assertNotIn(created["activity_id"], [e["activity_id"] for e in self.owner.get("/api/history").json()])
        self.client.delete(f"/api/activities/{created['activity_id']}")


class SchemaTests(unittest.TestCase):
    def test_the_single_user_database_is_migrated_whole(self):
        copy = TMP / "migrate-me.db"
        shutil.copy(FIXTURE_DB, copy)
        before = sqlite3.connect(copy)
        tables = [t for (t,) in before.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                  if t in ("training_plan", "activity_log", "activity_history", "plan_skips", "strava_imports")]
        counts = {t: before.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        before.close()
        self.assertTrue(migrate_to_multi_user(copy))
        after = sqlite3.connect(copy)
        for t in tables:
            self.assertEqual(after.execute(f"SELECT COUNT(*), MIN(user_id), MAX(user_id) FROM {t}").fetchone(),
                             (counts[t], 1 if counts[t] else None, 1 if counts[t] else None), t)
        self.assertEqual(after.execute("SELECT user_id, is_admin FROM users").fetchall(), [(1, 1)])
        self.assertEqual(after.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertFalse(migrate_to_multi_user(copy))  # only once
        after.close()

    def test_a_new_database_gets_the_whole_schema(self):
        db = TMP / "fresh.db"
        env = {**os.environ, "MARATHON_DB": str(db)}
        subprocess.run([sys.executable, "-c", "from backend.app.core.database import init_db; init_db()"],
                       cwd=ROOT, env=env, check=True, capture_output=True)
        con = sqlite3.connect(db)
        names = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
        con.close()
        self.assertLessEqual({"users", "invites", "plan_weeks", "training_plan", "activity_log", "activity_history",
                              "plan_skips", "strava_imports", "plan_vs_actual"}, names)

    def test_the_owner_is_user_one(self):
        with SessionLocal() as s:
            owner = s.get(User, OWNER_ID)
        self.assertTrue(owner.is_admin)
        self.assertEqual(owner.plan_start, date(2026, 9, 14))


if __name__ == "__main__":
    unittest.main()
