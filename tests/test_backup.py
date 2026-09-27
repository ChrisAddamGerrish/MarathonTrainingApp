r"""Tests for restoring the CSV backups of workouts and the training plan.

Run from the project root:   .venv\Scripts\python.exe -m unittest tests.test_backup -v
"""
import csv
import io
import unittest
from pathlib import Path

from tests import TEST_DB, signed_in_client

from backend.app.core import config
from backend.app.core.database import engine, init_db

# Same headers as frontend/src/utils/csv.js writes (plus some of the columns the import ignores).
WORKOUT_HEADERS = {"Activity ID": "activity_id", "Date": "activity_date", "Week": "week", "Category": "category",
                   "Session": "actual_session", "Distance (mi)": "distance_mi", "Duration (min)": "duration_min",
                   "Output (kJ)": "output_kj", "Plan ID": "plan_id", "Notes": "notes"}
PLAN_HEADERS = {"Plan ID": "plan_id", "Week": "week", "Day": "day", "Date": "date", "Week type": "week_type",
                "Category": "category", "Run type": "run_subtype", "Session": "planned_session",
                "Target distance (mi)": "target_distance_mi", "Target duration (min)": "target_duration_min",
                "Notes": "notes", "Skipped": "skipped", "Skip reason": "skip_reason"}
ACTIVITY_FIELDS = ["activity_id", "activity_date", "category", "actual_session", "distance_mi", "duration_min",
                   "output_kj", "plan_id", "notes"]
PLAN_FIELDS = ["plan_id", "week", "day", "week_type", "category", "run_subtype", "planned_session",
               "target_distance_mi", "target_duration_min", "notes", "skipped", "skip_reason"]


def setUpModule():
    if Path(config.DB_PATH) != TEST_DB:
        raise RuntimeError(f"Tests must use the temp database, but the app is using {config.DB_PATH}")
    init_db()


def tearDownModule():
    engine.dispose()


