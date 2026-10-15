import asyncio
import threading

import pytest

from use_lllm.web.contracts import WebError
from use_lllm.web.search import SearchService


async def public(url, *, deadline):
    if "internal" in url:
        raise WebError("blocked_url", "internal")


def test_ddgs_receives_fixed_backend_and_region():
    calls = []

    def search(query, **kwargs):
        calls.append((query, kwargs))
        return [{"title": "Evidence", "href": "https://example.org/", "body": "Description"}]

    async def check():
        service = SearchService(search_fn=search, resolver=public)
        try:
            result = await service.search("日本語の質問")
            assert not result.is_error
            assert calls == [
                (
                    "日本語の質問",
                    dict(
                        backend="duckduckgo", region="jp-jp", safesearch="moderate", max_results=5
                    ),
                )
            ]
        finally:
            service.close()

    asyncio.run(check())


@pytest.mark.parametrize("query,count", [("", 5), ("  ", 5), ("x" * 501, 5), ("q", 0), ("q", 11)])
def test_validates_query_and_count(query, count):
    async def check():
        service = SearchService(search_fn=lambda *a, **k: pytest.fail("must not search"))
        try:
            assert (await service.search(query, count)).is_error
        finally:
            service.close()

    asyncio.run(check())


def test_normalizes_and_filters_results():
    rows = [
        {"title": "t" * 400, "href": "https://EXAMPLE.org:443/#x", "body": "s" * 2000},
        {"href": "https://example.org/"},
        {"href": "http://internal/"},
        {"href": "file:///a"},
    ]

    async def check():
        service = SearchService(search_fn=lambda *a, **k: rows, resolver=public)
        try:
            result = await service.search("q")
            assert len(result.payload["results"]) == 1
            assert len(result.payload["results"][0]["title"]) == 300
            assert len(result.payload["results"][0]["snippet"]) == 1000
        finally:
            service.close()

    asyncio.run(check())


def test_empty_and_failed_search_are_distinct():
    async def check():
        service = SearchService(search_fn=lambda *a, **k: [], resolver=public)
        assert (await service.search("q")).payload["status"] == "no_results"
        service.close()

        def broken(*a, **k):
            raise RuntimeError("No results found")

        service = SearchService(search_fn=broken)
        assert (await service.search("q")).payload["error"]["code"] == "search_failed"
        service.close()

    asyncio.run(check())


def test_timeout_does_not_release_running_worker_slot():
    gate = threading.Event()
    lock = threading.Lock()
    calls = []

    def blocked(*args, **kwargs):
        with lock:
            calls.append(1)
        gate.wait(2)
        return []

    async def check():
        service = SearchService(search_fn=blocked, timeout_seconds=0.03, resolver=public)
        try:
            results = await asyncio.gather(*(service.search("q") for _ in range(8)))
            assert all(r.is_error for r in results)
            assert len(calls) == 2
            gate.set()
            await asyncio.sleep(0.02)
            assert (await service.search("q")).payload["status"] == "no_results"
        finally:
            gate.set()
            service.close()

    asyncio.run(check())
