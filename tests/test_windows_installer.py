from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_installer_projects_and_build_script_exist() -> None:
    build = (ROOT / "Build-Windows-Installer.ps1").read_text(encoding="utf-8")
    launcher = (ROOT / "launcher" / "Use-LLLM-WebUI" / "Program.cs").read_text(encoding="utf-8")
    installer = (ROOT / "installer" / "Use-LLLM-Installer" / "Program.cs").read_text(
        encoding="utf-8"
    )

    assert "pyinstaller" in build.lower()
    assert "--self-contained true" in build
    assert '"runtime", "Use-LLLM-Server.exe"' in launcher
    assert "UseLLLM.Payload.zip" in installer
    assert "LocalApplicationData" in installer
    assert "UninstallKey" in installer


def test_fresh_runtime_has_no_machine_specific_mcp_path() -> None:
    config = (ROOT / "src" / "use_lllm" / "core" / "config.py").read_text(encoding="utf-8")
    settings = (ROOT / "src" / "use_lllm" / "core" / "settings_store.py").read_text(
        encoding="utf-8"
    )
    start = (ROOT / "Start-WebUI.ps1").read_text(encoding="utf-8")

    combined = config + settings + start
    assert "C:\\Users\\yuu18" not in combined
    assert "C:\\Python314" not in combined
    assert "mcp_servers=()" in settings
