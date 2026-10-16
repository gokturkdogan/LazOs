from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import BootConfig
from app.main import create_app
from app.research.providers.base import SearchResult
from app.research.service import ResearchService
from app.services import Services
from tests.fakes import FakeLLM, FakeSearchProvider

REAL_MODELS_DIR = Path.home() / "Library" / "Application Support" / "LAZOS" / "models"


@pytest.fixture
def boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BootConfig:
    monkeypatch.delenv("LAZOS_TOKEN", raising=False)
    cfg = BootConfig(
        data_dir=tmp_path / "data",
        warmup=False,
        ollama_url="http://127.0.0.1:9",  # never used: tests inject FakeLLM
        searxng_url="http://127.0.0.1:9",
        whisper_torch_checkpoint=tmp_path / "missing.pt",
    )
    for d in (cfg.data_dir, cfg.models_dir, cfg.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_search() -> FakeSearchProvider:
    return FakeSearchProvider(
        [
            SearchResult("Haber A", "https://example.com/a", "Birinci kaynak özeti.", "fake-search"),
            SearchResult("Haber B", "https://example.org/b", "İkinci kaynak özeti.", "fake-search"),
        ]
    )


@pytest.fixture
def client(boot: BootConfig, fake_llm: FakeLLM, fake_search: FakeSearchProvider) -> Iterator[TestClient]:
    research = ResearchService([fake_search], fetch_timeout=0.5, total_timeout=3)

    async def no_fetch(client, url, query):  # type: ignore[no-untyped-def]
        return ""  # never touch the network from unit tests

    research._excerpt = no_fetch  # type: ignore[method-assign]

    def factory(cfg: BootConfig) -> Services:
        svc = Services(cfg, llm=fake_llm, research=research)
        return svc

    app = create_app(boot, services_factory=factory)
    with TestClient(app) as c:
        c.token = app.state.token  # type: ignore[attr-defined]
        c.services = app.state.services  # type: ignore[attr-defined]
        yield c


@pytest.fixture
def auth(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {client.token}"}  # type: ignore[attr-defined]
