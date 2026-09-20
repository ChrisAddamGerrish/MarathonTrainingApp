"""Test support. Run from the project root:   .venv\\Scripts\\python.exe -m unittest discover -v

Every test module runs against a temporary COPY of marathon.db and writes its logs to a temporary
folder, never the real ones. The environment has to be pointed at them before any backend module is
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

shutil.copy(ROOT / "marathon.db", TEST_DB)
os.environ["MARATHON_DB"] = str(TEST_DB)
os.environ["MARATHON_LOG_DIR"] = str(LOG_DIR)
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
