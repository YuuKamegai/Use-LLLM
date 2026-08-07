"""状態喪失からの事後リカバリ。サーバ固有知識を使わないことも併せて確認する。"""

from __future__ import annotations

import json
import unittest

from tests.test_general_agent import FakeOllama, response  # 既存のフェイクを再利用
from use_lllm.core.mcp_client import MCPToolResult
from use_lllm.core.policy import ToolDecision, ToolSafety
from use_lllm.core.sessions import SessionStore
from use_lllm.general.agent_loop import GeneralAgentLoop


def envelope(state, tools, message="先に実行してください"):
    return json.dumps(
        {
            "error": {
                "code": "missing_state",
                "state": state,
                "required_tools": list(tools),
                "message": message,
            }
        },
        ensure_ascii=False,
    )


class RecordingRegistry:
    """呼び出し順を記録し、指定回数だけ missing_state を返すフェイク。"""

    def __init__(self, *, replay_safe: set[str], fail_once: dict[str, str]):
        self._replay_safe = replay_safe
        self._fail_once = dict(fail_once)
        self.calls: list[tuple[str, dict]] = []

    def ollama_tools(self, query=None, *, limit=None, excluded=None):
        return []

    def decide(self, name, *, approved=False, network_mode="offline"):
        return ToolDecision(name, ToolSafety.READ_ONLY, True, False, "テスト")

    def is_replay_safe(self, name):
        return name in self._replay_safe

    def may_write_files(self, name):
        return True

    async def call_tool(
        self, name, arguments=None, *, approved=False, network_mode="offline", session_id=None
    ):
        self.calls.append((name, dict(arguments or {})))
        body = self._fail_once.pop(name, None)
        text = body if body is not None else f"ok:{name}"
        return MCPToolResult(name, False, ({"type": "text", "text": text},), None)


class MissingStateRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(Path(self.tmp.name))
        self.session = self.store.create_session(surface="general")

    def loop(self, registry):
        ollama = FakeOllama([response("done")])
        return GeneralAgentLoop(ollama, self.store, registry)

    async def test_replays_the_named_tool_with_its_recorded_arguments(self) -> None:
        """LLM に再実行させると引数が変わりうる。記録済み引数の同一性を守る。"""
        registry = RecordingRegistry(
            replay_safe={"srv::prep"},
            fail_once={"srv::pca": envelope("matrix", ["prep"])},
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(
            sid,
            "srv::prep",
            {"normalize": "none", "impute": "half_min"},
            approved=True,
            network_mode="offline",
        )
        result = await loop._call_and_record(
            sid, "srv::pca", {}, approved=True, network_mode="offline"
        )
        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertFalse(result.is_error)
        self.assertIn(
            ("srv::prep", {"normalize": "none", "impute": "half_min"}),
            registry.calls[1:],
        )

    async def test_gives_up_when_the_producer_is_not_replay_safe(self) -> None:
        registry = RecordingRegistry(
            replay_safe=set(), fail_once={"srv::pca": envelope("matrix", ["prep"])}
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(sid, "srv::prep", {}, approved=True, network_mode="offline")
        result = await loop._call_and_record(
            sid, "srv::pca", {}, approved=True, network_mode="offline"
        )
        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertTrue(result.is_error)

    async def test_gives_up_when_there_is_no_recorded_invocation(self) -> None:
        registry = RecordingRegistry(
            replay_safe={"srv::prep"}, fail_once={"srv::pca": envelope("matrix", ["prep"])}
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        result = await loop._call_and_record(
            sid, "srv::pca", {}, approved=True, network_mode="offline"
        )
        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertTrue(result.is_error)

    async def test_picks_the_first_usable_alternative(self) -> None:
        """required_tools は OR。使える最初の候補を選ぶ。"""
        registry = RecordingRegistry(
            replay_safe={"srv::second"},
            fail_once={"srv::save": envelope("plot", ["first", "second"])},
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(
            sid, "srv::second", {"n": 1}, approved=True, network_mode="offline"
        )
        result = await loop._call_and_record(
            sid, "srv::save", {}, approved=True, network_mode="offline"
        )
        result = await loop._maybe_recover_and_retry(
            sid, "srv::save", {}, result, approved=True, network_mode="offline"
        )
        self.assertFalse(result.is_error)
        self.assertIn(("srv::second", {"n": 1}), registry.calls)

    async def test_retry_is_single_level(self) -> None:
        """復旧後もまた missing_state なら、復旧の復旧はしない。"""
        registry = RecordingRegistry(
            replay_safe={"srv::prep"},
            fail_once={},
        )
        registry._fail_once = {"srv::pca": envelope("matrix", ["prep"])}
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(sid, "srv::prep", {}, approved=True, network_mode="offline")
        result = await loop._call_and_record(
            sid, "srv::pca", {}, approved=True, network_mode="offline"
        )
        await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertEqual(sum(1 for name, _ in registry.calls if name == "srv::prep"), 2)

    async def test_a_plain_error_is_left_alone(self) -> None:
        registry = RecordingRegistry(replay_safe=set(), fail_once={})
        loop = self.loop(registry)
        sid = self.session["id"]
        plain = MCPToolResult("srv::x", True, ({"type": "text", "text": "壊れた"},), None)
        result = await loop._maybe_recover_and_retry(
            sid, "srv::x", {}, plain, approved=True, network_mode="offline"
        )
        self.assertIs(result, plain)
        self.assertEqual(registry.calls, [])


if __name__ == "__main__":
    unittest.main()
