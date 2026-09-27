"""Tests for signing in to the web app: passwords, session cookies and which routes need a session.

Run from the project root:   .venv\Scripts\python.exe -m unittest tests.test_auth -v
"""
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import AUTH_FILE, OWNER_ID, OWNER_PLAN_START, TEST_DB, TEST_PASSWORD, TEST_USER, signed_in_client

from fastapi.testclient import TestClient

from backend.app.api.routes import auth as auth_routes
from backend.app.core import auth
from backend.app.core.database import SessionLocal, init_db
from backend.app.main import app
from backend.app.models import User
from backend.app.services import accounts


def setUpModule():
    if Path(auth.config.AUTH_FILE) != AUTH_FILE or Path(auth.config.DB_PATH) != TEST_DB:
        raise RuntimeError(f"Tests must use the temp login and database, but the app is using "
                           f"{auth.config.AUTH_FILE} and {auth.config.DB_PATH}")
    init_db()


def user_of(token):
    """The user id a session cookie is good for right now (None if it isn't)."""
    with SessionLocal() as s:
        return auth.read_session(token, lambda uid: (u := s.get(User, uid)) and u.password_hash)


def login(client, username=TEST_USER, password=TEST_PASSWORD, remember=False):
    return client.post("/api/auth/login", json={"username": username, "password": password, "remember": remember})


class PasswordTests(unittest.TestCase):
    def test_hash_round_trip(self):
        stored = auth.hash_password("a long password")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(auth.verify_password("a long password", stored))
        self.assertFalse(auth.verify_password("a long password ", stored))

    def test_same_password_hashes_differently(self):
        self.assertNotEqual(auth.hash_password("same"), auth.hash_password("same"))

    def test_malformed_hash_is_rejected(self):
        self.assertFalse(auth.verify_password("x", "not-a-hash"))
        self.assertFalse(auth.verify_password("x", "bcrypt$1$2$3$4$5"))


class RouteProtectionTests(unittest.TestCase):
    def setUp(self):
        auth_routes._failures.clear()

    def test_data_routes_need_a_session(self):
        client = TestClient(app)
        for method, path in [("get", "/api/data"), ("get", "/api/history"),
                             ("post", "/api/activities"), ("delete", "/api/activities/1")]:
            with self.subTest(path=path):
                self.assertEqual(client.request(method.upper(), path).status_code, 401)

    def test_health_and_session_status_are_open(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/health").json()["app"], "marathon")
        self.assertEqual(client.get("/api/auth/session").json(),
                         {"authenticated": False, "user": None, "configured": True})

    def test_sign_in_then_out(self):
        client = TestClient(app)
        response = login(client)
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertEqual(client.get("/api/auth/session").json()["user"], TEST_USER)
        self.assertEqual(client.get("/api/data").status_code, 200)

        client.post("/api/auth/logout")
        self.assertEqual(client.get("/api/data").status_code, 401)

    def test_wrong_password_or_username(self):
        client = TestClient(app)
        self.assertEqual(login(client, password="wrong password").status_code, 401)
        self.assertEqual(login(client, username="someone").status_code, 401)
        self.assertEqual(client.get("/api/data").status_code, 401)

    def test_repeated_failures_are_locked_out(self):
        client = TestClient(app)
        for _ in range(auth_routes.MAX_FAILURES):
            self.assertEqual(login(client, password="wrong password").status_code, 401)
        self.assertEqual(login(client).status_code, 429)  # even the right password, for a while

    def test_forged_or_expired_cookie_is_refused(self):
        client = signed_in_client()
        token = client.cookies[auth.COOKIE]
        payload, signature = token.rsplit(".", 1)
        forged = auth._b64(b'{"uid": 1, "exp": 9999999999}')
        self.assertIsNone(user_of(f"{forged}.{signature}"))
        self.assertIsNone(user_of(f"{payload}.{signature[:-2]}AA"))
        self.assertIsNone(user_of("not-a-token"))
        with mock.patch.object(time, "time", return_value=time.time() + auth.SESSION_SECONDS + 1):
            self.assertIsNone(user_of(token))

    def test_remember_me_keeps_the_session_for_90_days(self):
        client = TestClient(app)
        cookie = login(client, remember=True).headers["set-cookie"].lower()
        self.assertIn(f"max-age={90 * 24 * 3600}", cookie)
        token = client.cookies[auth.COOKIE]
        with mock.patch.object(time, "time", return_value=time.time() + 89 * 24 * 3600):
            self.assertEqual(user_of(token), OWNER_ID)
        with mock.patch.object(time, "time", return_value=time.time() + 91 * 24 * 3600):
            self.assertIsNone(user_of(token))

    def test_without_remember_me_the_cookie_ends_with_the_browser(self):
        client = TestClient(app)
        cookie = login(client).headers["set-cookie"].lower()
        self.assertNotIn("max-age", cookie)
        self.assertNotIn("expires", cookie)

    def test_changing_the_password_signs_everyone_out(self):
        client = signed_in_client()
        self.assertEqual(client.get("/api/data").status_code, 200)
        try:
            accounts.set_password(TEST_USER, "a brand new password", OWNER_PLAN_START)
            self.assertEqual(client.get("/api/data").status_code, 401)
            self.assertEqual(login(client, password="a brand new password").status_code, 200)
        finally:
            accounts.set_password(TEST_USER, TEST_PASSWORD, OWNER_PLAN_START)

    def test_usernames_are_not_case_sensitive(self):
        self.assertEqual(login(TestClient(app), username=TEST_USER.upper()).status_code, 200)

    def test_no_account_with_a_password_means_no_sign_in(self):
        with mock.patch.object(accounts, "any_can_sign_in", return_value=False):
            client = TestClient(app)
            self.assertFalse(client.get("/api/auth/session").json()["configured"])
            self.assertEqual(login(client).status_code, 503)


if __name__ == "__main__":
    unittest.main()
