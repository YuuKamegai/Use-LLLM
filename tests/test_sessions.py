from pathlib import Path
import tempfile
import unittest

from use_lllm.core.sessions import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_persists_events_messages_and_detects_changed_input(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = SessionStore(root / "state")
            session = store.create_session("test", "objective")
            store.add_message(session["id"], "user", "hello")
            event_id = store.append_event(session["id"], "test", {"ok": True})
            source = root / "sample.arf"
            source.write_bytes(b"first")
            store.bind_file(session["id"], "NEG", "arf", str(source))
            self.assertEqual(store.verify_bindings(session["id"])[0]["state"], "valid")
            source.write_bytes(b"changed")
            self.assertEqual(store.verify_bindings(session["id"])[0]["state"], "changed")
            restored = SessionStore(root / "state").get_session(session["id"])
            self.assertEqual(restored["messages"][0]["content"], "hello")
            self.assertEqual(restored["events"][0]["id"], event_id)

    def test_marks_incomplete_tool_event_as_interrupted_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = SessionStore(root)
            session = store.create_session()
            store.append_event(session["id"], "analysis", {"step": "running"}, status="running")
            restored = SessionStore(root).get_session(session["id"])
            self.assertEqual(restored["status"], "error")
            self.assertEqual(restored["events"][0]["status"], "interrupted")

    def test_delete_session_removes_database_rows_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("delete-me")
            store.add_message(session["id"], "user", "private")
            store.append_event(session["id"], "analysis", {"ok": True})
            artifact = store.save_artifact(session["id"], "result.json", b"{}")
            artifact_path = store.artifact_root / artifact["path"]
            self.assertTrue(artifact_path.is_file())

            result = store.delete_session(session["id"])

            self.assertTrue(result["deleted"])
            self.assertFalse(artifact_path.exists())
            with self.assertRaises(KeyError):
                store.get_session(session["id"])


if __name__ == "__main__":
    unittest.main()
