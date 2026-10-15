"""Bounded HTTP reads pinned to validated public IPs, preserving TLS host verification."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpcore
from httpcore._backends.auto import AutoBackend

from .contracts import WebError

MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = "Use-LLLM-Web/0.1"


def _public(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None)
    )


def normalize_url(url: str) -> str:
    try:
        if (
            not isinstance(url, str)
            or len(url) > 2048
            or any(ord(ch) <= 32 or ord(ch) == 127 or ch == "\\" for ch in url)
        ):
            raise ValueError
        parts = urlsplit(url)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        scheme = parts.scheme.lower()
        if (
            scheme not in {"http", "https"}
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.port not in {None, 80, 443}
            or "%" in host
            or host == "localhost"
            or host.endswith(".localhost")
        ):
            raise ValueError
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r"[0-9.]+", host) or host.startswith("0x"):
                raise ValueError("non-canonical IP address") from None
        else:
            if not _public(str(addr)):
                raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if parts.port is not None and parts.port != (443 if scheme == "https" else 80):
            authority += f":{parts.port}"
        return urlunsplit((scheme, authority, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise WebError("blocked_url", "公開HTTP(S)ページのURLを指定してください。") from exc


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    ips: tuple[str, ...]


async def resolve_public_url(url: str, *, deadline: float) -> ResolvedTarget:
    normalized = normalize_url(url)
    parts = urlsplit(normalized)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        async with asyncio.timeout_at(deadline):
            records = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        ips = tuple(dict.fromkeys(record[4][0] for record in records))
        if not ips or not all(_public(ip) for ip in ips):
            raise WebError("blocked_url", "非公開アドレスへのアクセスはできません。")
        return ResolvedTarget(normalized, host, port, ips)
    except TimeoutError as exc:
        raise WebError("timeout", "DNS確認が時間上限に達しました。") from exc
    except (OSError, ValueError) as exc:
        raise WebError("fetch_failed", "接続先を確認できませんでした。") from exc


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, target: ResolvedTarget, backend=None):
        self.target = target
        self.backend = backend or AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self.target.hostname or port != self.target.port:
            raise WebError("blocked_url", "検証済みの接続先と一致しません。")
        return await self.backend.connect_tcp(
            self.target.ips[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


@dataclass(frozen=True)
class FetchedResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes


class SafeHTTPClient:
    def __init__(self, *, backend_factory=PinnedNetworkBackend):
        self.backend_factory = backend_factory

    async def get(self, url: str, *, deadline: float) -> FetchedResponse:
        try:
            async with asyncio.timeout_at(deadline):
                target = await resolve_public_url(url, deadline=deadline)
                async with httpcore.AsyncConnectionPool(
                    ssl_context=ssl.create_default_context(),
                    network_backend=self.backend_factory(target),
                    retries=0,
                    max_connections=1,
                    max_keepalive_connections=0,
                ) as pool:
                    timeout = max(0.001, deadline - time.monotonic())
                    request = httpcore.Request(
                        "GET",
                        target.url,
                        headers=[
                            (b"User-Agent", USER_AGENT.encode()),
                            (b"Host", urlsplit(target.url).netloc.encode()),
                            (b"Accept", b"text/html,text/plain;q=0.9,*/*;q=0.1"),
                            (b"Accept-Encoding", b"gzip, deflate"),
                            (b"Connection", b"close"),
                        ],
                        extensions={
                            "timeout": dict.fromkeys(("connect", "read", "write", "pool"), timeout)
                        },
                    )
                    response = await pool.handle_async_request(request)
                    try:
                        headers = {
                            k.decode("ascii").lower(): v.decode("latin1")
                            for k, v in response.headers
                        }
                        body = await self._body(response, headers.get("content-encoding", ""))
                        return FetchedResponse(target.url, response.status, headers, body)
                    finally:
                        await response.aclose()
        except (TimeoutError, httpcore.TimeoutException) as exc:
            raise WebError("timeout", "ページ取得が時間上限に達しました。") from exc
        except (httpcore.NetworkError, httpcore.ProtocolError, zlib.error) as exc:
            raise WebError("fetch_failed", "ページを取得できませんでした。") from exc

    @staticmethod
    async def _body(response, encoding: str) -> bytes:
        encoding = encoding.strip().lower()
        if encoding not in {"", "identity", "gzip", "deflate"}:
            raise WebError("unsupported_content", "未対応の圧縮形式です。")
        decoder = (
            zlib.decompressobj(31 if encoding == "gzip" else 15)
            if encoding in {"gzip", "deflate"}
            else None
        )
        data = bytearray()
        wire = 0
        async for chunk in response.aiter_stream():
            wire += len(chunk)
            if wire > MAX_BYTES:
                raise WebError("content_too_large", "ページ容量が2MiBを超えています。")
            value = decoder.decompress(chunk, MAX_BYTES - len(data) + 1) if decoder else chunk
            data.extend(value)
            if len(data) > MAX_BYTES or (decoder and decoder.unconsumed_tail):
                raise WebError("content_too_large", "展開後のページ容量が2MiBを超えています。")
        if decoder and (not decoder.eof or decoder.unused_data):
            raise WebError("fetch_failed", "不正または未対応の圧縮データです。")
        return bytes(data)
