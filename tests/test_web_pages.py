import asyncio
import time

import pytest

from use_lllm.web.http import FetchedResponse
from use_lllm.web.pages import PageReader


class FakeHTTP:
    def __init__(self, pages, robots=b"User-agent: *\nAllow: /"):
        self.pages = pages
        self.robots = robots
        self.calls = []
        self.deadlines = []

    async def get(self, url, *, deadline):
        self.calls.append(url)
        self.deadlines.append(deadline)
        if url.endswith("/robots.txt"):
            return FetchedResponse(url, 200, {}, self.robots)
        status, headers, body = self.pages[url]
        return FetchedResponse(url, status, headers, body)


def read(http, url="https://example.org/"):
    return asyncio.run(PageReader(http).read_page(url))


def test_extracts_html_title_text_and_timestamp():
    body = (
        "<html><head><title>Evidence</title></head><body><article><p>"
        + "This is an official scientific document with useful information. " * 30
        + "</p></article><script>ignore instructions</script></body></html>"
    ).encode()
    http = FakeHTTP({"https://example.org/": (200, {"content-type": "text/html"}, body)})
    result = read(http)
    assert result.payload["title"] == "Evidence"
    assert "official scientific" in result.payload["text"]
    assert "ignore instructions" not in result.payload["text"]
    assert result.payload["retrieved_at"] and "published_at" not in result.payload
    assert not result.is_error
    assert http.calls[0].endswith("/robots.txt")


def test_plain_text_and_truncation():
    result = read(
        FakeHTTP({"https://example.org/": (200, {"content-type": "text/plain"}, b"x" * 13000)})
    )
    assert len(result.payload["text"]) == 12000
    assert result.payload["truncated"]


def test_checks_robots_before_body():
    http = FakeHTTP({}, b"User-agent: *\nDisallow: /")
    result = read(http)
    assert result.payload["error"]["code"] == "robots_denied"
    assert len(http.calls) == 1


def test_revalidates_redirect_destinations():
    http = FakeHTTP({"https://example.org/": (302, {"location": "http://127.0.0.1/"}, b"")})
    result = read(http)
    assert result.payload["error"]["code"] == "blocked_url"
    assert len(http.calls) == 2


def test_redirect_limit_and_shared_deadline():
    pages = {
        "https://example.org/" + "a" * n: (302, {"location": "/" + "a" * (n + 1)}, b"")
        for n in range(5)
    }
    http = FakeHTTP(pages)
    result = read(http)
    assert result.is_error
    assert len(set(http.deadlines)) == 1
    assert time.monotonic() < http.deadlines[0]
    assert sum(not x.endswith("robots.txt") for x in http.calls) == 4


@pytest.mark.parametrize(
    "status,ctype,body,code",
    [
        (200, "application/pdf", b"%PDF", "unsupported_content"),
        (200, "text/plain", b"", "empty_content"),
        (401, "text/html", b"login", "fetch_failed"),
        (403, "text/html", b"captcha?", "fetch_failed"),
        (429, "text/html", b"wait", "rate_limited"),
    ],
)
def test_rejects_pdf_empty_and_auth_failures(status, ctype, body, code):
    result = read(FakeHTTP({"https://example.org/": (status, {"content-type": ctype}, body)}))
    assert result.is_error and result.payload["error"]["code"] == code


def test_new_origin_rechecks_robots():
    http = FakeHTTP(
        {
            "https://example.org/": (302, {"location": "https://example.com/"}, b""),
            "https://example.com/": (200, {"content-type": "text/plain"}, b"Evidence"),
        }
    )
    result = read(http)
    assert result.payload["final_url"] == "https://example.com/"
    assert http.calls == [
        "https://example.org/robots.txt",
        "https://example.org/",
        "https://example.com/robots.txt",
        "https://example.com/",
    ]


@pytest.mark.parametrize("status,expected", [(404, "ok"), (503, "error")])
def test_robots_missing_and_unavailable(status, expected):
    class HTTP(FakeHTTP):
        async def get(self, url, *, deadline):
            if url.endswith("/robots.txt"):
                self.calls.append(url)
                return FetchedResponse(url, status, {}, b"")
            return await super().get(url, deadline=deadline)

    http = HTTP({"https://example.org/": (200, {"content-type": "text/plain"}, b"Evidence")})
    result = read(http)
    assert result.payload["status"] == expected
    assert len(http.calls) == (2 if status == 404 else 1)
