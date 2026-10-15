"""Read public HTML/text with robots checks and a single request deadline."""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from .contracts import WebError, WebResult, failure, utc_now
from .http import USER_AGENT, SafeHTTPClient, normalize_url

REDIRECTS = {301, 302, 303, 307, 308}


class PageReader:
    def __init__(self, http: SafeHTTPClient | None = None):
        self.http = http or SafeHTTPClient()

    async def _robots(self, url: str, deadline: float) -> RobotFileParser:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        parser = RobotFileParser()
        for _ in range(4):
            response = await self.http.get(robots_url, deadline=deadline)
            if response.status_code == 404:
                parser.parse(["User-agent: *", "Allow: /"])
                return parser
            if response.status_code in REDIRECTS:
                robots_url = normalize_url(
                    urljoin(robots_url, response.headers.get("location", ""))
                )
                continue
            if response.status_code != 200:
                raise WebError("robots_denied", "robots.txtを確認できないため取得を停止しました。")
            parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
            return parser
        raise WebError("robots_denied", "robots.txtのリダイレクト上限に達しました。")

    async def read_page(self, url: str) -> WebResult:
        fields = {
            "requested_url": url,
            "final_url": None,
            "title": "",
            "text": "",
            "truncated": False,
        }
        try:
            deadline = time.monotonic() + 30
            async with asyncio.timeout_at(deadline):
                current = normalize_url(url)
                robots = {}
                for redirect in range(4):
                    parts = urlsplit(current)
                    origin = (parts.scheme, parts.netloc)
                    if origin not in robots:
                        robots[origin] = await self._robots(current, deadline)
                    if not robots[origin].can_fetch(USER_AGENT, current):
                        raise WebError(
                            "robots_denied", "このページはrobots.txtで取得が禁止されています。"
                        )
                    response = await self.http.get(current, deadline=deadline)
                    fields["final_url"] = response.url
                    if response.status_code in REDIRECTS:
                        if redirect == 3 or not response.headers.get("location"):
                            raise WebError("fetch_failed", "ページのリダイレクト上限に達しました。")
                        current = normalize_url(urljoin(current, response.headers["location"]))
                        continue
                    if response.status_code == 429:
                        raise WebError("rate_limited", "ページ取得が制限されています。")
                    if response.status_code != 200:
                        raise WebError(
                            "fetch_failed",
                            f"ページを取得できません（HTTP {response.status_code}）。",
                        )
                    ctype = response.headers.get("content-type", "").lower()
                    if ctype.split(";", 1)[0].strip() not in {
                        "text/html",
                        "application/xhtml+xml",
                        "text/plain",
                    }:
                        raise WebError(
                            "unsupported_content", "HTMLとプレーンテキストのみ取得できます。"
                        )
                    charset = re.search(r"charset=[\"']?([a-zA-Z0-9_-]+)", ctype)
                    try:
                        text = response.body.decode(
                            charset[1] if charset else "utf-8", errors="replace"
                        )
                    except LookupError:
                        text = response.body.decode("utf-8", errors="replace")
                    if not ctype.startswith("text/plain"):
                        from trafilatura import bare_extraction

                        document = bare_extraction(
                            text,
                            url=current,
                            include_comments=False,
                            include_tables=True,
                            with_metadata=True,
                        )
                        text = document.text if document else ""
                        fields["title"] = document.title or "" if document else ""
                    text = (text or "").strip()
                    if not text:
                        raise WebError("empty_content", "本文を抽出できませんでした。")
                    fields.update(text=text[:12000], truncated=len(text) > 12000)
                    return WebResult(
                        {**fields, "status": "ok", "retrieved_at": utc_now(), "error": None}
                    )
        except (TimeoutError, WebError) as exc:
            code = exc.code if isinstance(exc, WebError) else "timeout"
            return failure(code, str(exc) or "ページ取得が時間上限に達しました。", **fields)
        except (ValueError, OSError) as exc:
            return failure(
                "fetch_failed", f"本文を取得できませんでした（{type(exc).__name__}）。", **fields
            )
