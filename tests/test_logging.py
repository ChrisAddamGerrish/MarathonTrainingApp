"""Tests for project-wide logging: the shared setup, the repository's change log and the web app's.

Run from the project root:   .venv\\Scripts\\python.exe -m unittest tests.test_logging -v
"""
import logging
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest import mock

from tests import LOG_DIR, TEST_DB, TMP, signed_in_client  # noqa: F401  (importing tests points the backend at the temp copies)

from fastapi.testclient import TestClient

from backend.app.core import config, logging_config
from backend.app.core.database import SessionLocal, engine, init_db
from backend.app.main import app
from backend.app.repository import repository as repo
from backend.app.schemas.schemas import ActivityIn


def setUpModule():
    if Path(config.DB_PATH) != TEST_DB:
        raise RuntimeError(f"Tests must use the temp database, but the app is using {config.DB_PATH}")
    init_db()


def tearDownModule():
    engine.dispose()


def a_run(**overrides):
    return {"activity_date": "2026-09-20", "category": "Run", "actual_session": "log test",
            "distance_mi": 2.0, "duration_min": 20, **overrides}


def values(**overrides):
    """What the repository receives from the REST API and the MCP server: validated ActivityIn fields."""
    return ActivityIn(**a_run(**overrides)).model_dump()


def restore_logging_after(test):
    """setup_logging changes the root logger (and the app's lifespan calls it); undo that afterwards."""
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)

    def restore():
        for handler in list(root.handlers):
            if handler not in handlers:
                if isinstance(handler, RotatingFileHandler):
                    logging_config.detach(handler)
                else:
                    root.removeHandler(handler)
        root.setLevel(level)

    test.addCleanup(restore)


class SetupLoggingTests(unittest.TestCase):
    def setUp(self):
        restore_logging_after(self)

    def attach(self, filename):
        handler = logging_config.setup_logging(filename)
        self.assertIsNotNone(handler)
        self.addCleanup(logging_config.detach, handler)
        return handler

    def test_writes_to_a_file_in_the_log_folder(self):
        handler = self.attach("setup.log")  # the folder doesn't exist yet
        logging.getLogger("marathon.test").info("hello file")
        handler.flush()
        self.assertRegex((LOG_DIR / "setup.log").read_text(encoding="utf-8"),
                         r"\d{4}-\d\d-\d\d .* INFO marathon\.test: hello file")

    def test_uvicorn_loggers_reach_the_file_once(self):
        for name in ("uvicorn", "uvicorn.access"):  # as uvicorn configures them at startup
            self.enterContext(mock.patch.object(logging.getLogger(name), "propagate", False))
        handler = self.attach("uvicorn.log")
        logging.getLogger("uvicorn.error").info("from uvicorn error")
        logging.getLogger("uvicorn.access").info("from uvicorn access")
        logging.getLogger("marathon.test").info("from the app")
        handler.flush()
        text = (LOG_DIR / "uvicorn.log").read_text(encoding="utf-8")
        for line in ("from uvicorn error", "from uvicorn access", "from the app"):
            self.assertEqual(text.count(line), 1, line)

    def test_level_comes_from_config(self):
        with mock.patch.object(config, "LOG_LEVEL", "WARNING"):
            handler = self.attach("level.log")
        logging.getLogger("marathon.test").info("too quiet")
        logging.getLogger("marathon.test").warning("loud enough")
        handler.flush()
        text = (LOG_DIR / "level.log").read_text(encoding="utf-8")
        self.assertIn("loud enough", text)
        self.assertNotIn("too quiet", text)

    def test_unknown_level_falls_back_to_info_and_says_so(self):
        with mock.patch.object(config, "LOG_LEVEL", "LOUD"):
            handler = self.attach("badlevel.log")
        self.assertEqual(logging.getLogger().level, logging.INFO)
        handler.flush()
        self.assertIn("Unknown MARATHON_LOG_LEVEL 'LOUD'", (LOG_DIR / "badlevel.log").read_text(encoding="utf-8"))

    def test_an_unwritable_log_folder_only_warns(self):
        blocker = TMP / "not-a-folder"
        blocker.write_text("")
        with mock.patch.object(config, "LOG_DIR", blocker), self.assertLogs("marathon.logging", "WARNING"):
            self.assertIsNone(logging_config.setup_logging("x.log"))


class RepositoryLoggingTests(unittest.TestCase):
    def test_every_kind_of_change_is_logged(self):
        with SessionLocal() as s, self.assertLogs("marathon.repo", "INFO") as logs:
            created = repo.create_activity(s, values())
            repo.update_activity(s, created["activity_id"], values(notes="changed"))
            repo.update_activity(s, created["activity_id"], values(notes="changed"))
            repo.delete_activity(s, created["activity_id"])
            entry = repo.history_entries(s, created["activity_id"], 1)[0]
            repo.revert_history(s, entry["history_id"])  # puts the deleted activity back
            repo.delete_activity(s, created["activity_id"])
        aid = created["activity_id"]
        self.assertEqual([r.getMessage() for r in logs.records], [
            f"Activity {aid} created: Run on 2026-09-20 (plan none)",
            f"Activity {aid} updated: notes",
            f"Activity {aid} updated: nothing changed",
            f"Activity {aid} deleted",
            f"History entry {entry['history_id']} reverted (DELETE of activity {aid})",
            f"Activity {aid} deleted",
        ])

    def test_skip_and_unskip_are_logged(self):
        with SessionLocal() as s:
            target = next(p for p in repo.plan_rows(s) if p["category"] != "Rest" and not p["skipped"]
                          and not p["linked_activity_count"])
            with self.assertLogs("marathon.repo", "INFO") as logs:
                repo.skip_session(s, target["plan_id"], "sore")
                repo.unskip_session(s, target["plan_id"])
        self.assertEqual([r.getMessage() for r in logs.records], [
            f"Session {target['plan_id']} skipped (sore)",
            f"Session {target['plan_id']} unskipped",
        ])

    def test_refused_changes_are_not_logged_as_changes(self):
        with SessionLocal() as s, self.assertNoLogs("marathon.repo", "INFO"):
            with self.assertRaises(Exception):
                repo.delete_activity(s, 999999)


class WebAppLoggingTests(unittest.TestCase):
    def setUp(self):
        restore_logging_after(self)

    def test_a_refused_request_logs_why(self):
        client = signed_in_client()
        with self.assertLogs("marathon.api", "WARNING") as logs:
            response = client.put("/api/activities/999999", json=a_run())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(logs.records[0].getMessage(), "PUT /api/activities/999999 -> 404: Activity 999999 not found")

    def test_startup_sets_up_the_app_log_file(self):
        with TestClient(app):  # entering runs the lifespan
            logging.getLogger("marathon.test").info("during app run")
        text = (LOG_DIR / "app.log").read_text(encoding="utf-8")
        self.assertIn("marathon app started", text)
        self.assertIn("marathon app stopping", text)
        self.assertIn("during app run", text)


if __name__ == "__main__":
    unittest.main()
