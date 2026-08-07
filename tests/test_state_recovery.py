"""状態喪失からの事後リカバリ。サーバ固有知識を使わないことも併せて確認する。"""

from __future__ import annotations

import asyncio
import json
import unittest

from tests.test_general_agent import FakeOllama, response  # 既存のフェイクを再利用
from use_lllm.core.mcp_client import MCPConnectionError, MCPToolResult
from use_lllm.core.policy import ToolDecision, ToolSafety
from use_lllm.core.sessions import SessionStore
from use_lllm.core.tool_result_contract import MissingState
from use_lllm.general.agent_loop import MAX_RECOVERY_DEPTH, GeneralAgentLoop


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


class ChainedStateRegistry:
    """load → prep → pca という実際の前提チェーンを模したフェイク。

    サーバ側の状態は ``self.state`` に持ち、reset() で「MCP再接続で状態が
    全部飛んだ」を再現する。クライアント側のセッション履歴（成功済み呼び出し
    の記録）はこれとは独立に SessionStore 側へ残り続ける点が実運用と同じ。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.state = {"load": False, "prep": False}

    def ollama_tools(self, query=None, *, limit=None, excluded=None):
        return []

    def decide(self, name, *, approved=False, network_mode="offline"):
        return ToolDecision(name, ToolSafety.READ_ONLY, True, False, "テスト")

    def is_replay_safe(self, name):
        return True

    def may_write_files(self, name):
        return True

    def reset(self) -> None:
        """MCP再接続を模す：サーバ側の状態を全部忘れる。"""
        self.state = {"load": False, "prep": False}

    async def call_tool(
        self, name, arguments=None, *, approved=False, network_mode="offline", session_id=None
    ):
        self.calls.append((name, dict(arguments or {})))
        if name == "srv::load":
            self.state["load"] = True
            text = "ok:load"
        elif name == "srv::prep":
            if not self.state["load"]:
                text = envelope("dataset", ["load"])
            else:
                self.state["prep"] = True
                text = "ok:prep"
        elif name == "srv::pca":
            if not self.state["prep"]:
                text = envelope("matrix", ["prep"])
            else:
                text = "ok:pca"
        else:
            raise AssertionError(f"unexpected tool: {name}")
        return MCPToolResult(name, False, ({"type": "text", "text": text},), None)


class CycleRegistry:
    """A が B を要求し、B が A を要求する、宣言された循環を模したフェイク。

    どちらも「本セッションで成功した呼び出し」の履歴だけは（テスト側で）
    捏造して存在させるが、実際に呼び出すと必ず missing_state を返し続ける。
    再帰的な復旧が有限回で止まることを検証するための最悪ケース。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def ollama_tools(self, query=None, *, limit=None, excluded=None):
        return []

    def decide(self, name, *, approved=False, network_mode="offline"):
        return ToolDecision(name, ToolSafety.READ_ONLY, True, False, "テスト")

    def is_replay_safe(self, name):
        return True

    def may_write_files(self, name):
        return True

    async def call_tool(
        self, name, arguments=None, *, approved=False, network_mode="offline", session_id=None
    ):
        self.calls.append((name, dict(arguments or {})))
        if name == "srv::a":
            text = envelope("s_a", ["b"])
        elif name == "srv::b":
            text = envelope("s_b", ["a"])
        else:
            raise AssertionError(f"unexpected tool: {name}")
        return MCPToolResult(name, False, ({"type": "text", "text": text},), None)


