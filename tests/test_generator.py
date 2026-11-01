"""Tests for generating a new runner's marathon plan (services/generator.py) and its safety rules.

Run from the project root:   .venv\\Scripts\\python.exe -m unittest tests.test_generator -v
"""
import csv
import io
import unittest
from collections import defaultdict

from backend.app.core.errors import UnprocessableError
from backend.app.services import backup, generator
from backend.app.services.planning import DAYS

GOALS = [  # (weeks, longest run, weekly miles, runs a week)
    (16, 13, 25, 3), (18, 10, 20, 3), (23, 6, 15, 4), (32, 10, 25, 4), (52, 6, 15, 5), (52, 22, 50, 5),
]


def by_week(rows):
    weeks = defaultdict(list)
    for r in rows:
        weeks[r["week"]].append(r)
    return weeks


def long_run(rows):
    return next(r["target_distance_mi"] for r in rows if r["day"] == "Sun")


def run_miles(rows):
    return sum(r["target_distance_mi"] for r in rows if r["category"] in ("Run", "Race"))


class MinWeeksTests(unittest.TestCase):
    def test_minimum_grows_with_less_to_build_on(self):
        self.assertEqual([generator.min_weeks(mi) for mi in (3, 6, 10, 13, 20, 26)], [27, 23, 18, 16, 16, 16])

    def test_too_soon_is_refused_with_the_minimum(self):
        with self.assertRaisesRegex(UnprocessableError, "at least 23 weeks"):
            generator.generate_plan(22, 6, 15, 4)
        generator.generate_plan(23, 6, 15, 4)

    def test_no_base_or_too_few_runs_is_refused(self):
        for args in [(30, 2.5, 15, 4), (30, 6, 8, 4), (30, 6, 15, 2), (30, 6, 15, 6)]:
            with self.subTest(args=args), self.assertRaises(UnprocessableError):
                generator.generate_plan(*args)


class GeneratedPlanTests(unittest.TestCase):
    def test_shape(self):
        for goal in GOALS:
            weeks, _, _, runs = goal
            rows = generator.generate_plan(*goal)
            plan = by_week(rows)
            with self.subTest(goal=goal):
                self.assertEqual(sorted(plan), list(range(1, weeks + 1)))
                for w, week_rows in plan.items():
                    self.assertEqual([r["day"] for r in week_rows], DAYS)  # one session a day
                    self.assertEqual(sum(r["category"] != "Rest" for r in week_rows), runs)
                    self.assertEqual(len({r["week_type"] for r in week_rows}), 1)
                types = [plan[w][0]["week_type"] for w in range(weeks - 3, weeks + 1)]
                self.assertEqual(types, ["Peak", "Taper", "Taper", "Race"])
                race = plan[weeks][-1]
                self.assertEqual((race["day"], race["category"], race["target_distance_mi"]), ("Sun", "Race", 26.2))

    def test_long_runs_build_safely_to_the_peak(self):
        for goal in GOALS:
            weeks, longest = goal[:2]
            plan = by_week(generator.generate_plan(*goal))
            longs = [long_run(plan[w]) for w in range(1, weeks - 3)]
            with self.subTest(goal=goal):
                self.assertLessEqual(longs[0], max(longest, generator.BASE_LONG_RUN_MI))
                self.assertEqual(long_run(plan[weeks - 3]), generator.PEAK_LONG_RUN_MI)
                self.assertEqual([long_run(plan[w]) for w in (weeks - 2, weeks - 1)], [12, 8])
                # Never more than a mile over the longest run so far.
                best = longest
                for mi in longs:
                    self.assertLessEqual(mi, best + 1)
                    best = max(best, mi)

    def test_step_back_every_fourth_week(self):
        plan = by_week(generator.generate_plan(32, 10, 25, 4))
        step_backs = [w for w, rows in plan.items() if rows[0]["week_type"] == "Step-back"]
        self.assertEqual(step_backs, [4, 8, 12, 16, 20, 24, 28])
        for w in step_backs:
            self.assertLess(long_run(plan[w]), long_run(plan[w - 1]))
            self.assertEqual(plan[w][3]["run_subtype"], "Easy")  # no workout on Thursday

    def test_weekly_miles_grow_at_most_ten_percent(self):
        for goal in GOALS:
            weeks, _, weekly, _ = goal
            plan = by_week(generator.generate_plan(*goal))
            last = weekly
            with self.subTest(goal=goal):
                for w in range(1, weeks - 2):
                    if plan[w][0]["week_type"] == "Step-back":
                        continue
                    total = run_miles(plan[w])
                    self.assertLessEqual(total, max(last * 1.1, last + 2) + 1e-9, f"week {w}")
                    last = total

    def test_long_plan_holds_the_long_run_instead_of_peaking_early(self):
        plan = by_week(generator.generate_plan(52, 6, 15, 5))
        self.assertEqual(max(long_run(plan[w]) for w in range(1, 40)), generator.BASE_LONG_RUN_MI)

    def test_rows_pass_the_plan_backup_rules(self):
        # The same validation an imported plan gets; also checks plan ids are unique.
        rows = generator.generate_plan(18, 10, 20, 3)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(backup.PLAN_COLUMNS)
        for r in rows:
            writer.writerow(["" if r[f] in (None, False) else r[f] for f in backup.PLAN_COLUMNS.values()])
        self.assertEqual(backup.parse_plan(out.getvalue()), rows)


if __name__ == "__main__":
    unittest.main()
