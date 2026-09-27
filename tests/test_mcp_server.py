"""Tests for the MCP server (backend/mcp_server.py).

Run from the project root:   .venv\\Scripts\\python.exe -m unittest tests.test_mcp_server -v

Everything runs against the temporary database copy and log folder set up in tests/__init__.py.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from tests import LOG_DIR, ROOT, TEST_DB  # noqa: F401  (importing tests points the backend at the temp copies)

from mcp import Client  # noqa: E402

from backend import mcp_server  # noqa: E402
from backend.app.core import config  # noqa: E402
from backend.app.core.database import engine, init_db  # noqa: E402

EXPECTED_READ_ONLY = {"get_summary", "get_week", "list_activities", "list_history"}
EXPECTED_WRITE = {"log_activity", "update_activity", "skip_planned_session", "unskip_planned_session"}
EXPECTED_DESTRUCTIVE = {"delete_activity", "revert_history_entry"}


def setUpModule():
    # If something imported the backend before MARATHON_DB was set, refuse to touch the real file.
    if Path(config.DB_PATH) != TEST_DB:
        raise RuntimeError(f"Tests must use the temp database, but the app is using {config.DB_PATH}")
    init_db()


def tearDownModule():
    engine.dispose()  # release the temp database file so it can be deleted at exit


class ToolFailed(Exception):
    """A tool call came back with isError set; str() is the message the client would see."""


async def call(client, name, **arguments):
    result = await client.call_tool(name, arguments)
    if result.is_error:
        raise ToolFailed(result.content[0].text if result.content else "tool error")
    data = result.structured_content
    if data is not None:
        return data["result"] if set(data) == {"result"} else data
    return json.loads(result.content[0].text)


def open_client():
    return Client(mcp_server.mcp)


async def find_session(client, wanted):
    """First planned session (searching every week) for which wanted(session) is true."""
    last = (await call(client, "get_summary"))["last_week"]
    for week in range(1, last + 1):
        for session in (await call(client, "get_week", week=week))["sessions"]:
            if wanted(session):
                return session
    raise AssertionError("no matching planned session in the test database")


def new_run(**overrides):
    return {"activity_date": "2026-09-20", "category": "Run", "actual_session": "MCP test run",
            "distance_mi": 3.0, "duration_min": 30, **overrides}


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_registered_with_the_right_hints(self):
        async with open_client() as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
        self.assertEqual(set(tools), EXPECTED_READ_ONLY | EXPECTED_WRITE | EXPECTED_DESTRUCTIVE)
        for name in EXPECTED_READ_ONLY:
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        for name in EXPECTED_WRITE:
            self.assertFalse(tools[name].annotations.read_only_hint, name)
            self.assertFalse(tools[name].annotations.destructive_hint, name)
        for name in EXPECTED_DESTRUCTIVE:
            self.assertTrue(tools[name].annotations.destructive_hint, name)

    async def test_summary_and_week(self):
        async with open_client() as client:
            summary = await call(client, "get_summary")
            self.assertLessEqual({"race_date", "current_week", "sessions_due", "sessions_skipped"}, set(summary))

            week = await call(client, "get_week")  # default: the current week
            self.assertTrue(week["sessions"])
            self.assertLessEqual({"plan_id", "status", "planned_session"}, set(week["sessions"][0]))

            with self.assertRaisesRegex(ToolFailed, "not in the plan"):
                await call(client, "get_week", week=999)

    async def test_log_then_revert_shows_up_in_history(self):
        async with open_client() as client:
            created = await call(client, "log_activity", **new_run())
            self.assertEqual(created["actual_session"], "MCP test run")

            listed = await call(client, "list_activities", limit=200)
            self.assertIn(created["activity_id"], [a["activity_id"] for a in listed])

            entry = (await call(client, "list_history", limit=1))[0]  # written by the DB trigger
            self.assertEqual((entry["action"], entry["activity_id"]), ("INSERT", created["activity_id"]))

            await call(client, "revert_history_entry", history_id=entry["history_id"])
            listed = await call(client, "list_activities", limit=200)
            self.assertNotIn(created["activity_id"], [a["activity_id"] for a in listed])

    async def test_bad_input_is_rejected_with_a_readable_message(self):
        async with open_client() as client:
            with self.assertRaisesRegex(ToolFailed, "Unknown plan session 'NOPE'"):
                await call(client, "log_activity", **new_run(plan_id="NOPE"))
            with self.assertRaises(ToolFailed):  # not one of the allowed categories
                await call(client, "log_activity", **new_run(category="Swim"))
            with self.assertRaisesRegex(ToolFailed, "distance_mi"):
                await call(client, "log_activity", **new_run(distance_mi=-1))
            with self.assertRaisesRegex(ToolFailed, "not found"):
                await call(client, "delete_activity", activity_id=999999)

    async def test_update_changes_only_what_you_pass_and_can_clear(self):
        async with open_client() as client:
            session = await find_session(client, lambda s: s["category"] != "Rest")
            created = await call(client, "log_activity", **new_run(plan_id=session["plan_id"], notes="first"))

            updated = await call(client, "update_activity", activity_id=created["activity_id"], notes="second")
            self.assertEqual(updated["notes"], "second")
            self.assertEqual(updated["plan_id"], session["plan_id"])  # untouched
            self.assertEqual(updated["distance_mi"], 3.0)             # untouched

            cleared = await call(client, "update_activity", activity_id=created["activity_id"], plan_id="")
            self.assertIsNone(cleared["plan_id"])
            self.assertEqual(cleared["notes"], "second")

            with self.assertRaisesRegex(ToolFailed, "actual_session"):
                await call(client, "update_activity", activity_id=created["activity_id"], actual_session="")

            entries = await call(client, "list_history", activity_id=created["activity_id"])
            fields = {c["field"] for e in entries if e["action"] == "UPDATE" for c in e["changes"]}
            self.assertEqual(fields, {"notes", "plan_id"})

    async def test_delete_then_restore(self):
        async with open_client() as client:
            created = await call(client, "log_activity", **new_run(actual_session="to delete"))
            await call(client, "delete_activity", activity_id=created["activity_id"])
            entry = (await call(client, "list_history", limit=1))[0]
            self.assertEqual(entry["action"], "DELETE")

            await call(client, "revert_history_entry", history_id=entry["history_id"])
            listed = await call(client, "list_activities", limit=200)
            restored = [a for a in listed if a["activity_id"] == created["activity_id"]]
            self.assertEqual([a["actual_session"] for a in restored], ["to delete"])

    async def test_skip_and_unskip(self):
        async with open_client() as client:
            target = await find_session(
                client, lambda s: s["category"] != "Rest" and s["status"] in ("upcoming", "today", "missed"))
            await call(client, "skip_planned_session", plan_id=target["plan_id"], reason="sick")

            week = await call(client, "get_week", week=int(target["plan_id"].split("-")[0][1:]))
            skipped = next(s for s in week["sessions"] if s["plan_id"] == target["plan_id"])
            self.assertEqual((skipped["status"], skipped["skip_reason"]), ("skipped", "sick"))

            await call(client, "unskip_planned_session", plan_id=target["plan_id"])
            with self.assertRaisesRegex(ToolFailed, "isn't skipped"):
                await call(client, "unskip_planned_session", plan_id=target["plan_id"])

    async def test_rest_days_cannot_be_skipped(self):
        async with open_client() as client:
            rest = await find_session(client, lambda s: s["category"] == "Rest")
            with self.assertRaisesRegex(ToolFailed, "Rest days can't be skipped"):
                await call(client, "skip_planned_session", plan_id=rest["plan_id"])


class ToolLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_call_is_logged_with_arguments_and_outcome(self):
        async with open_client() as client:
            with self.assertLogs("marathon.mcp", "INFO") as logs:
                await call(client, "list_activities", limit=2, week=1)
                with self.assertRaises(ToolFailed):
                    await call(client, "get_week", week=999)
        # (The first call also logs whose data the server works on.)
        ok, refused = [r for r in logs.records if not r.getMessage().startswith("Working on")]
        self.assertEqual((ok.levelname, refused.levelname), ("INFO", "WARNING"))
        self.assertRegex(ok.getMessage(), r"^list_activities\(limit=2, week=1\) ok in \d+ ms$")
        self.assertRegex(refused.getMessage(), r"^get_week\(week=999\) refused: Week 999 is not in the plan")

    async def test_long_values_are_shortened_in_the_log(self):
        async with open_client() as client:
            with self.assertLogs("marathon.mcp", "INFO") as logs:
                created = await call(client, "log_activity", **new_run(actual_session="x" * 500))
                await call(client, "delete_activity", activity_id=created["activity_id"])
        self.assertLess(len(logs.records[0].getMessage()), 300)


class StdioTests(unittest.TestCase):
    """Launch it the way an MCP client does: `python -m backend.mcp_server` over stdio."""

    def test_runs_as_a_module_and_stdout_carries_only_protocol_messages(self):
        proc = subprocess.Popen(
            [sys.executable, "-m", "backend.mcp_server"], cwd=ROOT, env=dict(os.environ),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        )
        lines: "queue.Queue[str]" = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout], daemon=True).start()

        def send(message):
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()

        def receive():
            return json.loads(lines.get(timeout=30))  # any stray print() would fail json.loads

        try:
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
            self.assertEqual(receive()["result"]["serverInfo"]["name"], "marathon")
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {t["name"] for t in receive()["result"]["tools"]}
            self.assertEqual(names, EXPECTED_READ_ONLY | EXPECTED_WRITE | EXPECTED_DESTRUCTIVE)
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
        self.assertIn("marathon MCP server starting", (LOG_DIR / "mcp_server.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
