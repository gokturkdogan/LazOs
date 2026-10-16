from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str
    published: str | None = None


class SearchProviderError(RuntimeError):
    pass


class SearchProvider(Protocol):
    name: str

    async def available(self) -> bool: ...

    async def search(self, query: str, *, max_results: int, language: str | None, news: bool) -> list[SearchResult]: ...
