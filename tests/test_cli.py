"""Tests for the command-line account tool (backend/cli.py): creating an admin and their plan start.

Run from the project root:   .venv\Scripts\python.exe -m unittest tests.test_cli -v
"""
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tests import OWNER_PLAN_START, TEST_DB, TEST_PASSWORD, TEST_USER

from sqlalchemy import delete

from backend import cli
from backend.app.core import auth, config
from backend.app.core.database import SessionLocal, init_db
from backend.app.models import PlanWeek
from backend.app.services import accounts

PASSWORD = "a long enough password"


def setUpModule():
    if Path(config.DB_PATH) != TEST_DB:
        raise RuntimeError(f"Tests must use the temp database, but the app is using {config.DB_PATH}")
    init_db()


def run_cli(*answers):
    """Run `python -m backend.cli` (set a password), typing `answers` at its prompts in order:
    username, then plan start (only asked for a new account). The password prompts get PASSWORD."""
    with mock.patch("builtins.input", side_effect=list(answers)), \
            mock.patch("getpass.getpass", return_value=PASSWORD):
        return cli.main(["cli"])


class CreateAdminTests(unittest.TestCase):
    def tearDown(self):
        with SessionLocal() as s:
            user = accounts.find_user(s, "newadmin")
            if user is not None:
                s.execute(delete(PlanWeek).where(PlanWeek.user_id == user.user_id))
                s.delete(user)
                s.commit()

    def plan_start(self, username):
        with SessionLocal() as s:
            user = accounts.find_user(s, username)
            return user and user.plan_start

    def test_new_account_gets_the_plan_start_given(self):
        self.assertEqual(run_cli("newadmin", "2026-09-14"), 0)
        self.assertEqual(self.plan_start("newadmin"), date(2026, 9, 14))

    def test_blank_answer_means_this_weeks_monday(self):
        self.assertEqual(run_cli("newadmin", ""), 0)
        today = date.today()
        self.assertEqual(self.plan_start("newadmin").weekday(), 0)
        self.assertLessEqual((today - self.plan_start("newadmin")).days, 6)

    def test_a_day_that_isnt_a_monday_is_refused(self):
        self.assertEqual(run_cli("newadmin", "2026-09-16"), 1)
        self.assertIsNone(self.plan_start("newadmin"))

    def test_something_that_isnt_a_date_is_refused(self):
        self.assertEqual(run_cli("newadmin", "next monday"), 1)
        self.assertIsNone(self.plan_start("newadmin"))

    def test_existing_account_keeps_its_plan_and_isnt_asked(self):
        self.addCleanup(accounts.set_password, TEST_USER, TEST_PASSWORD, OWNER_PLAN_START)  # other tests sign in
        # Only the username is answered: a plan-start prompt would run out of input and fail.
        self.assertEqual(run_cli(TEST_USER), 0)
        self.assertEqual(self.plan_start(TEST_USER), OWNER_PLAN_START)
        with SessionLocal() as s:
            self.assertTrue(auth.check_password(PASSWORD, accounts.find_user(s, TEST_USER).password_hash))


if __name__ == "__main__":
    unittest.main()
