"""Safe retrieval and text extraction of public web pages.

Web content is untrusted: we only fetch http(s) URLs that resolve to public IP addresses,
cap response size, never execute anything, and strip pages down to plain text.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from app.research.extract import parse_page, select_passages

log = logging.getLogger("lazos.fetch")

MAX_BYTES = 2_500_000
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) LAZOS/1.0"


class UnsafeURL(ValueError):
    pass


async def assert_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURL(f"unsupported URL: {url}")
    host = parts.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURL(f"cannot resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeURL(f"{host} resolves to a non-public address")


async def fetch_html(
    client: httpx.AsyncClient, url: str, timeout: float, guard: Callable[[str], Awaitable[None]] | None = None
) -> str | None:
    guard = guard or assert_public_url
    await guard(url)
    async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        if resp.status_code >= 400:
            return None
        # Re-check the final hop after redirects.
        if str(resp.url) != url:
            await guard(str(resp.url))
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text/plain" not in ctype:
            return None
        chunks: list[bytes] = []
        size = 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        return _decode(raw, resp.headers.get("content-type", ""))


_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([\w-]+)", re.IGNORECASE)


def _decode(raw: bytes, content_type: str) -> str:
    """Header charset, then <meta charset>, then UTF-8, then Windows-1254 (common on older Turkish sites)."""
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    meta = _META_CHARSET.search(raw[:4096])
    for enc in (m.group(1) if m else None, meta.group(1).decode("ascii", "ignore") if meta else None, "utf-8"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("cp1254", errors="replace")


def extract_text(html: str, url: str) -> str:
    return parse_page(html, url).text


__all__ = ["USER_AGENT", "UnsafeURL", "assert_public_url", "extract_text", "fetch_html", "select_passages"]
