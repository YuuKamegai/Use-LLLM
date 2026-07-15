from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from use_lllm.core.audit import AuditLogger
from use_lllm.core.policy import ToolDecision, ToolSafety
from use_lllm.core.sessions import SessionStore


class GeneralSessionTests(unittest.TestCase):
    def test_general_and_lipidomics_sessions_are_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            lipid = store.create_session("lipid")
            general = store.create_session("chat", surface="general")

            self.assertEqual(lipid["surface"], "lipidomics")
            self.assertEqual(general["surface"], "general")
            self.assertEqual(general["state"]["phase"], "chat")
            self.assertEqual([item["id"] for item in store.list_sessions()], [lipid["id"]])
            self.assertEqual(
                [item["id"] for item in store.list_sessions("general")],
                [general["id"]],
            )

    def test_general_messages_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            store = SessionStore(root)
            session = store.create_session("chat", surface="general")
            store.add_message(session["id"], "user", "こんにちは")
            store.add_message(
                session["id"],
                "assistant",
                "確認します",
                {"tool_calls": [{"function": {"name": "srv::peek"}}]},
            )

            restored = SessionStore(root).get_session(session["id"])
            self.assertEqual(restored["surface"], "general")
            self.assertEqual(
                [item["content"] for item in restored["messages"]],
                ["こんにちは", "確認します"],
            )
            self.assertEqual(
                restored["messages"][1]["metadata"]["tool_calls"][0]["function"]["name"],
                "srv::peek",
            )

    def test_tool_ledger_and_summary_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            store = SessionStore(root)
            session = store.create_session("chat", surface="general")
            first = store.add_message(session["id"], "user", "目的")
            last = store.add_message(session["id"], "assistant", "了解")
            invocation = store.start_tool_invocation(
                session["id"],
                "ms-data-parser",
                "ms-data-parser::arf_parser",
                {"file_path": "C:/data/test.arf"},
                connection_generation=42,
            )
            result_message = store.add_message(
                session["id"], "tool", "x" * 4000, {"tool_name": "arf_parser"}
            )
            store.complete_tool_invocation(
                invocation,
                result_message_id=result_message,
                is_error=False,
                result_text="x" * 4000,
            )
            store.add_conversation_summary(
                session["id"],
                first,
                last,
                {"objective": "目的", "facts": ["了解"]},
                source_hash="abc",
                model="fake",
                estimated_tokens=12,
            )

            restored = SessionStore(root).get_session(session["id"])

            self.assertEqual(
                restored["tool_invocations"][0]["arguments"]["file_path"],
                "C:/data/test.arf",
            )
            self.assertLessEqual(len(restored["tool_invocations"][0]["result_summary"]), 2050)
            self.assertEqual(restored["memory_summary"]["summary"]["objective"], "目的")

    def test_legacy_database_is_migrated_to_lipidomics_surface(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            root.mkdir(parents=True)
            database = root / "use-lllm.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
                    objective TEXT NOT NULL DEFAULT '', dataset_path TEXT,
                    state_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL)"""
                )
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                    ("old", "legacy", "new", "", json.dumps({}), "now", "now"),
                )
                connection.commit()

            restored = SessionStore(root).get_session("old")
            self.assertEqual(restored["surface"], "lipidomics")

    def test_unknown_surface_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            with self.assertRaisesRegex(ValueError, "surface"):
                store.create_session("bad", surface="other")


class AuditLoggerTests(unittest.TestCase):
    def test_tool_audit_omits_argument_values(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("chat", surface="general")
            audit = AuditLogger(store)
            decision = ToolDecision(
                "srv::peek",
                ToolSafety.READ_ONLY,
                True,
                False,
                "read-only",
            )

            audit.record_tool_decision(
                session["id"],
                "srv::peek",
                {"path": "C:/secret/data", "query": "private"},
                decision,
            )
            audit.record_tool_result(session["id"], "srv::peek", is_error=False, content_blocks=2)

            events = store.list_events(session["id"])
            encoded = json.dumps(events, ensure_ascii=False)
            self.assertNotIn("C:/secret/data", encoded)
            self.assertNotIn("private", encoded)
            self.assertEqual(events[0]["payload"]["argument_keys"], ["path", "query"])
            self.assertEqual(events[1]["payload"]["content_blocks"], 2)


if __name__ == "__main__":
    unittest.main()
