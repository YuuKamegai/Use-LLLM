import asyncio
import gzip
import time
from unittest.mock import patch

import pytest

from use_lllm.web.contracts import WebError, WebResult, to_mcp_result
from use_lllm.web.http import (
    MAX_BYTES,
    PinnedNetworkBackend,
    SafeHTTPClient,
    normalize_url,
    resolve_public_url,
)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:8.8.8.8]/",
        "http://user:pw@example.org/",
        "https://example.org:8443/",
        "https://example.org/" + "a" * 2048,
        "http://2130706433/",
        "http://127.1/",
        "http://example.org\\@8.8.8.8/",
    ],
)
def test_rejects_non_public_targets(url):
    with pytest.raises(WebError) as exc:
        run(resolve_public_url(url, deadline=time.monotonic() + 1))
    assert exc.value.code == "blocked_url"


def test_rejects_mixed_dns_answers():
    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))]

    with patch("asyncio.BaseEventLoop.getaddrinfo", resolve), pytest.raises(WebError):
        run(resolve_public_url("https://example.org/", deadline=time.monotonic() + 1))


def test_normalize_url_preserves_query():
    assert (
        normalize_url("HTTPS://Example.ORG:443/a?z=2&a=1#frag") == "https://example.org/a?z=2&a=1"
    )


class Stream:
    def __init__(self, body=b"hello", headers=b""):
        self.pending = [
            b"HTTP/1.1 200 OK\r\n"
            + headers
            + b"Content-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        ]
        self.written = b""
        self.sni = None
        self.closed = False

    async def read(self, max_bytes, timeout=None):
        return self.pending.pop(0) if self.pending else b""

    async def write(self, buffer, timeout=None):
        self.written += buffer

    async def aclose(self):
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        assert ssl_context.check_hostname
        self.sni = server_hostname
        return self

    def get_extra_info(self, info):
        return None


class Backend:
    def __init__(self, stream):
        self.stream = stream
        self.hosts = []

    async def connect_tcp(self, host, port, **kwargs):
        self.hosts.append(host)
        return self.stream


def test_pins_address_but_keeps_host_and_sni():
    stream = Stream(headers=b"Set-Cookie: secret=value\r\n")
    backend = Backend(stream)

    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    async def check():
        client = SafeHTTPClient(
            backend_factory=lambda target: PinnedNetworkBackend(target, backend)
        )
        with (
            patch("asyncio.BaseEventLoop.getaddrinfo", resolve),
            patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:9"}),
        ):
            result = await client.get("https://example.org/", deadline=time.monotonic() + 1)
        assert result.body == b"hello"
        assert backend.hosts == ["93.184.216.34"]
        assert stream.sni == "example.org"
        assert b"Host: example.org" in stream.written
        assert b"Cookie:" not in stream.written and b"Authorization:" not in stream.written
        assert stream.closed

    run(check())


@pytest.mark.parametrize(
    "body,headers",
    [
        (b"x" * (2 * 1024 * 1024 + 1), b""),
        (gzip.compress(b"x" * (2 * 1024 * 1024 + 1)), b"Content-Encoding: gzip\r\n"),
    ],
    ids=["wire", "gzip"],
)
def test_limits_decoded_and_wire_body(body, headers):
    assert MAX_BYTES == 2 * 1024 * 1024
    stream = Stream(body, headers)

    async def check():
        client = SafeHTTPClient(
            backend_factory=lambda target: PinnedNetworkBackend(target, Backend(stream))
        )
        with pytest.raises(WebError) as exc:
            await client.get("https://8.8.8.8/", deadline=time.monotonic() + 1)
        assert exc.value.code == "content_too_large"
        assert stream.closed

    run(check())


def test_deadline_covers_dns_and_connection():
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    async def check():
        with patch("asyncio.BaseEventLoop.getaddrinfo", slow), pytest.raises(WebError) as exc:
            await SafeHTTPClient().get("https://example.org/", deadline=time.monotonic() + 0.01)
        assert exc.value.code == "timeout"

    run(check())


def test_result_preserves_failure():
    value = WebResult({"status": "error", "error": {"code": "timeout", "message": "timeout"}}, True)
    result = to_mcp_result("web::search", value)
    assert result.is_error and result.structured_content == value.payload
