"""Portable session HTML with local assets, fonts and complete persisted history."""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from use_lllm.core.sessions import SessionStore


def _json(value: object) -> str:
    # JSON lives in a script raw-text element; HTML escaping would corrupt it.
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def export_session_html(
    session: dict, store: SessionStore, static: Path, vendor: Path, language: str
) -> str:
    def data_uri(path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    # Only files physically contained in this session may be included.
    directory = (store.artifact_root / session["id"]).resolve()
    files = {}
    for path in sorted(directory.iterdir()):
        if path.is_file() and not path.is_symlink() and path.resolve().parent == directory:
            files[path.name] = data_uri(path)
    assets = {}
    for message in session["messages"]:
        for block in message.get("metadata", {}).get("content_blocks", []):
            if block.get("type") != "artifact_image":
                continue
            url = str(block.get("url", ""))
            prefix = f"./api/sessions/{session['id']}/artifacts/"
            name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
            if not url.startswith(prefix) or name not in files:
                raise ValueError("書き出す画像が見つかりません。")
            assets[url] = files[name]

    css = (vendor / "katex.min.css").read_text(encoding="utf-8")
    # The app distributes WOFF2 only; discard obsolete WOFF/TTF fallbacks.
    css = re.sub(r",url\([^)]*\) format\(\"(?:woff|truetype)\"\)", "", css)
    css = re.sub(
        r"url\(([^)]+)\)",
        lambda match: "url(" + data_uri(vendor / match[1].strip("\"'")) + ")",
        css,
    )
    css += (static / "styles.css").read_text(encoding="utf-8")
    css += """
body { display:block; overflow:auto; padding:24px; }
.export-page { max-width:1100px; margin:auto; }
.export-page h1 { overflow-wrap:anywhere; }
.export-page .bubble { max-height:none!important; overflow:visible; }
.export-page .message { display:block; }
.export-page .message.user .bubble { margin-left:auto; }
.export-page .chat-messages { padding:0; }
.export-page .chat-pca-plot { height:440px; min-height:300px; }
.export-page pre { white-space:pre-wrap; overflow-wrap:anywhere; }
.export-page .copy-code, .export-page .resize-handle { display:none; }
.export-page img { max-width:100%; }
"""
    assets_scripts = []
    for path in [vendor / "plotly.min.js", vendor / "katex.min.js"] + [
        static / name
        for name in (
            "i18n.js",
            "markdown.js",
            "artifact-renderer.js",
            "pca-plot.js",
            "eic-plot.js",
            "volcano-plot.js",
        )
    ]:
        code = path.read_text(encoding="utf-8")
        assets_scripts.append(
            "<script>" + re.sub(r"</script", r"<\\/script", code, flags=re.I) + "</script>"
        )
    title = html.escape(session["title"])
    history = "Complete session data" if language == "en" else "セッションの全データ"
    attachments = "Saved files" if language == "en" else "保存されたファイル"
    links = "".join(
        f'<li><a download="{html.escape(name, quote=True)}" href="{uri}">'
        f"{html.escape(name)}</a></li>"
        for name, uri in files.items()
    )
    return (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
        "font-src data:; worker-src blob:; connect-src 'none'\">"
        f"<title>{title}</title><style>{css}</style></head><body>"
        f'<main class="export-page"><h1>{title}</h1>'
        f"<p>{html.escape(session['created_at'])} · {html.escape(session['updated_at'])}</p>"
        '<div id="chat-messages" class="chat-messages"></div>'
        f"<details><summary>{attachments}</summary><ul>{links}</ul></details>"
        f'<details><summary>{history}</summary><pre id="raw-session"></pre></details></main>'
        f'<script id="session-data" type="application/json">{_json(session)}</script>'
        f'<script id="session-assets" type="application/json">{_json(assets)}</script>'
        + "".join(assets_scripts)
        + "<script>"
        + (static / "session-export.js").read_text(encoding="utf-8")
        + "</script></body></html>"
    )
