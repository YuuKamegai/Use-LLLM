"""承認停止と再開を持つ汎用Ollama tool-callingループ。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Protocol

from use_lllm.core.audit import AuditLogger
from use_lllm.core.context_memory import ContextAssembly, ContextMemoryManager
from use_lllm.core.mcp_client import MCPToolResult
from use_lllm.core.mcp_registry import MCPRegistry
from use_lllm.core.mcp_state_policy import (
    StateRestoreBlocked,
    build_replay_plan,
    indicates_missing_state,
    rule_for,
)
from use_lllm.core.ollama import OllamaClient
from use_lllm.core.sessions import SessionStore

SYSTEM_PROMPT = """あなたはローカルファーストの汎用アシスタントです。
MCPツール名は server::tool 形式です。ユーザーがtool部分だけを指定した場合も、末尾が一致する名前空間付きツールを選んでください。
利用可能なMCPツールが必要な場合だけ呼び出し、1回の応答では1ツールずつ使ってください。
ツール結果、推測、未確認事項を区別し、ユーザーの承認が必要な操作を実行済みと主張しないでください。
既定は日本語で簡潔に回答してください。"""

DEFAULT_GENERAL_SESSION_TITLE = "新しいチャット"
GENERAL_SESSION_TITLE_LIMIT = 38


class RegistryLike(Protocol):
    def ollama_tools(
        self, query: str | None = None, *, limit: int = 24, excluded: set[str] | None = None
    ) -> list[dict[str, Any]]: ...

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


class GeneralAgentLoop:
    def __init__(
        self,
        ollama: OllamaClient,
        sessions: SessionStore,
        registry: MCPRegistry | RegistryLike,
        *,
        audit: AuditLogger | None = None,
        memory: ContextMemoryManager | None = None,
        max_steps: int = 6,
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
        return await self.memory.assemble(session_id, SYSTEM_PROMPT + catalog, tools)

    async def _record_context_usage(
        self,
        session_id: str,
        assembled: ContextAssembly,
        *,
        model: str,
        content: str,
        metadata: dict[str, Any],
        prompt_eval_count: int | None = None,
    ) -> dict[str, Any]:
        usage = await self.memory.context_usage(
            assembled,
            model=model,
            response_content=content,
            response_metadata=metadata,
            prompt_eval_count=prompt_eval_count,
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
            content = self._tool_content(result)
            if not result.is_error and indicates_missing_state(qualified_name, content):
                result = MCPToolResult(
                    result.tool_name,
                    True,
                    result.content,
                    result.structured_content,
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

    async def _restore_mcp_state(
        self,
        session_id: str,
        target_tool: str,
        *,
        network_mode: str,
    ) -> bool:
        server_name = self._server_name(target_tool)
        generation = await self._ensure_registry_session(session_id, server_name)
        if generation == 0:
            return True
        session = self._general_session(session_id)
        generations = dict(session["state"].get("mcp_generations", {}))
        if int(generations.get(server_name, 0)) == generation:
            return True
        if not rule_for(target_tool).requires:
            return True
        invocations = self.sessions.list_tool_invocations(session_id, include_replays=False)
        try:
            plan = build_replay_plan(target_tool, invocations)
        except StateRestoreBlocked as exc:
            self.sessions.append_event(
                session_id,
                "mcp_state_restore_blocked",
                {"tool": target_tool, "reason": str(exc)},
                status="skipped",
            )
            return False
        replayed: list[int] = []
        for invocation in plan:
            replay_tool = str(invocation["tool_name"])
            if not rule_for(replay_tool).replay_safe:
                raise StateRestoreBlocked(f"安全に再実行できないMCPツールです: {replay_tool}")
            await self._call_and_record(
                session_id,
                replay_tool,
                dict(invocation["arguments"]),
                approved=True,
                network_mode=network_mode,
                replay_of_id=int(invocation["id"]),
                add_message=False,
            )
            replayed.append(int(invocation["id"]))
        generations[server_name] = generation
        self.sessions.update_session(session_id, state_patch={"mcp_generations": generations})
        if replayed:
            self.sessions.append_event(
                session_id,
                "mcp_state_restored",
                {
                    "server_name": server_name,
                    "generation": generation,
                    "replayed_invocation_ids": replayed,
                },
            )
        return True

    def _mark_mcp_state_live(self, session_id: str, qualified_name: str, generation: int) -> None:
        if generation == 0:
            return
        server_name = self._server_name(qualified_name)
        session = self._general_session(session_id)
        generations = dict(session["state"].get("mcp_generations", {}))
        generations[server_name] = generation
        self.sessions.update_session(session_id, state_patch={"mcp_generations": generations})

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
        tools = (
            self.registry.ollama_tools(query=query, excluded=excluded)
            if excluded
            else self.registry.ollama_tools(query=query)
        )
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
            )
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
            state_ready = await self._restore_mcp_state(
                session_id, qualified_name, network_mode=network_mode
            )
            result = await self._call_and_record(
                session_id,
                qualified_name,
                arguments,
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
            if not result.is_error and (state_ready or not rule_for(qualified_name).requires):
                generation = await self._ensure_registry_session(
                    session_id, self._server_name(qualified_name)
                )
                self._mark_mcp_state_live(session_id, qualified_name, generation)
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
        tools = (
            self.registry.ollama_tools(query=query, excluded=excluded)
            if excluded
            else self.registry.ollama_tools(query=query)
        )
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
            )
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

            state_ready = await self._restore_mcp_state(
                session_id, qualified_name, network_mode=network_mode
            )
            result = await self._call_and_record(
                session_id,
                qualified_name,
                arguments,
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
            if not result.is_error and (state_ready or not rule_for(qualified_name).requires):
                generation = await self._ensure_registry_session(
                    session_id, self._server_name(qualified_name)
                )
                self._mark_mcp_state_live(session_id, qualified_name, generation)

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

    async def resolve_approval(
        self, session_id: str, event_id: int, *, approved: bool
    ) -> dict[str, Any]:
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
            state_ready = await self._restore_mcp_state(session_id, name, network_mode=network_mode)
            result = await self._call_and_record(
                session_id,
                name,
                arguments,
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
            if not result.is_error and (state_ready or not rule_for(name).requires):
                generation = await self._ensure_registry_session(
                    session_id, self._server_name(name)
                )
                self._mark_mcp_state_live(session_id, name, generation)
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
        return await self._drive(session_id)

    async def resolve_approval_stream(
        self, session_id: str, event_id: int, *, approved: bool
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "status", "status": "executing"}
        result = await self.resolve_approval(session_id, event_id, approved=approved)
        yield {"type": result["status"], **result}
