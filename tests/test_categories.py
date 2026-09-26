"""Tests for widening activity_log's category CHECK constraint (adding Stretch) on an old database.

Run from the project root:   .venv\\Scripts\\python.exe -m unittest tests.test_categories -v
"""
import sqlite3
import unittest

from tests import TMP

from backend.app.core.database import allow_activity_categories

# activity_log, training_plan and the view as they were before Stretch existed (trimmed columns).
OLD_SCHEMA = """
CREATE TABLE training_plan (
    plan_id  TEXT PRIMARY KEY,
    week     INTEGER NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('Strength','Bike','Run','Race','Row','Rest'))
);
CREATE TABLE activity_log (
    activity_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_date   TEXT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('Strength','Bike','Run','Race','Row','Rest')),
    actual_session  TEXT NOT NULL,
    plan_id         TEXT,
    FOREIGN KEY (plan_id) REFERENCES training_plan(plan_id)
);
CREATE VIEW plan_vs_actual AS
SELECT p.plan_id, COUNT(a.activity_id) AS linked_activity_count
FROM training_plan p LEFT JOIN activity_log a ON a.plan_id = p.plan_id GROUP BY p.plan_id;
INSERT INTO training_plan VALUES ('W1-Mon', 1, 'Strength');
INSERT INTO activity_log (activity_date, category, actual_session, plan_id) VALUES
    ('2026-09-14', 'Strength', 'Full body', 'W1-Mon'),
    ('2026-09-14', 'Strength', 'Lower Body Stretch', NULL),
    ('2026-09-15', 'Run', 'Easy run', NULL);
DELETE FROM activity_log WHERE activity_id = 3;  -- the newest id is gone but must not be reused
"""


class WidenCategoriesTests(unittest.TestCase):
    def setUp(self):
        self.folder = TMP / f"categories-{self._testMethodName}"
        self.folder.mkdir()
        self.db = self.folder / "old.db"
        con = sqlite3.connect(self.db)
        try:
            con.executescript(OLD_SCHEMA)
        finally:
            con.close()

    def query(self, sql, *args):
        con = sqlite3.connect(self.db)
        try:
            con.execute("PRAGMA foreign_keys = ON")
            rows = con.execute(sql, args).fetchall()
            con.commit()
            return rows
        finally:
            con.close()

    def test_stretch_is_accepted_after_the_rebuild(self):
        add_stretch = "INSERT INTO activity_log (activity_date, category, actual_session) VALUES ('2026-09-16', ?, 'x')"
        with self.assertRaises(sqlite3.IntegrityError):
            self.query(add_stretch, "Stretch")
        self.assertTrue(allow_activity_categories(self.db))
        self.query("UPDATE activity_log SET category = 'Stretch' WHERE activity_id = 2")
        self.assertEqual(self.query("SELECT category FROM activity_log WHERE activity_id = 2"), [("Stretch",)])
        with self.assertRaises(sqlite3.IntegrityError):  # still a closed list
            self.query(add_stretch, "Yoga")

    def test_rows_ids_links_and_view_survive(self):
        allow_activity_categories(self.db)
        self.assertEqual(self.query("SELECT activity_id, actual_session, plan_id FROM activity_log ORDER BY activity_id"),
                         [(1, "Full body", "W1-Mon"), (2, "Lower Body Stretch", None)])
        self.assertEqual(self.query("SELECT * FROM plan_vs_actual"), [("W1-Mon", 1)])
        with self.assertRaises(sqlite3.IntegrityError):  # the foreign key is still enforced
            self.query("INSERT INTO activity_log (activity_date, category, actual_session, plan_id) "
                       "VALUES ('2026-09-16', 'Run', 'x', 'W9-Nope')")

    def test_deleted_ids_are_not_reused(self):
        allow_activity_categories(self.db)
        self.query("INSERT INTO activity_log (activity_date, category, actual_session) VALUES ('2026-09-16', 'Stretch', 'x')")
        self.assertEqual(self.query("SELECT MAX(activity_id) FROM activity_log"), [(4,)])

    def test_backup_first_and_only_once(self):
        self.assertTrue(allow_activity_categories(self.db))
        self.assertFalse(allow_activity_categories(self.db))
        (backup,) = (self.folder / "backups").iterdir()
        con = sqlite3.connect(backup)
        try:
            check = con.execute("SELECT sql FROM sqlite_master WHERE name = 'activity_log'").fetchone()[0]
            self.assertNotIn("Stretch", check)  # the copy is from before the change
            self.assertEqual(con.execute("SELECT COUNT(*) FROM activity_log").fetchone(), (2,))
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