def to_csv(rows, headers):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(headers)
    for r in rows:
        writer.writerow(["yes" if r[f] is True else "" if r[f] in (None, False) else r[f] for f in headers.values()])
    return "﻿" + out.getvalue()  # the app's exports start with a BOM


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.client = signed_in_client()
        data = self.client.get("/api/data").json()
        self.activities, self.plan = data["activities"], data["plan"]
        self.workouts_csv = to_csv(self.activities, WORKOUT_HEADERS)
        self.plan_csv = to_csv(self.plan, PLAN_HEADERS)

    def tearDown(self):
        # Put everything back the way the module found it, plan first (workouts link to it).
        self.restore("plan", self.plan_csv, apply=True)
        self.restore("workouts", self.workouts_csv, apply=True)

    def restore(self, kind, text, apply=False, status=200):
        response = self.client.post(f"/api/restore/{kind}" + ("?apply=true" if apply else ""),
                                    content=text.encode(), headers={"Content-Type": "text/csv"})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def data(self):
        return self.client.get("/api/data").json()

    def activity_values(self):
        return sorted(({f: a[f] for f in ACTIVITY_FIELDS} for a in self.data()["activities"]),
                      key=lambda a: a["activity_id"])

    def plan_values(self):
        return [{f: p[f] for f in PLAN_FIELDS} for p in self.data()["plan"]]

    # ---- workouts ----

    def test_restoring_an_unchanged_log_changes_nothing(self):
        result = self.restore("workouts", self.workouts_csv)
        self.assertEqual(result, {"added": 0, "changed": 0, "removed": 0, "unchanged": len(self.activities)})

    def test_workouts_restore_undoes_adds_edits_and_deletes(self):
        before = self.activity_values()
        first, second = self.activities[0], self.activities[1]
        self.client.put(f"/api/activities/{first['activity_id']}",
                        json={**{f: first[f] for f in ACTIVITY_FIELDS[1:]}, "notes": "edited"})
        self.client.delete(f"/api/activities/{second['activity_id']}")
        self.client.post("/api/activities", json={"activity_date": "2026-09-20", "category": "Row",
                                                  "actual_session": "Extra row"})

        preview = self.restore("workouts", self.workouts_csv)
        self.assertEqual((preview["added"], preview["changed"], preview["removed"]), (1, 1, 1))
        self.assertNotEqual(self.activity_values(), before, "a preview must not write anything")

        result = self.restore("workouts", self.workouts_csv, apply=True)
        self.assertTrue(Path(result["backup"]).exists())
        self.assertEqual(self.activity_values(), before)  # the deleted one is back with its own id
        history = self.client.get("/api/history?limit=3").json()
        self.assertEqual({e["action"] for e in history}, {"INSERT", "UPDATE", "DELETE"})

    def test_rows_without_an_id_are_added(self):
        text = "Date,Category,Session,Distance (mi)\r\n2026-09-20,Run,Imported jog,2.5\r\n"
        result = self.restore("workouts", text)
        self.assertEqual((result["added"], result["removed"]), (1, len(self.activities)))

    def test_id_is_read_from_the_details_link_of_older_exports(self):
        a = self.activities[0]
        # No Activity ID column, only the link to the workout's details.
        headers = {**{h: f for h, f in WORKOUT_HEADERS.items() if h != "Activity ID"}, "Details": "details"}
        row = {**a, "details": f"http://localhost:5173/#/log/{a['activity_id']}"}
        result = self.restore("workouts", to_csv([row], headers))
        self.assertEqual((result["added"], result["unchanged"]), (0, 1))

    def test_formula_guard_is_removed(self):
        a = self.activities[0]
        row = {**a, "notes": "'=not a formula"}
        self.restore("workouts", to_csv([row], WORKOUT_HEADERS), apply=True)
        restored = next(x for x in self.data()["activities"] if x["activity_id"] == a["activity_id"])
        self.assertEqual(restored["notes"], "=not a formula")

    def test_bad_rows_are_reported_with_their_row_number(self):
        text = "Date,Category,Session\r\n2026-09-20,Run,Fine\r\n2026-09-21,Swim,Nope\r\n"
        detail = self.restore("workouts", text, status=422)["detail"]
        self.assertTrue(detail.startswith("Row 3: category"), detail)

    def test_wrong_file_is_refused(self):
        detail = self.restore("workouts", self.plan_csv.replace("Session", "Planned"), status=422)["detail"]
        self.assertIn("missing the Session column", detail)

    def test_duplicate_ids_are_refused(self):
        rows = [self.activities[0], self.activities[0]]
        self.restore("workouts", to_csv(rows, WORKOUT_HEADERS), status=422)

    def test_unknown_plan_session_is_refused(self):
        text = "Date,Category,Session,Plan ID\r\n2026-09-20,Run,Jog,W99-Mon\r\n"
        self.assertIn("W99-Mon", self.restore("workouts", text, status=422)["detail"])

    # ---- plan ----

    def test_restoring_an_unchanged_plan_changes_nothing(self):
        result = self.restore("plan", self.plan_csv)
        self.assertEqual((result["added"], result["changed"], result["removed"], result["unlinked"]), (0, 0, 0, 0))

    def test_plan_restore_undoes_edits_and_keeps_the_order(self):
        before = self.plan_values()
        target = next(p for p in self.plan if p["week"] == 19 and p["category"] != "Rest")
        self.client.put("/api/plan/weeks/19", json={"week_type": "Peak" if target["week_type"] != "Peak" else "Normal"})
        self.client.put(f"/api/plan/{target['plan_id']}/skip", json={"reason": "travel"})
        self.client.post("/api/plan/weeks/19/sessions", json={"day": "Mon", "category": "Row",
                                                             "planned_session": "Extra row"})
        self.client.delete(f"/api/plan/{target['plan_id']}/skip")
        self.client.put(f"/api/plan/{target['plan_id']}/skip", json={"reason": "moved"})

        preview = self.restore("plan", self.plan_csv)
        self.assertEqual(preview["added"], 0)
        self.assertEqual(preview["removed"], 1)
        self.assertGreaterEqual(preview["changed"], 1)

        self.restore("plan", self.plan_csv, apply=True)
        self.assertEqual(self.plan_values(), before)

    def test_plan_restore_unlinks_workouts_of_removed_sessions(self):
        linked = next(a for a in self.activities if a["plan_id"])
        rows = [p for p in self.plan if p["plan_id"] != linked["plan_id"]]
        result = self.restore("plan", to_csv(rows, PLAN_HEADERS), apply=True)
        self.assertEqual(result["removed"], 1)
        self.assertGreaterEqual(result["unlinked"], 1)
        after = next(a for a in self.data()["activities"] if a["activity_id"] == linked["activity_id"])
        self.assertIsNone(after["plan_id"])

    def test_plan_restore_keeps_skips(self):
        target = next(p for p in self.plan if p["week"] == 19 and p["category"] != "Rest")
        rows = [{**p, "skipped": True, "skip_reason": "sick"} if p is target else p for p in self.plan]
        self.restore("plan", to_csv(rows, PLAN_HEADERS), apply=True)
        after = next(p for p in self.data()["plan"] if p["plan_id"] == target["plan_id"])
        self.assertEqual((after["status"], after["skip_reason"]), ("skipped", "sick"))

    def test_mixed_week_types_are_refused(self):
        rows = [dict(p) for p in self.plan]
        first = next(p for p in rows if p["week"] == 19)
        first["week_type"] = "Peak" if first["week_type"] != "Peak" else "Normal"
        self.assertIn("Week 19", self.restore("plan", to_csv(rows, PLAN_HEADERS), status=422)["detail"])

    def test_skipped_rest_day_is_refused(self):
        rows = [{**p, "skipped": True} if p["category"] == "Rest" else p for p in self.plan]
        self.restore("plan", to_csv(rows, PLAN_HEADERS), status=422)


if __name__ == "__main__":
    unittest.main()
