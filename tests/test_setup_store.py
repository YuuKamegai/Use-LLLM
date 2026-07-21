from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from use_lllm.core.setup_store import SETUP_VERSION, SetupStore


class SetupStoreTests(unittest.TestCase):
    def test_missing_or_malformed_state_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SetupStore(Path(temp))
            self.assertFalse(store.is_complete())
            store.path.write_text("{bad", encoding="utf-8")
            self.assertFalse(store.is_complete())

    def test_mark_complete_is_versioned_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SetupStore(Path(temp))
            state = store.mark_complete(endpoint="local", model="qwen3:14b")
            self.assertEqual(state["completed_version"], SETUP_VERSION)
            self.assertTrue(store.is_complete())
            persisted = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["model"], "qwen3:14b")


if __name__ == "__main__":
    unittest.main()
