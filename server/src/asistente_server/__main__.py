"""Entrada para levantar el servidor: `python -m asistente_server`.

Es el mismo comando en Windows y en Docker para que no haya dos formas de
levantar lo mismo. Existe porque uvicorn elige el loop de eventos antes de
importar la app y en Windows elige uno que psycopg no puede usar; ver
`runtime.py`.
"""

from __future__ import annotations

import asyncio
import os

import uvicorn

from .runtime import event_loop_factory


def main() -> None:
    config = uvicorn.Config(
        "asistente_server.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        loop="none",
    )
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=event_loop_factory())


if __name__ == "__main__":
    main()
