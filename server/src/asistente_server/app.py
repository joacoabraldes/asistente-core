"""Aplicacion FastAPI."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from .routers import messages, usage
from .runtime import configurar_event_loop


def create_app() -> FastAPI:
    configurar_event_loop()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    app = FastAPI(title="asistente-core", version="0.1.0")
    app.include_router(messages.router)
    app.include_router(usage.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
