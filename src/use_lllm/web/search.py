"""Keyless DDGS search with bounded actual workers, including after cancellation."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from .contracts import WebError, WebResult, failure, utc_now
from .http import normalize_url, resolve_public_url


def _ddgs_search(query: str, **kwargs):
    from ddgs import DDGS
    from ddgs.engines import ENGINES

    if "duckduckgo" not in ENGINES["text"]:
        raise WebError("search_failed", "DuckDuckGoバックエンドが利用できません。")
    with DDGS(timeout=10, verify=True) as engine:
        engine.threads = 1
        return engine.text(query, **kwargs)


class SearchService:
    def __init__(self, *, search_fn=None, resolver=resolve_public_url, timeout_seconds=30):
        self._search_fn = search_fn or _ddgs_search
        self._resolver = resolver
        self._timeout = timeout_seconds
        self._executor = None
        self._slots = asyncio.Semaphore(2)
        self._closed = False

    def close(self) -> None:
        self._closed = True
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    async def search(self, query: str, max_results: int = 5) -> WebResult:
        fields = {"query": query, "backend": "duckduckgo", "results": []}
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 500
            or type(max_results) is not int
            or not 1 <= max_results <= 10
        ):
            return failure(
                "search_failed", "検索語は1〜500文字、件数は1〜10を指定してください。", **fields
            )
        if self._closed:
            return failure("search_failed", "検索サービスは終了しています。", **fields)
        deadline = time.monotonic() + self._timeout
        try:
            async with asyncio.timeout_at(deadline):
                await self._slots.acquire()
                loop = asyncio.get_running_loop()
                try:
                    if self._closed:
                        raise RuntimeError("Search service closed")
                    if self._executor is None:
                        self._executor = ThreadPoolExecutor(
                            max_workers=2, thread_name_prefix="web-search"
                        )
                    future = self._executor.submit(
                        self._search_fn,
                        query.strip(),
                        backend="duckduckgo",
                        region="jp-jp",
                        safesearch="moderate",
                        max_results=max_results,
                    )
                except BaseException:
                    self._slots.release()
                    raise

                def release(_future):
                    try:
                        loop.call_soon_threadsafe(self._slots.release)
                    except RuntimeError:
                        pass  # App loop has shut down; the executor remains bounded.

                future.add_done_callback(release)
                wrapped = asyncio.wrap_future(future)
                wrapped.add_done_callback(lambda f: None if f.cancelled() else f.exception())
                rows = await asyncio.shield(wrapped)
                results = []
                seen = set()
                for row in rows:
                    try:
                        url = normalize_url(row.get("href") or row.get("url") or "")
                        if url in seen:
                            continue
                        await self._resolver(url, deadline=deadline)
                    except WebError as exc:
                        if exc.code == "timeout":
                            raise
                        continue
                    seen.add(url)
                    results.append(
                        {
                            "title": str(row.get("title") or "")[:300],
                            "url": url,
                            "snippet": str(row.get("body") or row.get("snippet") or "")[:1000],
                        }
                    )
                    if len(results) >= max_results:
                        break
                return WebResult(
                    {
                        **fields,
                        "status": "ok" if results else "no_results",
                        "results": results,
                        "retrieved_at": utc_now(),
                        "error": None,
                    }
                )
        except (TimeoutError, WebError) as exc:
            code = exc.code if isinstance(exc, WebError) else "timeout"
            return failure(code, str(exc) or "検索が時間上限に達しました。", **fields)
        except Exception as exc:
            # DDGS uses exceptions for both remote failures and ambiguous empty responses.
            # Do not turn these into evidence that the requested information does not exist.
            return failure(
                "search_failed", f"検索を確認できませんでした（{type(exc).__name__}）。", **fields
            )
