"""Isolated synthetic fixture for the optional Playwright UI smoke test."""

import base64
import json
import sys
import tempfile
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import uvicorn

from tests.test_general_agent import response
from tests.test_mcp_registry import FakeSession, FakeTool
from tests.test_web_agent import StreamingModel
from use_lllm.core.mcp_registry import MCPRegistry
from use_lllm.core.sessions import SessionStore
from use_lllm.core.settings_store import MCPServerSpec, default_settings, save_settings
from use_lllm.core.setup_store import SetupStore
from use_lllm.general.api import create_general_app


def main():
    with tempfile.TemporaryDirectory(prefix="use-lllm-ui-") as td:
        store = SessionStore(Path(td))
        SetupStore(Path(td)).mark_complete(endpoint="local", model="fake")
        session = store.create_session("テスト会話", surface="general")
        sid = session["id"]
        store.add_message(sid, "user", "接続と設定")
        store.add_message(
            sid,
            "assistant",
            "# Hello\n**Markdown** $$x^2$$\n```python\nprint('hello')\n```\n<script>window.injected=true</script>",
        )
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9AAAAABJRU5ErkJggg=="
        )
        artifact = store.save_artifact(sid, "result.png", png)
        store.add_message(
            sid,
            "tool",
            json.dumps(
                {
                    "pca_scores": [
                        {"sample_name": "A", "PC1": 1, "PC2": -1, "group": "control"},
                        {"sample_name": "B", "PC1": 2, "PC2": 1, "group": "treated"},
                    ]
                }
            ),
            {
                "content_blocks": [
                    {
                        "type": "artifact_image",
                        "url": f"./api/sessions/{sid}/artifacts/{Path(artifact['path']).name}",
                    }
                ]
            },
        )
        store.append_event(sid, "test-history", {"value": "complete history"})

        @asynccontextmanager
        async def fixture_session(spec):
            yield FakeSession(
                [FakeTool("write_report", {"type": "object"}, annotations={"readOnlyHint": False})]
            )

        class FixtureModel(StreamingModel):
            async def health(self):
                return {"status": "ready", "selected_model": "fake", "models": ["fake"]}

        model = FixtureModel(
            [
                response(tool="web::search", arguments={"query": "public evidence"}),
                response(tool="web::read_page", arguments={"url": "https://example.org/"}),
                response(tool="fixture::write_report", arguments={}),
                response("本文を確認しました。[出典](https://example.org/)（合成テスト）"),
            ]
        )
        save_settings(
            store.base_directory / "settings.json",
            replace(
                default_settings(),
                mcp_servers=(MCPServerSpec("fixture", "python", autostart=True),),
            ),
        )
        app = create_general_app(
            session_store=store,
            ollama_factory=lambda config: model,
            registry_factory=lambda specs: MCPRegistry(specs, session_factory=fixture_session),
        )
        from use_lllm.web.contracts import WebResult

        class FixtureWeb:
            async def search(self, query, max_results=5):
                return WebResult(
                    {
                        "status": "ok",
                        "results": [
                            {
                                "title": "Public evidence",
                                "url": "https://example.org/",
                                "snippet": "Synthetic public evidence.",
                            }
                        ],
                        "error": None,
                    }
                )

            async def read_page(self, url):
                return WebResult(
                    {"status": "ok", "final_url": url, "text": "Synthetic evidence.", "error": None}
                )

            def close(self):
                pass

        app.state.runtime.web_search = FixtureWeb()
        app.state.runtime.web_pages = FixtureWeb()
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")


if __name__ == "__main__":
    main()
