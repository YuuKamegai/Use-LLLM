import json
import subprocess
from pathlib import Path

from tests.test_general_agent import response
from tests.test_general_api import secure_client
from tests.test_web_agent import StreamingModel
from tests.test_web_registry import Service
from use_lllm.core.sessions import SessionStore
from use_lllm.general.api import create_general_app


def test_web_ui_contract_and_bilingual_copy():
    root = Path(__file__).parents[1] / "src/use_lllm/general/static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    i18n = (root / "i18n.js").read_text(encoding="utf-8")
    assert html.index('id="web-search-section"') < html.index('id="server-form"')
    for name in ["web-search-enabled", "web-search-status", "web-search-error"]:
        assert f'id="{name}"' in html
    assert "saveWebSearchSettings" in js and "web-search" in js
    assert "Web検索と公開ページ取得を有効化" in i18n
    assert "Web検索中" in i18n and "ページを確認中" in i18n


def test_settings_to_web_evidence_and_disable(tmp_path):
    model = StreamingModel(
        [
            response(tool="web::search", arguments={"query": "public fact"}),
            response(tool="web::read_page", arguments={"url": "https://example.org/"}),
            response("確認しました。[根拠](https://example.org/)"),
        ]
    )
    app = create_general_app(
        session_store=SessionStore(tmp_path), ollama_factory=lambda config: model
    )
    service = Service()
    service.close = lambda: None
    app.state.runtime.web_search = service
    app.state.runtime.web_pages = service
    with secure_client(app) as client:
        assert client.put("/api/web-search", json={"enabled": True}).status_code == 200
        sid = client.post("/api/sessions", json={"title": "test"}).json()["id"]
        result = client.post(f"/api/sessions/{sid}/chat/stream", json={"message": "確認して"})
        assert result.status_code == 200
        events = [
            json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")
        ]
        assert not any(e["type"] == "approval_required" for e in events)
        assert [e["tool"] for e in events if e["type"] == "tool_started"] == [
            "web::search",
            "web::read_page",
        ]
        assert "https://example.org/" in client.get(f"/api/sessions/{sid}").text
        assert client.put("/api/web-search", json={"enabled": False}).status_code == 200
        assert client.get("/api/tools").json()["tools"] == []


def test_web_toggle_error_does_not_show_success():
    root = Path(__file__).parents[1] / "src/use_lllm/general/static"
    source = (root / "app.js").read_text(encoding="utf-8")
    body = source[
        source.index("async function saveWebSearchSettings()") : source.index(
            "function renderEndpointBanner()"
        )
    ]
    program = (
        """
const assert = require('node:assert/strict');
const nodes = {'#web-search-enabled':{checked:true,disabled:false}, '#web-search-error':{textContent:''}};
const $ = key => nodes[key];
const state = {settings:{web_search_enabled:false}};
const api = async () => {throw new Error('save failed')};
const renderServerList = () => {throw new Error('must not show success')};
const loadTools = async () => {};
const renderWebSearchSettings = () => {nodes['#web-search-enabled'].checked=state.settings.web_search_enabled};
"""
        + body
        + """
saveWebSearchSettings().then(() => {
assert.equal(nodes['#web-search-enabled'].checked,false);
assert.equal(nodes['#web-search-enabled'].disabled,false);
assert.equal(nodes['#web-search-error'].textContent,'save failed');
}).catch(e=>{console.error(e);process.exitCode=1});
"""
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