class DenyingRegistry(RecordingRegistry):
    """is_replay_safe は真だが、registry.decide がその候補を拒否するフェイク。

    idempotentHint=true だが readOnlyHint=false（＝ファイル書き込み）の
    ツールが required_tools の代替候補に混ざっている状況を模す。
    """

    def __init__(self, *, denied: set[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._denied = denied

    def decide(self, name, *, approved=False, network_mode="offline"):
        if name in self._denied:
            return ToolDecision(name, ToolSafety.LOCAL_WRITE, False, True, "承認が必要です。")
        return ToolDecision(name, ToolSafety.READ_ONLY, True, False, "テスト")


class RaisingRegistry(RecordingRegistry):
    """指定した名前への2回目以降の呼び出しで例外を送出するフェイク（FIX2用）。

    1回目（テストのセットアップ呼び出し。復旧が参照する履歴を作るためのもの）
    は成功させ、2回目以降（＝復旧のリプレイ呼び出し）でのみ MCPConnectionError
    を模した例外を送出する。セットアップ自体を失敗させると、復旧が試す
    前提の履歴が残らず、「リプレイ中の例外」を検証するテストの意図に届かない。
    """

    def __init__(self, *, raises_on: set[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._raises_on = raises_on
        self._seen: dict[str, int] = {}

    async def call_tool(
        self, name, arguments=None, *, approved=False, network_mode="offline", session_id=None
    ):
        if name in self._raises_on:
            count = self._seen.get(name, 0) + 1
            self._seen[name] = count
            if count > 1:
                self.calls.append((name, dict(arguments or {})))
                raise MCPConnectionError(f"{name} がタイムアウトしました。")
        return await super().call_tool(
            name, arguments, approved=approved, network_mode=network_mode, session_id=session_id
        )


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

    # --- FIX1: 連鎖する前提状態の再帰的な復旧 -----------------------------

    async def test_recovers_chained_state_dependencies_recursively(self) -> None:
        """load → prep → pca の連鎖。MCP再接続で全状態が飛んだ後、
        pca を復旧しようとして prep を再実行しても、prep 自身がさらに
        load を要求する missing_state を返す。これが実際のバグ報告の再現
        （'srv::load was replay-safe with recorded args and was never tried'）で、
        再帰的な復旧がなければ最下流の load まで届かず失敗する。
        """
        registry = ChainedStateRegistry()
        loop = self.loop(registry)
        sid = self.session["id"]

        # 通常運用：load → prep → pca の順に成功させ、履歴に記録させる。
        await loop._call_and_record(
            sid, "srv::load", {"directory": "/data"}, approved=True, network_mode="offline"
        )
        await loop._call_and_record(
            sid, "srv::prep", {"normalize": "median"}, approved=True, network_mode="offline"
        )

        # MCP再接続：サーバ側の状態は全部飛ぶが、クライアント側の履歴は残る。
        registry.reset()

        result = await loop._call_and_record(
            sid, "srv::pca", {}, approved=True, network_mode="offline"
        )
        self.assertTrue(result.is_error)  # 前提が無いので missing_state で失敗する

        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )

        self.assertFalse(result.is_error)
        names = [name for name, _ in registry.calls]
        # 再接続後、load が復旧チェーンの一部として実際に呼ばれたこと
        # （旧実装ではここまで届かず prep だけで諦めていた）。
        self.assertIn("srv::load", names)
        self.assertIn(
            ("srv::load", {"directory": "/data"}),
            registry.calls,
        )
        self.assertIn(
            ("srv::prep", {"normalize": "median"}),
            registry.calls,
        )
        # 最終的に pca が再接続後の呼び出しの最後で成功していること。
        self.assertEqual(names[-1], "srv::pca")

    async def test_recovery_terminates_on_a_declared_cycle(self) -> None:
        """サーバが A⇄B の循環した前提を宣言していても、有限回で必ず止まる。"""
        registry = CycleRegistry()
        loop = self.loop(registry)
        sid = self.session["id"]

        # 「本セッションで成功した」履歴を捏造する。実際に registry を呼ぶと
        # 常に missing_state を返すフェイクなので、履歴は直接 SessionStore に
        # 書き込む（サーバが循環を宣言する状況そのものを再現するため、
        # 「一度も本当に成功したことがない」を許容する必要がある）。
        inv_a = self.store.start_tool_invocation(sid, "srv", "srv::a", {"n": 1})
        self.store.complete_tool_invocation(
            inv_a, result_message_id=None, is_error=False, result_text="ok:a"
        )
        inv_b = self.store.start_tool_invocation(sid, "srv", "srv::b", {"n": 2})
        self.store.complete_tool_invocation(
            inv_b, result_message_id=None, is_error=False, result_text="ok:b"
        )

        missing = MissingState("cyclic", ("a",), "循環した前提")
        recovered = await asyncio.wait_for(
            loop._recover_missing_state(sid, "srv::target", missing, network_mode="offline"),
            timeout=5,
        )

        self.assertIsNone(recovered)
        # depth ガードにより、深さ1つにつきちょうど1回の呼び出しで打ち切られる。
        self.assertEqual(len(registry.calls), MAX_RECOVERY_DEPTH)

    # --- FIX3: リプレイは registry.decide の判断でゲートされる -----------

    async def test_replay_is_gated_by_registry_decide_not_asserted_approval(self) -> None:
        """is_replay_safe が真でも、registry.decide が拒否すれば実行しない。

        旧実装は _call_and_record を approved=True 固定で呼んでいたため、
        ここでの decide の拒否は無視されて prep がそのまま再実行され、
        pca のリトライも成功してしまっていた（fail_once は初回だけ失敗する
        ため）。
        """
        registry = DenyingRegistry(
            denied={"srv::prep"},
            replay_safe={"srv::prep"},
            fail_once={"srv::pca": envelope("matrix", ["prep"])},
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(
            sid, "srv::prep", {"normalize": "median"}, approved=True, network_mode="offline"
        )
        result = await loop._call_and_record(
            sid, "srv::pca", {}, approved=True, network_mode="offline"
        )
        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertTrue(result.is_error)
        # prep はセットアップの1回のみ。decide に拒否された候補は再実行されない。
        self.assertEqual(sum(1 for name, _ in registry.calls if name == "srv::prep"), 1)

    # --- FIX2: リプレイ中の例外はターンを落とさず復旧失敗に縮退する -------

    async def test_an_exception_during_replay_degrades_to_the_original_error(self) -> None:
        registry = RaisingRegistry(
            raises_on={"srv::prep"},
            replay_safe={"srv::prep"},
            fail_once={"srv::pca": envelope("matrix", ["prep"])},
        )
        loop = self.loop(registry)
        sid = self.session["id"]
        await loop._call_and_record(
            sid, "srv::prep", {"normalize": "median"}, approved=True, network_mode="offline"
        )
        result = await loop._call_and_record(
            sid, "srv::pca", {}, approved=True, network_mode="offline"
        )
        # 例外はここから外に漏れてはいけない。
        result = await loop._maybe_recover_and_retry(
            sid, "srv::pca", {}, result, approved=True, network_mode="offline"
        )
        self.assertTrue(result.is_error)

    # --- FIX4: 通知メッセージはリトライの成否が分かってから書かれる -------

    async def test_recovery_message_only_claims_resolution_after_retry_succeeds(self) -> None:
        registry = RecordingRegistry(
            replay_safe={"srv::prep"},
            fail_once={"srv::pca": envelope("matrix", ["prep"])},
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
        self.assertFalse(result.is_error)
        messages = self.store.list_messages(sid)
        recovery_messages = [m["content"] for m in messages if "自動復旧" in m["content"]]
        self.assertTrue(recovery_messages)
        last = recovery_messages[-1]
        self.assertIn("成功", last)
        # 復元されたのは required_tools の1状態のみで、セッション全体では
        # ないことを明示している（全体復元を主張していない）。
        self.assertIn("復元されたのはこの状態のみ", last)
        self.assertNotIn("セッション全体が復元されました", last)

    async def test_recovery_message_does_not_overclaim_when_retry_still_fails(self) -> None:
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
        messages = self.store.list_messages(sid)
        recovery_messages = [m["content"] for m in messages if "自動復旧" in m["content"]]
        self.assertTrue(recovery_messages)
        last = recovery_messages[-1]
        self.assertNotIn("直前のエラーは解消済みです", last)
        self.assertIn("失敗", last)


if __name__ == "__main__":
    unittest.main()
