"""Test support. Run from the project root:   .venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v

(-s tests matters: a plain `discover` walks backend/ first, importing it before this package can
point it at the temp copies, and the modules' setUpModule guards then refuse to run.)

Every test module runs against a temporary COPY of tests/fixtures/single-user.db, writes its logs
to a temporary folder, signs in with a temporary login (TEST_USER / TEST_PASSWORD) and keeps Strava
settings and tokens in temporary files, never the real ones. The copy is migrated to multiple users like the real
database would be, with the test login as the old single-user login: TEST_USER becomes user 1 (an
admin) and owns the copied data. Its Strava connection is faked (connect_owner_strava) because the
app requires one. The environment has to be pointed at them before any backend module is
imported (the database engine is created at import time), which is why this lives here: importing
any tests.* module imports this package first.
"""
import atexit
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="marathon-test-"))
TEST_DB = TMP / "marathon.db"
LOG_DIR = TMP / "logs"
AUTH_FILE = TMP / "auth.env"
TEST_USER = "tester"
TEST_PASSWORD = "correct horse battery"

# A single-user database from before accounts existed (the last marathon.db in git), so every run
# also exercises the migration. The real marathon.db is never read.
FIXTURE_DB = ROOT / "tests" / "fixtures" / "single-user.db"
shutil.copy(FIXTURE_DB, TEST_DB)
os.environ["MARATHON_DB"] = str(TEST_DB)
os.environ["MARATHON_LOG_DIR"] = str(LOG_DIR)
os.environ["MARATHON_AUTH_FILE"] = str(AUTH_FILE)
os.environ["MARATHON_STRAVA_CONFIG"] = str(TMP / "strava.env")
os.environ["MARATHON_STRAVA_TOKENS"] = str(TMP / "strava_tokens.json")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

OWNER_ID = 1
OWNER_PLAN_START = date(2026, 9, 14)


def _write_legacy_login():
    from backend.app.core import auth

    # The old single-user format: the migration turns it into user 1.
    lines = [f"MARATHON_USER={TEST_USER}", f"MARATHON_PASSWORD_HASH={auth.hash_password(TEST_PASSWORD)}",
             "MARATHON_SECRET_KEY=test-secret"]
    AUTH_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


_write_legacy_login()


def owner_tenant():
    from backend.app.core.database import Tenant

    return Tenant(OWNER_ID, OWNER_PLAN_START)


def owner_session():
    """A database session over the test owner's data (what a signed-in request gets)."""
    from backend.app.core.database import user_session

    return user_session(owner_tenant())


def connect_owner_strava():
    """Make user 1 count as connected to Strava (fake tokens), unless they already are."""
    from backend.app.services import strava

    if not strava.is_connected(OWNER_ID):
        strava._save_state(OWNER_ID, {"access_token": "test", "refresh_token": "test", "expires_at": 9999999999,
                                      "athlete_id": 1, "athlete_name": "Test Owner", "sync_start": "2026-09-14",
                                      "newest_start": None, "last_sync": None, "last_result": None,
                                      "last_error": None})


def signed_in_client():
    """A TestClient for the web app holding a valid session cookie for the test login."""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    connect_owner_strava()
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    response.raise_for_status()
    return client
