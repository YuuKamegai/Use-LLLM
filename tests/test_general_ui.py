from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from use_lllm.core.sessions import SessionStore
from use_lllm.general.api import create_general_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "use_lllm" / "general" / "static"


class GeneralUiContractTests(unittest.TestCase):
    def test_static_ui_contains_chat_settings_and_approval_contracts(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        artifact_renderer = (STATIC / "artifact-renderer.js").read_text(encoding="utf-8")
        styles = (STATIC / "styles.css").read_text(encoding="utf-8")

        for element_id in (
            "endpoint-banner",
            "session-list",
            "chat-messages",
            "chat-form",
            "settings-panel",
            "endpoint-form",
            "endpoint-provider",
            "endpoint-api-key",
            "endpoint-context-window",
            "setup-use-azure",
            "server-form",
            "tool-list",
            "context-meter",
            "context-meter-label",
            "context-meter-fill",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("resolveApproval", script)
        self.assertIn("/approvals/", script)
        self.assertIn("AbortController", script)
        self.assertIn("split(/\\r?\\n/)", script)
        self.assertIn("引数（1行1引数）", html)
        self.assertIn("server::tool", html)
        self.assertIn("./vendor/plotly.min.js", html)
        self.assertIn("./vendor/katex.min.js", html)
        self.assertIn("./vendor/katex.min.css", html)
        self.assertIn("./static/pca-plot.js", html)
        self.assertIn("./static/eic-plot.js", html)
        self.assertIn("appendPcaPlot", script)
        self.assertIn("appendEicPlot", script)
        self.assertIn("applySessionTitle", script)
        self.assertIn("function appendMessage(item)", script)
        self.assertIn("function renderContextUsage()", script)
        self.assertIn("残りコンテキスト", script)
        self.assertIn('meter.classList.add("critical")', script)
        self.assertIn(".context-meter.warning", styles)
        self.assertIn(".context-meter.critical", styles)
        self.assertIn(".message-content .katex-display", styles)
        self.assertIn('block.type === "artifact_image"', artifact_renderer)
        self.assertIn('image.loading = "lazy"', artifact_renderer)
        self.assertIn(".tool-image-link", styles)
        self.assertIn('id="setup-overlay"', html)
        self.assertIn("async function pullSetupModel()", script)
        self.assertIn("async function configureAzureFromSetup()", script)
        self.assertIn("async function testEndpoint(name)", script)
        self.assertIn('api_key: $("#endpoint-api-key").value.trim() || null', script)
        self.assertIn('context_window: $("#endpoint-context-window").value', script)
        self.assertIn("usage.context_window_confirmed === false", script)
        self.assertIn('meta[name="use-lllm-csrf-token"]', script)
        self.assertIn('result["X-Use-LLLM-CSRF"] = csrfToken', script)
        self.assertIn('name="use-lllm-csrf-token"', html)
        self.assertIn('$("#endpoint-trust").disabled = azure', script)
        self.assertIn('$("#endpoint-url").value === "http://127.0.0.1:11434"', script)
        self.assertIn('api("setup/complete"', script)
        self.assertIn('const workspace = $("#chat-workspace")', script)
        self.assertIn("workspace.scrollTop = workspace.scrollHeight", script)
        self.assertIn("grid-template-rows: minmax(0, 1fr) auto", styles)
        self.assertIn(".chat-workspace { min-height: 0; overflow-y: auto", styles)
        self.assertIn(".composer-wrap { z-index: 4; width: 100%", styles)
        self.assertNotIn(".composer-wrap { position: absolute", styles)
        self.assertNotIn(".composer-wrap { position: fixed", styles)
        self.assertLess(html.index('id="chat-workspace"'), html.index('class="composer-wrap"'))
        self.assertIn("@media (max-width: 720px)", styles)

        send_source = script[
            script.index("async function sendMessage") : script.index("function cancelChat")
        ]
        optimistic_index = send_source.index("appendMessage(optimisticMessage)")
        request_index = send_source.index("const response = await fetch")
        reconcile_index = send_source.index("await openSession(state.current.id)")
        self.assertLess(optimistic_index, request_index)
        self.assertLess(request_index, reconcile_index)

    def test_general_index_and_static_assets_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = SessionStore(Path(raw) / "state")
            app = create_general_app(
                session_store=store,
                settings_path=Path(raw) / "settings.json",
            )
            with TestClient(app, base_url="http://127.0.0.1") as client:
                index = client.get("/")
                script = client.get("/static/app.js")
                styles = client.get("/static/styles.css")
                pca_helper = client.get("/static/pca-plot.js")
                eic_helper = client.get("/static/eic-plot.js")
                plotly = client.get("/vendor/plotly.min.js")
                katex_script = client.get("/vendor/katex.min.js")
                katex_styles = client.get("/vendor/katex.min.css")
                katex_font = client.get("/vendor/fonts/KaTeX_Main-Regular.woff2")
                launcher_health = client.get("/api/launcher-health")
        self.assertEqual(index.status_code, 200)
        self.assertIn("Use-LLLM General", index.text)
        self.assertNotIn("__USE_LLLM_CSRF_TOKEN__", index.text)
        self.assertEqual(index.headers["cache-control"], "no-store")
        self.assertEqual(script.status_code, 200)
        self.assertIn("initialize();", script.text)
        self.assertEqual(styles.status_code, 200)
        self.assertEqual(pca_helper.status_code, 200)
        self.assertIn("GeneralPcaPlot", pca_helper.text)
        self.assertEqual(eic_helper.status_code, 200)
        self.assertIn("GeneralEicPlot", eic_helper.text)
        self.assertEqual(plotly.status_code, 200)
        self.assertEqual(katex_script.status_code, 200)
        self.assertEqual(katex_styles.status_code, 200)
        self.assertEqual(katex_font.status_code, 200)
        self.assertEqual(
            launcher_health.json(),
            {
                "application": "use-lllm-webui",
                "surface": "general",
                "static_ready": True,
            },
        )

    def test_launcher_health_rejects_missing_static_ui(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "missing"
            store = SessionStore(Path(raw) / "state")
            with (
                patch("use_lllm.general.api.STATIC_ROOT", missing),
                patch("use_lllm.general.api.VENDOR_ROOT", missing / "vendor"),
            ):
                app = create_general_app(
                    session_store=store,
                    settings_path=Path(raw) / "settings.json",
                )
                with TestClient(app, base_url="http://127.0.0.1") as client:
                    health = client.get("/api/launcher-health")
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["static_ready"])

    def test_package_includes_general_static(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"general/static/*"', pyproject)
        self.assertIn('"static/vendor/fonts/*"', pyproject)

    def test_windows_launcher_targets_general_surface(self) -> None:
        launcher = (ROOT / "Start-WebUI.ps1").read_text(encoding="utf-8")
        exe_source = (ROOT / "launcher" / "Use-LLLM-WebUI" / "Program.cs").read_text(
            encoding="utf-8"
        )
        build_script = (ROOT / "Build-WebUI-Launcher.ps1").read_text(encoding="utf-8")
        self.assertIn('$SourceRoot = Join-Path $ProjectRoot "src"', launcher)
        self.assertIn("$env:PYTHONPATH", launcher)
        self.assertNotIn("--surface", launcher)
        self.assertNotIn("127.0.0.1:8765", exe_source)
        self.assertIn("SelectAvailablePort()", exe_source)
        self.assertIn('start.ArgumentList.Add("-Port")', exe_source)
        self.assertIn('start.ArgumentList.Add("-NoBrowser")', exe_source)
        self.assertIn("$env:USE_LLLM_WEB_PORT = [string]$Port", launcher)
        self.assertIn("api/launcher-health", exe_source)
        self.assertIn('TryGetProperty("static_ready"', exe_source)
        self.assertIn("ServerState.Incompatible", exe_source)
        self.assertNotIn('Add("general")', exe_source)
        self.assertIn("dotnet publish", build_script)


if __name__ == "__main__":
    unittest.main()
