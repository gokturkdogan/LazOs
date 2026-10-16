"""FastAPI application factory and entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import http, memory, ws
from app.core.config import BootConfig, get_boot_config
from app.core.logging import setup_logging
from app.core.security import ALLOWED_ORIGINS, load_or_create_token
from app.services import Services

log = logging.getLogger("lazos")


def create_app(boot: BootConfig | None = None, *, services_factory: Callable[[BootConfig], Services] | None = None) -> FastAPI:
    boot = boot or get_boot_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = services_factory(boot) if services_factory else Services(boot)
        app.state.services = services
        await services.start()
        log.info("LAZOS backend ready", extra={"host": boot.host, "port": boot.port, "data_dir": str(boot.data_dir)})
        try:
            yield
        finally:
            await services.stop()

    app = FastAPI(title="LAZOS AI Backend", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.token = load_or_create_token(boot.token_path)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(ALLOWED_ORIGINS),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-LAZOS-Token"],
    )
    app.include_router(http.public)
    app.include_router(http.api)
    app.include_router(memory.router)
    app.include_router(ws.router)
    return app


def run() -> None:
    import uvicorn

    boot = get_boot_config()
    setup_logging(boot.log_level, boot.logs_dir)
    uvicorn.run(create_app(boot), host=boot.host, port=boot.port, log_config=None, ws_ping_interval=20)


if __name__ == "__main__":
    run()
