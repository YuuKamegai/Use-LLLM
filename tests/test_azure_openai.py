from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from use_lllm.core.azure_openai import (
    AzureOpenAIClient,
    AzureOpenAIConfig,
    azure_model_context_profile,
    normalize_azure_openai_endpoint,
)
from use_lllm.core.config import ConfigurationError
from use_lllm.core.mcp_client import MCPToolResult
from use_lllm.core.policy import ToolDecision, ToolSafety
from use_lllm.core.sessions import SessionStore
from use_lllm.general.agent_loop import GeneralAgentLoop


class LocalMCPRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def ollama_tools(self, query=None, *, limit=24, excluded=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": "local-lab::measure",
                    "description": "Run a local measurement",
                    "parameters": {
                        "type": "object",
                        "properties": {"sample": {"type": "string"}},
                        "required": ["sample"],
                    },
                },
            }
        ]

    def decide(self, name, *, approved=False, network_mode="offline"):
        return ToolDecision(name, ToolSafety.READ_ONLY, True, False, "read-only")

    async def call_tool(
        self,
        name,
        arguments=None,
        *,
        approved=False,
        network_mode="offline",
        session_id=None,
    ):
        self.calls.append((name, arguments or {}))
        return MCPToolResult(
            name,
            False,
            ({"type": "text", "text": "local-mcp-result"},),
            None,
        )


class AzureOpenAIConfigTests(unittest.TestCase):
    def test_gpt_54_context_profiles_match_azure_limits(self) -> None:
        mini = azure_model_context_profile("gpt-5.4-mini-2026-03-17")
        full = azure_model_context_profile("lab-gpt-5.4-kamegai")
        self.assertEqual(mini.context_window, 400_000)
        self.assertEqual(mini.max_input_tokens, 272_000)
        self.assertEqual(full.context_window, 1_050_000)
        self.assertIsNone(azure_model_context_profile("private-deployment"))

    def test_portal_and_v1_endpoints_normalize_to_one_base_url(self) -> None:
        expected = "https://sample.openai.azure.com/openai/v1"
        for value in (
            "https://sample.openai.azure.com",
            "https://sample.openai.azure.com/openai",
            "https://sample.openai.azure.com/openai/v1/",
        ):
            self.assertEqual(normalize_azure_openai_endpoint(value), expected)

    def test_foundry_endpoint_is_supported(self) -> None:
        self.assertEqual(
            normalize_azure_openai_endpoint("https://sample.services.ai.azure.com/openai/v1/"),
            "https://sample.services.ai.azure.com/openai/v1",
        )

    def test_non_azure_hosts_credentials_ports_and_unknown_paths_are_rejected(self) -> None:
        for value in (
            "https://attacker.invalid",
            "https://openai.azure.com",
            "https://sample.openai.azure.com.attacker.invalid",
            "https://nested.sample.openai.azure.com",
            "https://user:password@sample.openai.azure.com",
            "https://sample.openai.azure.com:8443",
            "https://sample.openai.azure.com/proxy",
            "https://sample.openai.azure.com?redirect=attacker.invalid",
        ):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                normalize_azure_openai_endpoint(value)


class AzureOpenAIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_general_agent_executes_local_mcp_and_returns_result_to_azure(self) -> None:
        request_payloads: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            request_payloads.append(payload)
            if len(request_payloads) == 1:
                safe_name = payload["tools"][0]["function"]["name"]
                return httpx.Response(
                    200,
                    json={
                        "model": "deployment-a",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call-local-mcp",
                                            "type": "function",
                                            "function": {
                                                "name": safe_name,
                                                "arguments": '{"sample":"A"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ],
                    },
                )
            self.assertEqual(payload["messages"][-1]["role"], "tool")
            self.assertEqual(payload["messages"][-1]["content"], "local-mcp-result")
            self.assertEqual(payload["messages"][-1]["tool_call_id"], "call-local-mcp")
            return httpx.Response(
                200,
                json={
                    "model": "deployment-a",
                    "choices": [{"message": {"role": "assistant", "content": "Azure final"}}],
                },
            )

        azure = AzureOpenAIClient(
            AzureOpenAIConfig(
                endpoint="https://sample.openai.azure.com",
                api_key="secret",
                deployment="deployment-a",
            ),
            transport=httpx.MockTransport(handler),
        )
        registry = LocalMCPRegistry()
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            session = store.create_session("azure-mcp", surface="general")

            result = await GeneralAgentLoop(azure, store, registry).chat(
                session["id"], "Run the local measurement"
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["content"], "Azure final")
        self.assertEqual(registry.calls, [("local-lab::measure", {"sample": "A"})])
        self.assertEqual(len(request_payloads), 2)

    async def test_tool_call_round_trip_maps_local_mcp_name_and_tool_call_id(self) -> None:
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["api-key"], "secret")
            payload = json.loads(request.content)
            requests.append(payload)
            safe_name = payload["tools"][0]["function"]["name"]
            self.assertNotIn("::", safe_name)
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    json={
                        "model": "deployment-a",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call-azure-1",
                                            "type": "function",
                                            "function": {
                                                "name": safe_name,
                                                "arguments": '{"directory":"C:/data"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ],
                        "usage": {"prompt_tokens": 31, "completion_tokens": 7},
                    },
                )
            assistant = payload["messages"][-2]
            tool_result = payload["messages"][-1]
            self.assertEqual(assistant["tool_calls"][0]["function"]["name"], safe_name)
            self.assertEqual(tool_result["role"], "tool")
            self.assertEqual(tool_result["tool_call_id"], "call-azure-1")
            self.assertEqual(tool_result["content"], "local-result")
            return httpx.Response(
                200,
                json={
                    "model": "deployment-a",
                    "choices": [{"message": {"role": "assistant", "content": "完了しました。"}}],
                    "usage": {"prompt_tokens": 52, "completion_tokens": 5},
                },
            )

        client = AzureOpenAIClient(
            AzureOpenAIConfig(
                endpoint="https://sample.openai.azure.com",
                api_key="secret",
                deployment="deployment-a",
            ),
            transport=httpx.MockTransport(handler),
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "ms-data-parser::list_data_files",
                    "description": "list files",
                    "parameters": {"type": "object"},
                },
            }
        ]

        first = await client.chat([{"role": "user", "content": "一覧"}], tools=tools)
        self.assertEqual(
            first.tool_calls[0]["function"]["name"],
            "ms-data-parser::list_data_files",
        )
        self.assertEqual(first.prompt_eval_count, 31)

        second = await client.chat(
            [
                {"role": "user", "content": "一覧"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": first.tool_calls,
                },
                {
                    "role": "tool",
                    "content": "local-result",
                    "tool_name": "ms-data-parser::list_data_files",
                },
            ],
            tools=tools,
        )
        self.assertEqual(second.content, "完了しました。")

    async def test_stream_accumulates_fragmented_tool_call_before_yielding_it(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.assertEqual(payload["stream_options"], {"include_usage": True})
            safe_name = payload["tools"][0]["function"]["name"]
            split = max(1, len(safe_name) // 2)
            events = [
                {
                    "model": "deployment-a",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-stream",
                                        "function": {
                                            "name": safe_name[:split],
                                            "arguments": '{"x":',
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                },
                {
                    "model": "deployment-a",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "name": safe_name[split:],
                                            "arguments": "1}",
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                },
                {
                    "model": "gpt-5.4-mini-2026-03-17",
                    "choices": [],
                    "usage": {"prompt_tokens": 91, "completion_tokens": 12},
                },
            ]
            body = "".join(f"data: {json.dumps(item)}\n\n" for item in events)
            body += "data: [DONE]\n\n"
            return httpx.Response(200, content=body.encode())

        client = AzureOpenAIClient(
            AzureOpenAIConfig(
                endpoint="https://sample.openai.azure.com/openai/v1/",
                api_key="secret",
                deployment="deployment-a",
            ),
            transport=httpx.MockTransport(handler),
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "local::peek",
                    "description": "peek",
                    "parameters": {"type": "object"},
                },
            }
        ]

        chunks = [
            item
            async for item in client.stream_chat([{"role": "user", "content": "peek"}], tools=tools)
        ]

        calls = chunks[-1]["message"]["tool_calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "local::peek")
        self.assertEqual(calls[0]["function"]["arguments"], '{"x":1}')
        self.assertEqual(chunks[-1]["prompt_eval_count"], 91)
        self.assertEqual(chunks[-1]["eval_count"], 12)
        details = await client.context_window_details()
        self.assertEqual(details["context_window"], 400_000)
        self.assertEqual(details["source"], "azure_model_profile")

    async def test_explicit_context_window_overrides_unknown_deployment(self) -> None:
        client = AzureOpenAIClient(
            AzureOpenAIConfig(
                endpoint="https://sample.openai.azure.com",
                api_key="secret",
                deployment="private-deployment",
                context_window=200_000,
            )
        )
        details = await client.context_window_details()
        self.assertEqual(details["context_window"], 200_000)
        self.assertEqual(details["source"], "azure_configured")


if __name__ == "__main__":
    unittest.main()
