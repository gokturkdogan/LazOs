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
from urllib.parse import urlsplit

import httpx

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


async def fetch_html(client: httpx.AsyncClient, url: str, timeout: float) -> str | None:
    await assert_public_url(url)
    async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        if resp.status_code >= 400:
            return None
        # Re-check the final hop after redirects.
        if str(resp.url) != url:
            await assert_public_url(str(resp.url))
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
        encoding = resp.encoding or "utf-8"
        return raw.decode(encoding, errors="replace")


def extract_text(html: str, url: str) -> str:
    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True, deduplicate=True)
    return (text or "").strip()


_WORD = re.compile(r"\w{3,}", re.UNICODE)


def _terms(text: str) -> set[str]:
    return {w.casefold()[:6] for w in _WORD.findall(text)}  # crude stemming helps Turkish suffixes


def select_passages(text: str, query: str, max_chars: int) -> str:
    """Pick the paragraphs most related to the query, keeping their original order."""
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if len(p.strip()) > 40]
    if not paragraphs:
        return text[:max_chars]
    q = _terms(query)
    scored = [(len(q & _terms(p)) + (0.5 if i < 3 else 0), i, p) for i, p in enumerate(paragraphs)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen: list[tuple[int, str]] = []
    total = 0
    for _, i, p in scored:
        if total + len(p) > max_chars and chosen:
            continue
        chosen.append((i, p[: max_chars - total]))
        total += min(len(p), max_chars - total)
        if total >= max_chars:
            break
    chosen.sort()
    return "\n".join(p for _, p in chosen)
