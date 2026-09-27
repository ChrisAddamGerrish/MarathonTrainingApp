"""Tests for editing the training plan: adding, changing, moving and deleting sessions, week types.

Run from the project root:   .venv\Scripts\python.exe -m unittest tests.test_plan_edit -v
"""
import unittest
from pathlib import Path

from tests import TEST_DB, signed_in_client

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.app.core import config
from backend.app.core.database import SessionLocal, engine, init_db
from backend.app.main import app
from backend.app.models import ActivityLog, PlanWeek, TrainingPlan

WEEK = 19  # far enough ahead that nothing is logged in it


def setUpModule():
    if Path(config.DB_PATH) != TEST_DB:
        raise RuntimeError(f"Tests must use the temp database, but the app is using {config.DB_PATH}")
    init_db()


def tearDownModule():
    engine.dispose()


def session_body(**overrides):
    return {"day": "Wed", "category": "Run", "run_subtype": "Tempo", "planned_session": "Tempo 40",
            "target_distance_mi": 5, "target_duration_min": 40, "notes": None, **overrides}


class PlanEditTests(unittest.TestCase):
    def setUp(self):
        self.client = signed_in_client()
        with SessionLocal() as s:
            self.original_ids = set(s.scalars(select(TrainingPlan.plan_id)))
            self.week_type = s.scalar(select(PlanWeek.week_type).where(PlanWeek.user_id == 1, PlanWeek.week == WEEK))

    def tearDown(self):
        with SessionLocal() as s:
            added = set(s.scalars(select(TrainingPlan.plan_id))) - self.original_ids
            s.execute(delete(ActivityLog).where(ActivityLog.plan_id.in_(added)))
            s.execute(delete(TrainingPlan).where(TrainingPlan.plan_id.in_(added)))
            s.commit()
        self.client.put(f"/api/plan/weeks/{WEEK}", json={"week_type": self.week_type})

    def add(self, **overrides):
        response = self.client.post(f"/api/plan/weeks/{WEEK}/sessions", json=session_body(**overrides))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def plan(self):
        return {p["plan_id"]: p for p in self.client.get("/api/data").json()["plan"]}

    def test_added_session_appears_in_the_plan(self):
        row = self.add(day="Tue")
        self.assertEqual(row["plan_id"], f"W{WEEK}-Tue")  # Tuesday's existing ids are W19-Tue-Bike/-Overflow
        shown = self.plan()[row["plan_id"]]
        self.assertEqual((shown["week"], shown["day"], shown["planned_session"]), (WEEK, "Tue", "Tempo 40"))
        self.assertEqual(shown["week_type"], self.week_type)
        self.assertEqual(shown["status"], "upcoming")

    def test_ids_stay_unique_and_later_sessions_come_last_in_the_day(self):
        first, second = self.add(), self.add(planned_session="Strides")
        self.assertEqual((first["plan_id"], second["plan_id"]), (f"W{WEEK}-Wed-2", f"W{WEEK}-Wed-3"))
        wed = [p["plan_id"] for p in self.plan().values() if p["week"] == WEEK and p["day"] == "Wed"]
        self.assertLess(wed.index(first["plan_id"]), wed.index(second["plan_id"]))

    def test_edit_and_move_to_another_day_keeps_the_id(self):
        row = self.add()
        response = self.client.put(f"/api/plan/{row['plan_id']}",
                                   json=session_body(day="Sat", planned_session="Tempo 45", target_duration_min=45))
        self.assertEqual(response.status_code, 200, response.text)
        shown = self.plan()[row["plan_id"]]
        self.assertEqual((shown["day"], shown["planned_session"], shown["target_duration_min"]), ("Sat", "Tempo 45", 45))

    def test_run_type_and_rest_targets_are_cleared(self):
        bike = self.add(category="Bike", run_subtype="Tempo")
        self.assertIsNone(bike["run_subtype"])
        rest = self.client.put(f"/api/plan/{bike['plan_id']}", json=session_body(category="Rest")).json()
        self.assertEqual((rest["target_distance_mi"], rest["target_duration_min"]), (None, None))

    def test_turning_a_skipped_session_into_rest_removes_the_skip(self):
        row = self.add()
        self.client.put(f"/api/plan/{row['plan_id']}/skip", json={"reason": "travel"})
        self.assertTrue(self.plan()[row["plan_id"]]["skipped"])
        self.client.put(f"/api/plan/{row['plan_id']}", json=session_body(category="Rest"))
        self.assertFalse(self.plan()[row["plan_id"]]["skipped"])

    def test_delete(self):
        row = self.add()
        self.client.put(f"/api/plan/{row['plan_id']}/skip", json={})
        self.assertEqual(self.client.delete(f"/api/plan/{row['plan_id']}").status_code, 200)
        self.assertNotIn(row["plan_id"], self.plan())

    def test_session_with_logged_activities_cannot_be_deleted(self):
        row = self.add()
        self.client.post("/api/activities", json={"activity_date": "2027-01-20", "category": "Run",
                                                  "actual_session": "tempo", "plan_id": row["plan_id"]})
        response = self.client.delete(f"/api/plan/{row['plan_id']}")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Activity log", response.json()["detail"])

    def test_week_type(self):
        response = self.client.put(f"/api/plan/weeks/{WEEK}", json={"week_type": "Peak"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({p["week_type"] for p in self.plan().values() if p["week"] == WEEK}, {"Peak"})
        self.assertEqual(self.client.put(f"/api/plan/weeks/{WEEK}", json={"week_type": "Huge"}).status_code, 422)

    def test_bad_input_is_refused(self):
        for body in (session_body(day="Funday"), session_body(planned_session="  "),
                     session_body(target_distance_mi=-1), session_body(category="Swim")):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(f"/api/plan/weeks/{WEEK}/sessions", json=body).status_code, 422)
        self.assertEqual(self.client.post("/api/plan/weeks/99/sessions", json=session_body()).status_code, 404)
        self.assertEqual(self.client.put("/api/plan/W99-Mon", json=session_body()).status_code, 404)

    def test_editing_needs_sign_in(self):
        anonymous = TestClient(app)
        self.assertEqual(anonymous.post(f"/api/plan/weeks/{WEEK}/sessions", json=session_body()).status_code, 401)
        self.assertEqual(anonymous.delete(f"/api/plan/W{WEEK}-Mon").status_code, 401)


if __name__ == "__main__":
    unittest.main()
