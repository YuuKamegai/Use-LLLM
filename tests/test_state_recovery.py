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
    """呼び出し順を記録し、指定回数だけ missing_state を返すフェイク。

    ``fail_once`` はその名前への最初の呼び出しだけ envelope を返す（以降は
    ``ok:<name>``）。``always_fail`` は呼ばれるたびに同じ envelope を返し続ける。
    「1回だけ失敗して次は必ず成功する」フィクスチャではリトライが常に成功して
    しまい、単段リトライと再帰的リカバリを区別できないテストがある
    （``test_retry_is_single_level``）ため、両方を用意する。
    """

    def __init__(
        self,
        *,
        replay_safe: set[str],
        fail_once: dict[str, str] | None = None,
        always_fail: dict[str, str] | None = None,
    ):
        self._replay_safe = replay_safe
        self._fail_once = dict(fail_once or {})
        self._always_fail = dict(always_fail or {})
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
        if name in self._always_fail:
            text = self._always_fail[name]
        else:
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

    async def test_uses_an_alternative_when_only_one_is_usable(self) -> None:
        """required_tools は OR。使える候補が1つしかなければそれを選ぶ。"""
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

    async def test_recency_beats_list_order_when_both_candidates_are_usable(self) -> None:
        """required_tools の順序ではなく、本セッションで直近に成功した候補を選ぶ。

        両方が replay-safe かつ両方に成功履歴がある場合、リストで先に書かれた
        候補ではなく、より最近に成功した方を復元する。古い候補を復元すると、
        ユーザーが最後に見ていたのとは別の状態を「同じもの」として提示して
        しまう —— これはこのタスクが閉じたのと同じ種類のバグなので、
        リスト順に戻る退行をこのテストで検出できなければならない。
        """
        registry = RecordingRegistry(
            replay_safe={"srv::first", "srv::second"},
            fail_once={"srv::save": envelope("plot", ["first", "second"])},
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        # "first" が先に成功するが、リストでは先頭。"second" は後から成功し、
        # リストでは2番目 —— リスト順と直近成功の結論が食い違う配置。
        await loop._call_and_record(
            sid, "srv::first", {"n": 1}, approved=True, network_mode="offline"
        )
        await loop._call_and_record(
            sid, "srv::second", {"n": 2}, approved=True, network_mode="offline"
        )
        result = await loop._call_and_record(
            sid, "srv::save", {}, approved=True, network_mode="offline"
        )
        result = await loop._maybe_recover_and_retry(
            sid, "srv::save", {}, result, approved=True, network_mode="offline"
        )
        self.assertFalse(result.is_error)
        # 復旧のために呼ばれたのは "first" ではなく、より最近成功した "second"。
        replay_call_index = 3  # first, second, save(失敗), ここが復旧のreplay
        self.assertEqual(registry.calls[replay_call_index], ("srv::second", {"n": 2}))
        self.assertEqual(sum(1 for name, _ in registry.calls if name == "srv::first"), 1)
        self.assertEqual(sum(1 for name, _ in registry.calls if name == "srv::second"), 2)

    async def test_retry_is_single_level(self) -> None:
        """復旧後もまた missing_state なら、復旧の復旧はしない。

        fail_once（1回だけ失敗して以降は必ず成功）だとリトライが必ず成功して
        しまい、単段リトライと「復旧の復旧」（再帰的なリカバリ）を区別できない。
        always_fail で毎回 missing_state を返し続けるレジストリを使い、
        (1) 最終結果が依然としてエラーであること、(2) prep の再実行が
        ちょうど1回（＝復旧1回分）だけであることの両方を確認する。
        """
        registry = RecordingRegistry(
            replay_safe={"srv::prep"},
            always_fail={"srv::pca": envelope("matrix", ["prep"])},
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
        # 事前のセットアップ呼び出し1回 + 復旧での再実行1回 = 2回。
        # これが3回以上になっていれば、リトライの結果が再び復旧に回されている
        # （＝単段のはずのリトライが再帰している）ことを意味する。
        self.assertEqual(sum(1 for name, _ in registry.calls if name == "srv::prep"), 2)
        # pca 自体もセットアップの失敗1回 + リトライの失敗1回 = 2回で止まる。
        self.assertEqual(sum(1 for name, _ in registry.calls if name == "srv::pca"), 2)

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
