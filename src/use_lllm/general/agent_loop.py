"""承認停止と再開を持つ汎用Ollama tool-callingループ。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, AsyncIterator, Protocol
from urllib.parse import quote

from use_lllm.core.audit import AuditLogger
from use_lllm.core.context_memory import ContextAssembly, ContextMemoryManager
from use_lllm.core.mcp_client import MCPToolResult
from use_lllm.core.mcp_registry import MCPRegistry
from use_lllm.core.ollama import OllamaClient
from use_lllm.core.policy import ToolSafety
from use_lllm.core.sessions import SessionStore
from use_lllm.core.tool_catalog import DESCRIBE_TOOL_NAME, ToolCatalog
from use_lllm.core.tool_result_contract import MissingState, read_missing_state

SYSTEM_PROMPT = """あなたはローカルファーストの汎用アシスタントです。
MCPツール名は server::tool 形式です。ユーザーがtool部分だけを指定した場合も、末尾が一致する名前空間付きツールを選んでください。
利用可能なMCPツールが必要な場合だけ呼び出し、1回の応答では1ツールずつ使ってください。
ツール結果、推測、未確認事項を区別し、ユーザーの承認が必要な操作を実行済みと主張しないでください。
既定は日本語で簡潔に回答してください。"""

# 1回の発話で連鎖できるツール実行の段数。脂質omicsの1手順は
# 「一覧で群を確認 → 前処理 → 差次的解析 → 描画」のように複数ツールに分かれるため、
# 6段では「進めて」の1往復ごとに上限へ当たって停止していた（general_step_limit）。
# 暴走を止める安全弁としては機能させたいので無制限にはしない。
DEFAULT_MAX_STEPS = 20

# 状態復旧の再帰段数の上限。実運用で観測される最長の前提チェーンは
# 「データ読み込み → 前処理 → 前処理済み行列に対する解析」の3段
# （読み込み → 前処理結果 → その結果を使う解析、という3種類の状態）で、
# 3段あれば末端まで復旧できる。汎用クライアントなのでチェーンの中身は
# サーバ固有の知識として持たない。サーバが循環した前提を宣言してしまった
# 場合（AがBを要求し、BがAを要求する）でも、この上限が有限回で必ず
# 再帰を止める安全弁になる。named constant として切り出しているのは、
# 正常なチェーン長が変わったときにここだけ見れば判断できるようにするため。
MAX_RECOVERY_DEPTH = 3

# MCP サーバ instructions の1サーバあたりの上限文字数。ms-data-parser は約 9k 文字。
# サーバが巨大な文字列を返してもコンテキストを食い潰さないための安全弁。
MAX_SERVER_INSTRUCTIONS_CHARS = 16_000

DEFAULT_GENERAL_SESSION_TITLE = "新しいチャット"
GENERAL_SESSION_TITLE_LIMIT = 38
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_TOOL_PNG_BYTES = 20 * 1024 * 1024
MAX_TOOL_PNG_ARTIFACTS = 4
WINDOWS_ABSOLUTE_PNG_PATH = re.compile(r'(?i)(?:[a-z]:[\\/]|\\\\)[^<>"\r\n]*?\.png')


class RegistryLike(Protocol):
    def ollama_tools(
        self,
        query: str | None = None,
        *,
        limit: int | None = None,
        excluded: set[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    def server_instructions(self) -> dict[str, str]: ...

    def decide(
        self,
        name: str,
        *,
        approved: bool = False,
        network_mode: str = "offline",
    ) -> Any: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        approved: bool = False,
        network_mode: str = "offline",
        session_id: str | None = None,
    ) -> MCPToolResult: ...

    def is_replay_safe(self, name: str) -> bool: ...

    def may_write_files(self, name: str) -> bool: ...


class GeneralAgentLoop:
    def __init__(
        self,
        ollama: OllamaClient,
        sessions: SessionStore,
        registry: MCPRegistry | RegistryLike,
        *,
        audit: AuditLogger | None = None,
        memory: ContextMemoryManager | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_stepsは1以上にしてください。")
        self.ollama = ollama
        self.sessions = sessions
        self.registry = registry
        self.audit = audit or AuditLogger(sessions)
        self.memory = memory or ContextMemoryManager(ollama, sessions)
        self.max_steps = max_steps

    def _general_session(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.get_session(session_id)
        if session.get("surface") != "general":
            raise ValueError("汎用チャット用ではないセッションです。")
        return session

    def _begin_user_message(
        self, session_id: str, message: str, metadata: dict[str, Any] | None
    ) -> str | None:
        session = self._general_session(session_id)
        if session["state"].get("pending_approval") is not None:
            raise ValueError("承認待ちのツール呼び出しを先に解決してください。")
        text = message.strip()
        if not text:
            raise ValueError("メッセージが空です。")

        new_title = None
        has_user_message = any(item["role"] == "user" for item in session["messages"])
        if session["title"] == DEFAULT_GENERAL_SESSION_TITLE and not has_user_message:
            new_title = " ".join(text.split())[:GENERAL_SESSION_TITLE_LIMIT]
            self.sessions.update_session(session_id, title=new_title)

        self.sessions.add_message(session_id, "user", text, metadata)
        self.sessions.update_session(session_id, status="running")
        return new_title

    async def _ollama_messages(
        self, session_id: str, tools: list[dict[str, Any]]
    ) -> ContextAssembly:
        tool_names = [
            str(item.get("function", {}).get("name", ""))
            for item in tools
            if item.get("function", {}).get("name")
        ]
        catalog = (
            "\n現在利用可能なツール: " + ", ".join(tool_names)
            if tool_names
            else "\n現在接続中のMCPツールはありません。"
        )
        system = SYSTEM_PROMPT + catalog + self._server_instructions_block(tool_names)
        return await self.memory.assemble(session_id, system, tools)

    def _server_instructions_block(self, tool_names: list[str]) -> str:
        """今回ツールを渡すサーバの instructions を system prompt 用に整形する。

        MCP の instructions は、入口の判定（生データか解析済み出力か）のように
        個々のツール説明には書けないサーバ全体の手順を運ぶ。これを渡さないと、
        モデルはツール名と説明だけから手順を推測するしかない。

        ツールを1つも渡していないサーバ（未接続・全ツール無効化）の手順は載せない。
        """

        provider = getattr(self.registry, "server_instructions", None)
        if provider is None:
            return ""
        offered = {self._server_name(name) for name in tool_names}
        sections = [
            f"\n### {server}\n{text[:MAX_SERVER_INSTRUCTIONS_CHARS]}"
            for server, text in provider().items()
            if server in offered
        ]
        if not sections:
            return ""
        return (
            "\n\n## MCPサーバーの利用手順\n"
            "以下は各MCPサーバー自身が提供する利用手順です。そのサーバーのツールを選ぶ順序や"
            "入口の判定に従ってください。ただしユーザーが明示した前提（例: 解析済みデータである）"
            "がある場合はそれを優先し、上記の方針にも反しないでください。" + "".join(sections)
        )

    async def _record_context_usage(
        self,
        session_id: str,
        assembled: ContextAssembly,
        *,
        model: str,
        content: str,
        metadata: dict[str, Any],
        prompt_eval_count: int | None = None,
        eval_count: int | None = None,
    ) -> dict[str, Any]:
        usage = await self.memory.context_usage(
            assembled,
            model=model,
            response_content=content,
            response_metadata=metadata,
            prompt_eval_count=prompt_eval_count,
            completion_eval_count=eval_count,
        )
        self.sessions.update_session(session_id, state_patch={"context_usage": usage})
        return usage

    @staticmethod
    def _parse_tool_call(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        function = call.get("function") or {}
        name = str(function.get("name", "")).strip()
        if not name:
            raise ValueError("Ollamaのtool callにnameがありません。")
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ValueError("Ollamaのtool引数がJSONではありません。") from exc
        if not isinstance(arguments, dict):
            raise ValueError("Ollamaのtool引数はobjectで指定してください。")
        return name, arguments

    @staticmethod
    def _tool_content(result: MCPToolResult) -> str:
        if result.structured_content is not None:
            return json.dumps(result.structured_content, ensure_ascii=False)
        if result.text:
            return result.text
        return json.dumps(result.to_dict(), ensure_ascii=False)

    @staticmethod
    def _is_describe_call(catalog: ToolCatalog, name: str) -> bool:
        if name == DESCRIBE_TOOL_NAME:
            return True
        # tool 部分だけで呼ばれた場合。同名の MCP ツールがあればそちらを優先する。
        return name == DESCRIBE_TOOL_NAME.split("::", 1)[1] and not any(
            key.split("::", 1)[-1] == name for key in catalog.full
        )

    def _answer_describe(
        self, session_id: str, catalog: ToolCatalog, arguments: dict[str, Any]
    ) -> bool:
        """要約で省いたツール説明の全文を、MCP を呼ばずにツール結果として返す。

        読み取り専用の参照なので承認も監査も不要で、解析状態の台帳
        （tool_invocations）にも載せない。tool_reference 印で結果圧縮を免れる。
        """

        content, is_error = catalog.describe(arguments)
        self.sessions.add_message(
            session_id,
            "tool",
            content,
            {"tool_name": DESCRIBE_TOOL_NAME, "is_error": is_error, "tool_reference": True},
        )
        return is_error

    @staticmethod
    def _server_name(qualified_name: str) -> str:
        return qualified_name.split("::", 1)[0]

    async def _ensure_registry_session(self, session_id: str, server_name: str) -> int:
        ensure = getattr(self.registry, "ensure_session", None)
        if ensure is None:
            return 0
        return int(await ensure(session_id, server_name))

    async def _registry_call(
        self,
        session_id: str,
        qualified_name: str,
        arguments: dict[str, Any],
        *,
        approved: bool,
        network_mode: str,
    ) -> MCPToolResult:
        return await self.registry.call_tool(
            qualified_name,
            arguments,
            approved=approved,
            network_mode=network_mode,
            session_id=session_id,
        )

    def _capture_png_artifacts(
        self,
        session_id: str,
        qualified_name: str,
        result: MCPToolResult,
    ) -> MCPToolResult:
        """Copy PNGs produced by known figure-save tools into session storage."""

        # 以前は特定サーバの図保存ツール名を直接3件持っていた。
        # 汎用クライアントとして、サーバ自身の annotations だけで判断する。
        # 読み取り専用ツールの出力にたまたま現れたパスは拾わない。
        if result.is_error or not self.registry.may_write_files(qualified_name):
            return result

        tool_name = qualified_name.rsplit("::", 1)[-1]

        blocks = list(result.content)
        captured_hashes = {
            str(block.get("sha256", ""))
            for block in blocks
            if block.get("type") == "artifact_image"
        }
        captured_count = 0
        for match in WINDOWS_ABSOLUTE_PNG_PATH.finditer(result.text):
            if captured_count >= MAX_TOOL_PNG_ARTIFACTS:
                break
            source = Path(match.group(0).strip().rstrip("'"))
            try:
                size = source.stat().st_size
                if not source.is_file() or size <= 0 or size > MAX_TOOL_PNG_BYTES:
                    continue
                data = source.read_bytes()
            except OSError:
                continue
            if len(data) != size or not data.startswith(PNG_SIGNATURE):
                continue

            artifact = self.sessions.save_artifact(session_id, source.name, data)
            if artifact["sha256"] in captured_hashes:
                continue
            captured_hashes.add(artifact["sha256"])
            artifact_name = Path(artifact["path"]).name
            blocks.append(
                {
                    "type": "artifact_image",
                    "mimeType": "image/png",
                    "name": source.name,
                    "alt": f"{tool_name} generated image",
                    "url": (
                        f"./api/sessions/{quote(session_id, safe='')}/artifacts/"
                        f"{quote(artifact_name, safe='')}"
                    ),
                    "sha256": artifact["sha256"],
                    "size": artifact["size"],
                }
            )
            captured_count += 1

        if tuple(blocks) == result.content:
            return result
        return MCPToolResult(
            result.tool_name,
            result.is_error,
            tuple(blocks),
            result.structured_content,
        )

    async def _call_and_record(
        self,
        session_id: str,
        qualified_name: str,
        arguments: dict[str, Any],
        *,
        approved: bool,
        network_mode: str,
        replay_of_id: int | None = None,
        add_message: bool = True,
    ) -> MCPToolResult:
        server_name = self._server_name(qualified_name)
        generation = await self._ensure_registry_session(session_id, server_name)
        invocation_id = self.sessions.start_tool_invocation(
            session_id,
            server_name,
            qualified_name,
            arguments,
            connection_generation=generation,
            replay_of_id=replay_of_id,
        )
        try:
            result = await self._registry_call(
                session_id,
                qualified_name,
                arguments,
                approved=approved,
                network_mode=network_mode,
            )
            result = self._capture_png_artifacts(session_id, qualified_name, result)
            content = self._tool_content(result)
            if not result.is_error and read_missing_state(content) is not None:
                # 契約上のエラー。サーバは isError を立てられない（SDK 制約）ので
                # クライアント側で立て直し、監査と LLM に「失敗」として見せる。
                result = MCPToolResult(
                    result.tool_name, True, result.content, result.structured_content
                )
            message_id = None
            if add_message:
                message_id = self.sessions.add_message(
                    session_id,
                    "tool",
                    content,
                    {
                        "tool_name": qualified_name,
                        "is_error": result.is_error,
                        "content_blocks": list(result.content),
                        "structured_content": result.structured_content,
                    },
                )
            self.sessions.complete_tool_invocation(
                invocation_id,
                result_message_id=message_id,
                is_error=result.is_error,
                result_text=content,
            )
            return result
        except Exception as exc:
            self.sessions.fail_tool_invocation(invocation_id, str(exc))
            raise

    def _replay_source(self, session_id: str, qualified_name: str) -> dict[str, Any] | None:
        """本セッションで成功した、その名前の最後の呼び出しを返す。"""
        found = None
        for item in self.sessions.list_tool_invocations(session_id, include_replays=False):
            if (
                item.get("tool_name") == qualified_name
                and item.get("status") == "complete"
                and not item.get("is_error")
            ):
                found = item
        return found

    async def _safe_replay_call(
        self,
        session_id: str,
        target: str,
        arguments: dict[str, Any],
        replay_of_id: int,
        network_mode: str,
    ) -> MCPToolResult | None:
        """リプレイ実行を1回行う。例外はここで飲み込み、None を返す（FIX2）。

        MCPConnectionError（タイムアウトなど）や ToolPolicyError が漏れると、
        呼び出し元の _drive / chat() まで伝播してターン全体を落としてしまう。
        復旧の失敗は「復旧できなかった」という結果であって、ターンを止める
        理由にはしない。失敗はイベントとして記録し、呼び出し元には None を
        返して次の候補（またはあきらめ）に委ねる。

        approved=True を渡す。ここへ到達するのは呼び出し元が
        「安全クラス READ_ONLY」かつ「本セッションで成功実績あり」に絞った候補だけで
        （FIX3）、その2条件はユーザーが既にこの呼び出しの実行を許可したことを意味する。
        同じ引数での再実行に改めて承認を求める先がループの途中には無いため、ここでは
        承認済みとして扱う。書き込み系・破壊系・ネットワーク系は呼び出し元の
        安全クラス判定で既に除かれている。
        """
        try:
            return await self._call_and_record(
                session_id,
                target,
                arguments,
                approved=True,
                network_mode=network_mode,
                replay_of_id=replay_of_id,
                add_message=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 復旧経路の例外は全て「復旧失敗」に縮退させる
            self.sessions.append_event(
                session_id,
                "mcp_state_recovery_error",
                {"tool": target, "error": str(exc)},
                status="failed",
            )
            return None

    async def _replay_producer(
        self,
        session_id: str,
        target: str,
        source: dict[str, Any],
        *,
        network_mode: str,
        depth: int,
    ) -> MCPToolResult | None:
        """候補を1つ再実行する。自身も missing_state を返したら、depth+1 で
        その前提を再帰的に復旧してから、この候補をもう一度だけ再実行する
        （連鎖する前提状態の復旧。FIX1）。

        再帰の停止は呼び出し先の _recover_missing_state の depth ガードが
        保証する。ここでは深さを1つ増やして渡すだけでよい。
        """
        arguments = dict(source["arguments"])
        replay_of_id = int(source["id"])
        replayed = await self._safe_replay_call(
            session_id, target, arguments, replay_of_id, network_mode
        )
        if replayed is None or not replayed.is_error:
            return replayed

        inner_missing = read_missing_state(self._tool_content(replayed))
        if inner_missing is None:
            return replayed  # ただのエラー。連鎖ではないので復旧しない。

        inner_recovered = await self._recover_missing_state(
            session_id, target, inner_missing, network_mode=network_mode, depth=depth + 1
        )
        if inner_recovered is None:
            return replayed  # 前提の復旧に失敗。この候補はあきらめる。

        # 前提が復旧できたので、この候補をもう一度だけ再実行する。
        return await self._safe_replay_call(
            session_id, target, arguments, replay_of_id, network_mode
        )

    async def _recover_missing_state(
        self,
        session_id: str,
        qualified_name: str,
        missing: MissingState,
        *,
        network_mode: str,
        depth: int = 0,
    ) -> str | None:
        """required_tools の候補を順に試して状態を復元する。復元できたツール名を返す。

        required_tools は OR の代替候補。サーバが宣言した annotations で
        リプレイ安全性を判断し、引数はセッション履歴に記録されたものをそのまま使う。
        LLM に再実行させると引数が変わって解析条件が黙って変わりうるため、
        引数の同一性はここで担保する。

        候補が複数あり得るケースでは、リスト順ではなく本セッションで
        最後に成功した呼び出し（invocation id が最大のもの）を選ぶ。ユーザーが
        直前に見ていたのはその状態のはずで、より古い候補を復元すると
        別の状態を「同じもの」として見せてしまう。

        候補は**安全クラスが READ_ONLY のものだけ**に絞る（FIX3）。
        read-only 以外（書き込み・破壊・外部ネットワーク）は、サーバが
        required_tools に名指ししてきても実行しない。悪意ある/壊れたサーバが
        「この状態を作るには write_report を呼べ」と宣言してもリプレイされない。

        判定に decide(...).allowed を使ってはいけない。allowed は read_only_auto
        （＝**モデルが選んだ**呼び出しを承認なしで走らせるか）に従うため、既定の
        read_only_auto=False では read-only 候補まで弾かれ、復旧機構が一度も
        発火しなくなる。リプレイはモデルが選んだ呼び出しではなく、**ユーザーが
        既に本セッションで実行を許可した呼び出し**（成功実績が候補の前提条件）を
        同じ引数でコードが復元する行為なので、別の判断軸で扱う。
        書き込み系を止める役目は allowed ではなく安全クラスが担う。

        候補自身が missing_state を返した場合は、depth を1つ増やして
        自分自身を再帰的に呼び出し、その前提を復旧してから候補を再実行する
        （FIX1）。depth が MAX_RECOVERY_DEPTH に達したら、サーバが循環した
        前提を宣言していても必ずここで打ち切る。
        """
        if depth >= MAX_RECOVERY_DEPTH:
            return None
        server_name = self._server_name(qualified_name)
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for bare in missing.required_tools:
            target = f"{server_name}::{bare}"
            if not self.registry.is_replay_safe(target):
                continue
            source = self._replay_source(session_id, target)
            if source is None:
                continue
            candidates.append((int(source["id"]), target, source))
        candidates.sort(key=lambda item: item[0], reverse=True)

        for _, target, source in candidates:
            # 安全クラスだけで判定する。allowed を見ると read_only_auto=False（既定）で
            # read-only 候補まで弾かれ、復旧が永久に発火しない。docstring 参照。
            decision = self.registry.decide(target, approved=False, network_mode=network_mode)
            if decision.safety is not ToolSafety.READ_ONLY:
                continue
            replayed = await self._replay_producer(
                session_id, target, source, network_mode=network_mode, depth=depth
            )
            if replayed is not None and not replayed.is_error:
                return target
        return None

    async def _maybe_recover_and_retry(
        self,
        session_id: str,
        qualified_name: str,
        arguments: dict[str, Any],
        result: MCPToolResult,
        *,
        approved: bool,
        network_mode: str,
    ) -> MCPToolResult:
        """missing_state なら状態を復元して1回だけリトライする。

        リトライは1段のみ。リトライ後の結果はこの関数を通らないので、
        復旧の復旧は起きない（_recover_missing_state 内部の連鎖復旧とは別物）。

        リトライの実行そのものも例外を飲み込む（FIX2）。復旧が成功しても
        続く再実行がタイムアウト等で例外を投げれば、ターンを落とさず
        「復旧は試みたが再実行できなかった」結果に縮退させる。

        通知メッセージは、リトライの成否が分かってから初めて書く（FIX4）。
        「直前のエラーは解消済みです」のような、リトライ前に解決を主張する
        文言は、リトライが失敗した場合に会話履歴へ偽の事実を残してしまう。
        また「復旧できたのは required_tools に挙げられた1つの状態だけ」で
        あり、セッション全体が復元されたわけではないことも明示する。
        """
        if not result.is_error:
            return result
        missing = read_missing_state(self._tool_content(result))
        if missing is None:
            return result
        recovered = await self._recover_missing_state(
            session_id, qualified_name, missing, network_mode=network_mode
        )
        if recovered is None:
            self.sessions.append_event(
                session_id,
                "mcp_state_recovery_failed",
                {
                    "tool": qualified_name,
                    "state": missing.state,
                    "required_tools": list(missing.required_tools),
                },
                status="skipped",
            )
            return result
        self.sessions.append_event(
            session_id,
            "mcp_state_recovered",
            {"tool": qualified_name, "state": missing.state, "replayed": recovered},
        )
        try:
            retried = await self._call_and_record(
                session_id, qualified_name, arguments, approved=approved, network_mode=network_mode
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - リトライの例外もターンを落とさず縮退させる
            self.sessions.append_event(
                session_id,
                "mcp_state_recovery_error",
                {"tool": qualified_name, "error": str(exc)},
                status="failed",
            )
            self.sessions.add_message(
                session_id,
                "tool",
                f"[自動復旧] {recovered} の状態のみ記録済みの引数で再実行して復元しましたが、"
                f"続く {qualified_name} の再実行中に例外が発生したため中断しました: {exc}",
                {"tool_name": qualified_name, "recovery": True, "recovery_succeeded": False},
            )
            return result

        if retried.is_error:
            self.sessions.add_message(
                session_id,
                "tool",
                f"[自動復旧] {recovered} の状態のみ記録済みの引数で再実行して復元しましたが、"
                f"{qualified_name} の再実行は依然として失敗しました。直前のエラーは解消して"
                "いません。",
                {"tool_name": qualified_name, "recovery": True, "recovery_succeeded": False},
            )
        else:
            self.sessions.add_message(
                session_id,
                "tool",
                f"[自動復旧] {recovered} の状態のみ記録済みの引数で再実行して復元し、"
                f"続く {qualified_name} の再実行に成功しました。セッション全体が復元された"
                "わけではなく、復元されたのはこの状態のみです。",
                {"tool_name": qualified_name, "recovery": True, "recovery_succeeded": True},
            )
        return retried

    async def chat(
        self, session_id: str, message: str, *, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._begin_user_message(session_id, message, metadata)
        try:
            return await self._drive(session_id)
        except asyncio.CancelledError:
            self.sessions.update_session(session_id, status="ready")
            raise
        except Exception as exc:
            self.sessions.update_session(session_id, status="error")
            self.sessions.append_event(
                session_id,
                "general_chat_failed",
                {"error": str(exc)},
                status="failed",
            )
            raise

    async def chat_stream(
        self, session_id: str, message: str, *, metadata: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        new_title = self._begin_user_message(session_id, message, metadata)
        status_event = {"type": "status", "status": "thinking"}
        if new_title is not None:
            status_event["session_title"] = new_title
        yield status_event
        try:
            streamer = getattr(self.ollama, "stream_chat", None)
            if streamer is None:
                result = await self._drive(session_id)
                yield {"type": result["status"], **result}
                return
            async for item in self._drive_stream(session_id):
                yield item
        except asyncio.CancelledError:
            self.sessions.update_session(session_id, status="ready")
            self.sessions.append_event(
                session_id,
                "general_chat_cancelled",
                {},
                status="cancelled",
            )
            raise
        except Exception as exc:
            self.sessions.update_session(session_id, status="error")
            self.sessions.append_event(
                session_id,
                "general_chat_failed",
                {"error": str(exc)},
                status="failed",
            )
            raise

    async def _drive_stream(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """Run the tool loop while forwarding model and tool progress to the WebUI."""

        network_mode = str(
            self._general_session(session_id)["state"].get("network_mode", "offline")
        )
        user_messages = [
            item["content"]
            for item in self.sessions.list_messages(session_id)
            if item["role"] == "user"
        ]
        query = user_messages[-1] if user_messages else ""
        excluded = set(self._general_session(session_id)["state"].get("disabled_tools", []))
        catalog = ToolCatalog.build(
            self.registry.ollama_tools(query=query, excluded=excluded)
            if excluded
            else self.registry.ollama_tools(query=query)
        )
        tools = catalog.tools
        for step in range(1, self.max_steps + 1):
            content_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            seen_calls: set[str] = set()
            model = "unknown"
            prompt_eval_count: int | None = None
            eval_count: int | None = None
            assembled = await self._ollama_messages(session_id, tools)
            async for chunk in self.ollama.stream_chat(
                assembled.messages,
                tools=tools,
                temperature=0.2,
            ):
                model = str(chunk.get("model") or model)
                if isinstance(chunk.get("prompt_eval_count"), int):
                    prompt_eval_count = int(chunk["prompt_eval_count"])
                if isinstance(chunk.get("eval_count"), int):
                    eval_count = int(chunk["eval_count"])
                chunk_message = chunk.get("message") or {}
                if not isinstance(chunk_message, dict):
                    continue
                delta = str(chunk_message.get("content") or "")
                if delta:
                    content_parts.append(delta)
                    yield {"type": "delta", "content": delta, "step": step}
                calls = chunk_message.get("tool_calls") or []
                if isinstance(calls, list):
                    for call in calls:
                        if not isinstance(call, dict):
                            continue
                        key = json.dumps(call, ensure_ascii=False, sort_keys=True)
                        if key not in seen_calls:
                            seen_calls.add(key)
                            tool_calls.append(call)

            content = "".join(content_parts).strip()
            response_metadata: dict[str, Any] = {"model": model, "step": step}
            if prompt_eval_count is not None:
                response_metadata["prompt_eval_count"] = prompt_eval_count
            if eval_count is not None:
                response_metadata["eval_count"] = eval_count
            if not tool_calls:
                self.sessions.add_message(
                    session_id,
                    "assistant",
                    content,
                    response_metadata,
                )
                context_usage = await self._record_context_usage(
                    session_id,
                    assembled,
                    model=model,
                    content=content,
                    metadata=response_metadata,
                    prompt_eval_count=prompt_eval_count,
                    eval_count=eval_count,
                )
                self.sessions.update_session(session_id, status="ready")
                yield {
                    "type": "complete",
                    "status": "complete",
                    "content": content,
                    "model": model,
                    "step": step,
                    "context_usage": context_usage,
                }
                return

            call = tool_calls[0]
            qualified_name, arguments = self._parse_tool_call(call)
            self.sessions.add_message(
                session_id,
                "assistant",
                content,
                {**response_metadata, "tool_calls": [call]},
            )
            await self._record_context_usage(
                session_id,
                assembled,
                model=model,
                content=content,
                metadata={**response_metadata, "tool_calls": [call]},
                prompt_eval_count=prompt_eval_count,
                eval_count=eval_count,
            )
            if self._is_describe_call(catalog, qualified_name):
                is_error = self._answer_describe(session_id, catalog, arguments)
                yield {
                    "type": "tool_started",
                    "tool": DESCRIBE_TOOL_NAME,
                    "arguments": arguments,
                    "step": step,
                }
                yield {
                    "type": "tool_result",
                    "tool": DESCRIBE_TOOL_NAME,
                    "is_error": is_error,
                    "step": step,
                }
                continue
            decision = self.registry.decide(qualified_name, network_mode=network_mode)
            decision_event = self.audit.record_tool_decision(
                session_id, qualified_name, arguments, decision
            )
            if not decision.allowed:
                event_id = self.sessions.append_event(
                    session_id,
                    "general_tool_approval",
                    {
                        "qualified_name": qualified_name,
                        "arguments": arguments,
                        "decision": decision.to_dict(),
                        "step": step,
                    },
                    status="pending",
                    parent_event_id=decision_event,
                )
                self.sessions.update_session(
                    session_id,
                    status="awaiting_approval",
                    state_patch={"pending_approval": event_id},
                )
                yield {
                    "type": "approval_required",
                    "status": "approval_required",
                    "approval": {
                        "event_id": event_id,
                        "qualified_name": qualified_name,
                        "arguments": arguments,
                        "reason": decision.reason,
                    },
                }
                return

            yield {
                "type": "tool_started",
                "tool": qualified_name,
                "arguments": arguments,
                "step": step,
            }
            result = await self._call_and_record(
                session_id,
                qualified_name,
                arguments,
                approved=False,
                network_mode=network_mode,
            )
            result = await self._maybe_recover_and_retry(
                session_id,
                qualified_name,
                arguments,
                result,
                approved=False,
                network_mode=network_mode,
            )
            self.audit.record_tool_result(
                session_id,
                qualified_name,
                is_error=result.is_error,
                content_blocks=len(result.content),
                has_structured_content=result.structured_content is not None,
                parent_event_id=decision_event,
            )
            yield {
                "type": "tool_result",
                "tool": qualified_name,
                "is_error": result.is_error,
                "step": step,
            }

        self.sessions.append_event(
            session_id,
            "general_step_limit",
            {"max_steps": self.max_steps},
            status="failed",
        )
        self.sessions.update_session(session_id, status="paused")
        yield {
            "type": "step_limit",
            "status": "step_limit",
            "content": "ツール実行の段階上限に達したため停止しました。",
            "step": self.max_steps,
        }

    async def _drive(self, session_id: str) -> dict[str, Any]:
        network_mode = str(
            self._general_session(session_id)["state"].get("network_mode", "offline")
        )
        user_messages = [
            item["content"]
            for item in self.sessions.list_messages(session_id)
            if item["role"] == "user"
        ]
        query = user_messages[-1] if user_messages else ""
        excluded = set(self._general_session(session_id)["state"].get("disabled_tools", []))
        catalog = ToolCatalog.build(
            self.registry.ollama_tools(query=query, excluded=excluded)
            if excluded
            else self.registry.ollama_tools(query=query)
        )
        tools = catalog.tools
        for step in range(1, self.max_steps + 1):
            assembled = await self._ollama_messages(session_id, tools)
            response = await self.ollama.chat(
                assembled.messages,
                tools=tools,
                temperature=0.2,
            )
            calls = response.tool_calls
            response_metadata: dict[str, Any] = {"model": response.model, "step": step}
            if response.prompt_eval_count is not None:
                response_metadata["prompt_eval_count"] = response.prompt_eval_count
            if response.eval_count is not None:
                response_metadata["eval_count"] = response.eval_count
            if not calls:
                content = response.content.strip()
                self.sessions.add_message(
                    session_id,
                    "assistant",
                    content,
                    response_metadata,
                )
                context_usage = await self._record_context_usage(
                    session_id,
                    assembled,
                    model=response.model,
                    content=content,
                    metadata=response_metadata,
                    prompt_eval_count=response.prompt_eval_count,
                    eval_count=response.eval_count,
                )
                self.sessions.update_session(session_id, status="ready")
                return {
                    "status": "complete",
                    "content": content,
                    "step": step,
                    "context_usage": context_usage,
                }

            call = calls[0]
            qualified_name, arguments = self._parse_tool_call(call)
            self.sessions.add_message(
                session_id,
                "assistant",
                response.content,
                {
                    **response_metadata,
                    "tool_calls": [call],
                },
            )
            await self._record_context_usage(
                session_id,
                assembled,
                model=response.model,
                content=response.content,
                metadata={**response_metadata, "tool_calls": [call]},
                prompt_eval_count=response.prompt_eval_count,
                eval_count=response.eval_count,
            )
            if self._is_describe_call(catalog, qualified_name):
                self._answer_describe(session_id, catalog, arguments)
                continue
            decision = self.registry.decide(qualified_name, network_mode=network_mode)
            decision_event = self.audit.record_tool_decision(
                session_id, qualified_name, arguments, decision
            )
            if not decision.allowed:
                event_id = self.sessions.append_event(
                    session_id,
                    "general_tool_approval",
                    {
                        "qualified_name": qualified_name,
                        "arguments": arguments,
                        "decision": decision.to_dict(),
                        "step": step,
                    },
                    status="pending",
                    parent_event_id=decision_event,
                )
                self.sessions.update_session(
                    session_id,
                    status="awaiting_approval",
                    state_patch={"pending_approval": event_id},
                )
                return {
                    "status": "approval_required",
                    "approval": {
                        "event_id": event_id,
                        "qualified_name": qualified_name,
                        "arguments": arguments,
                        "reason": decision.reason,
                    },
                }

            result = await self._call_and_record(
                session_id,
                qualified_name,
                arguments,
                approved=False,
                network_mode=network_mode,
            )
            result = await self._maybe_recover_and_retry(
                session_id,
                qualified_name,
                arguments,
                result,
                approved=False,
                network_mode=network_mode,
            )
            self.audit.record_tool_result(
                session_id,
                qualified_name,
                is_error=result.is_error,
                content_blocks=len(result.content),
                has_structured_content=result.structured_content is not None,
                parent_event_id=decision_event,
            )

        self.sessions.append_event(
            session_id,
            "general_step_limit",
            {"max_steps": self.max_steps},
            status="failed",
        )
        self.sessions.update_session(session_id, status="paused")
        return {
            "status": "step_limit",
            "content": "ツール実行の段数上限に達したため停止しました。",
            "step": self.max_steps,
        }

    async def _apply_approval(
        self, session_id: str, event_id: int, *, approved: bool
    ) -> MCPToolResult | None:
        """承認待ちのツールを実行（または拒否を記録）し、続きを回せる状態にする。"""

        session = self._general_session(session_id)
        event = self.sessions.get_event(session_id, event_id)
        if event["kind"] != "general_tool_approval" or event["status"] != "pending":
            raise ValueError("実行待ちの汎用ツール承認ではありません。")
        if session["state"].get("pending_approval") != event_id:
            raise ValueError("セッションの承認待ち状態と一致しません。")
        payload = event["payload"]
        name = str(payload["qualified_name"])
        arguments = dict(payload.get("arguments", {}))
        network_mode = str(session["state"].get("network_mode", "offline"))
        self.sessions.record_approval(
            session_id,
            name,
            {"argument_keys": sorted(arguments)},
            approved,
        )

        if approved:
            decision = self.registry.decide(name, approved=True, network_mode=network_mode)
            decision_event = self.audit.record_tool_decision(session_id, name, arguments, decision)
            result = await self._call_and_record(
                session_id,
                name,
                arguments,
                approved=True,
                network_mode=network_mode,
            )
            result = await self._maybe_recover_and_retry(
                session_id,
                name,
                arguments,
                result,
                approved=True,
                network_mode=network_mode,
            )
            self.audit.record_tool_result(
                session_id,
                name,
                is_error=result.is_error,
                content_blocks=len(result.content),
                has_structured_content=result.structured_content is not None,
                parent_event_id=decision_event,
            )
            status = "complete"
        else:
            self.sessions.add_message(
                session_id,
                "tool",
                "ユーザーがこのツール呼び出しを拒否しました。",
                {"tool_name": name, "rejected": True},
            )
            status = "cancelled"

        self.sessions.complete_event(event_id, status, {**payload, "approved": approved})
        self.sessions.update_session(
            session_id,
            status="running",
            state_patch={"pending_approval": None},
        )
        return result if approved else None

    async def resolve_approval(
        self, session_id: str, event_id: int, *, approved: bool
    ) -> dict[str, Any]:
        await self._apply_approval(session_id, event_id, approved=approved)
        return await self._drive(session_id)

    async def resolve_approval_stream(
        self, session_id: str, event_id: int, *, approved: bool
    ) -> AsyncIterator[dict[str, Any]]:
        """承認後の続きを、通常の送信と同じイベント列で逐次返す。

        承認後もモデルは何段もツールを呼び続けうる（ローカル LLM では 1 段数十秒）。
        単発の応答で返すと、その間 UI に経過が出ず停止もできないため、
        承認したツールの実行から以降の段まで chat_stream と同じ形で流す。
        """

        payload = self.sessions.get_event(session_id, event_id)["payload"]
        name = str(payload.get("qualified_name", ""))
        try:
            if approved:
                yield {
                    "type": "tool_started",
                    "tool": name,
                    "arguments": dict(payload.get("arguments", {})),
                    "step": 0,
                }
            yield {"type": "status", "status": "executing"}
            result = await self._apply_approval(session_id, event_id, approved=approved)
            if result is not None:
                yield {
                    "type": "tool_result",
                    "tool": name,
                    "is_error": result.is_error,
                    "step": 0,
                }
            yield {"type": "status", "status": "thinking"}
            if getattr(self.ollama, "stream_chat", None) is None:
                final = await self._drive(session_id)
                yield {"type": final["status"], **final}
                return
            async for item in self._drive_stream(session_id):
                yield item
        except asyncio.CancelledError:
            self.sessions.update_session(session_id, status="ready")
            self.sessions.append_event(
                session_id,
                "general_chat_cancelled",
                {},
                status="cancelled",
            )
            raise
        except Exception as exc:
            self.sessions.update_session(session_id, status="error")
            self.sessions.append_event(
                session_id,
                "general_chat_failed",
                {"error": str(exc)},
                status="failed",
            )
            raise
