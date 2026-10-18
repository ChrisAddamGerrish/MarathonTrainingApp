"""Tests for linking an activity to a planned session: only sessions of the activity's own plan week.

Run from the project root:   .venv\Scripts\python.exe -m unittest tests.test_plan_link -v
"""
import unittest
from datetime import timedelta
from pathlib import Path

from tests import OWNER_PLAN_START, TEST_DB, owner_session, signed_in_client

from sqlalchemy import select, update

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

    def test_a_session_of_another_week_is_rejected(self):
        with owner_session() as s, self.assertRaises(UnprocessableError) as caught:
            repo.create_activity(s, values(plan_id=self.other_week[-1]))
        self.assertIn(f"week {WEEK + 1}", caught.exception.detail)
        self.assertIn(f"week {WEEK}", caught.exception.detail)

    def test_relinking_to_another_week_is_rejected(self):
        created = self.create(plan_id=self.this_week[0])
        with owner_session() as s, self.assertRaises(UnprocessableError):
            repo.update_activity(s, created["activity_id"], values(plan_id=self.other_week[0]))
        with owner_session() as s:
            self.assertEqual(repo.get_activity(s, created["activity_id"])["plan_id"], self.this_week[0])

    def test_moving_a_linked_activity_to_another_week_is_rejected(self):
        created = self.create(plan_id=self.this_week[0])
        with owner_session() as s, self.assertRaises(UnprocessableError):
            repo.update_activity(s, created["activity_id"],
                                 values(plan_id=self.this_week[0], activity_date=SATURDAY + timedelta(weeks=1)))

    def test_an_existing_cross_week_link_can_still_be_edited(self):
        created = self.create()
        with owner_session() as s:  # a link made before the rule existed
            s.execute(update(ActivityLog).where(ActivityLog.activity_id == created["activity_id"])
                      .values(plan_id=self.other_week[0]))
            s.commit()
            updated = repo.update_activity(s, created["activity_id"],
                                           values(plan_id=self.other_week[0], notes="still editable"))
        self.assertEqual(updated["notes"], "still editable")

    def test_the_api_answers_a_cross_week_link_with_422(self):
        response = signed_in_client().post("/api/activities", json={
            **values(plan_id=self.other_week[0]), "activity_date": SATURDAY.isoformat()})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("its own week", response.text)


if __name__ == "__main__":
    unittest.main()
