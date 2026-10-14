"""Isolated synthetic fixture for the optional Playwright UI smoke test."""

import base64
import json
import sys
import tempfile
from pathlib import Path

import uvicorn

from tests.test_general_api import FakeOllama, FakeRegistry
from use_lllm.core.sessions import SessionStore
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
        app = create_general_app(
            session_store=store,
            ollama_factory=lambda config: FakeOllama(),
            registry_factory=lambda specs: FakeRegistry(specs),
        )
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")


if __name__ == "__main__":
    main()
