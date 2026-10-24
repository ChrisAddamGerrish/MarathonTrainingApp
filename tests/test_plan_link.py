"""Tests for linking an activity to a planned session, in its own plan week or another one.

The activity dialog only offers the activity's own week unless you untick "Only this week's
sessions"; the backend accepts a link to any week.

Run from the project root:   .venv\Scripts\python.exe -m unittest tests.test_plan_link -v
"""
import unittest
from pathlib import Path

from tests import OWNER_PLAN_START, TEST_DB, owner_session, signed_in_client

from sqlalchemy import select

from backend.app.core import config
from backend.app.core.database import engine, init_db
from backend.app.core.errors import UnprocessableError
from backend.app import repository as repo
from backend.app.models import ActivityLog, TrainingPlan
from backend.app.services.planning import plan_date

WEEK = 5
SATURDAY = plan_date(WEEK, "Sat", OWNER_PLAN_START)


def setUpModule():
    if Path(config.DB_PATH) != TEST_DB:
        raise RuntimeError(f"Tests must use the temp database, but the app is using {config.DB_PATH}")
    init_db()


def tearDownModule():
    engine.dispose()


def values(**overrides):
    return {"activity_date": SATURDAY, "category": "Run", "actual_session": "Saturday run", "distance_mi": 5.0,
            "duration_min": 45.0, "output_kj": None, "plan_id": None, "notes": None, **overrides}


def plan_ids(week: int) -> list[str]:
    with owner_session() as s:
        return list(s.scalars(select(TrainingPlan.plan_id).where(TrainingPlan.week == week)
                              .order_by(TrainingPlan.entry_order)))


class PlanLinkTests(unittest.TestCase):
    def setUp(self):
        self.created: list[int] = []
        self.this_week = plan_ids(WEEK)
        self.other_week = plan_ids(WEEK + 1)
        self.assertTrue(self.this_week and self.other_week, "fixture plan has no sessions in weeks 5-6")

    def tearDown(self):
        with owner_session() as s:
            for activity_id in self.created:
                if s.get(ActivityLog, activity_id) is not None:
                    repo.delete_activity(s, activity_id)

    def create(self, **overrides):
        with owner_session() as s:
            created = repo.create_activity(s, values(**overrides))
        self.created.append(created["activity_id"])
        return created

    def test_any_session_of_the_same_week_can_be_linked(self):
        for plan_id in self.this_week:
            with self.subTest(plan_id=plan_id):
                self.assertEqual(self.create(plan_id=plan_id)["plan_id"], plan_id)

    def test_a_session_of_another_week_can_be_linked(self):
        created = self.create(plan_id=self.other_week[-1])
        self.assertEqual(created["plan_id"], self.other_week[-1])
        self.assertEqual(created["week"], WEEK)  # the activity stays in the week it was done

    def test_relinking_to_another_week_is_saved(self):
        created = self.create(plan_id=self.this_week[0])
        with owner_session() as s:
            updated = repo.update_activity(s, created["activity_id"], values(plan_id=self.other_week[0]))
        self.assertEqual(updated["plan_id"], self.other_week[0])

    def test_an_unknown_session_is_still_rejected(self):
        with owner_session() as s, self.assertRaises(UnprocessableError):
            repo.create_activity(s, values(plan_id="W99-Nope"))

    def test_the_api_saves_a_cross_week_link(self):
        response = signed_in_client().post("/api/activities", json={
            **values(plan_id=self.other_week[0]), "activity_date": SATURDAY.isoformat()})
        self.assertEqual(response.status_code, 201, response.text)
        self.created.append(response.json()["activity_id"])
        self.assertEqual(response.json()["plan_id"], self.other_week[0])


if __name__ == "__main__":
    unittest.main()
