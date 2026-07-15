from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".cs", ".html", ".js", ".md", ".ps1", ".py", ".toml"}
FORBIDDEN_TEXT = (
    "lipidmix_with_llm\\webui",
    "lipidmix_with_llm/webui",
    "use-lllm(archive)",
)


def test_repository_has_no_former_location_reference() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if path == Path(__file__):
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in {".git", "bin", "obj", "publish"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8").lower()
        if any(forbidden in text for forbidden in FORBIDDEN_TEXT):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"former repository paths remain: {offenders}"


def test_start_script_resolves_source_from_its_own_directory() -> None:
    launcher = (ROOT / "Start-WebUI.ps1").read_text(encoding="utf-8")
    assert "$MyInvocation.MyCommand.Path" in launcher
    assert 'Join-Path $ProjectRoot "src"' in launcher
    assert "$env:PYTHONPATH" in launcher
