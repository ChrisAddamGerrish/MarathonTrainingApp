"""Test support. Run from the project root:   .venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v

(-s tests matters: a plain `discover` walks backend/ first, importing it before this package can
point it at the temp copies, and the modules' setUpModule guards then refuse to run.)

Every test module runs against a temporary COPY of marathon.db, writes its logs to a temporary
folder and signs in with a temporary login (TEST_USER / TEST_PASSWORD), never the real ones. The environment has to be pointed at them before any backend module is
imported (the database engine is created at import time), which is why this lives here: importing
any tests.* module imports this package first.
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="marathon-test-"))
TEST_DB = TMP / "marathon.db"
LOG_DIR = TMP / "logs"
AUTH_FILE = TMP / "auth.env"
TEST_USER = "tester"
TEST_PASSWORD = "correct horse battery"

shutil.copy(ROOT / "marathon.db", TEST_DB)
os.environ["MARATHON_DB"] = str(TEST_DB)
os.environ["MARATHON_LOG_DIR"] = str(LOG_DIR)
os.environ["MARATHON_AUTH_FILE"] = str(AUTH_FILE)
atexit.register(shutil.rmtree, TMP, ignore_errors=True)


def signed_in_client():
    """A TestClient for the web app holding a valid session cookie for the test login."""
    from fastapi.testclient import TestClient

    from backend.app.core import auth
    from backend.app.main import app

    if not AUTH_FILE.exists():
        auth.write_credentials(TEST_USER, TEST_PASSWORD)
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    response.raise_for_status()
    return client
