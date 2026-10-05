"""Ajustes de plataforma.

En Windows el loop de eventos por defecto es ProactorEventLoop, y psycopg en
modo async no funciona con ese: necesita SelectorEventLoop. En Linux, que es
donde corre en Docker, nada de esto hace falta.

Hay dos caminos para arrancar un loop y cada uno necesita su propio ajuste:

* `configurar_event_loop()` fija la politica del proceso. Sirve para quien
  llama a `asyncio.run()` sin mas, como el TestClient de Starlette en los
  tests.
* `event_loop_factory()` devuelve la fabrica explicita. Uvicorn 0.54 **ignora
  la politica**: en Windows devuelve `asyncio.ProactorEventLoop` directamente
  (ver `uvicorn/loops/asyncio.py`), asi que la unica forma de que use el
  selector es pasarle la fabrica a mano. Por eso `__main__.py` arranca el
  server con `loop="none"` y corre `asyncio.run(..., loop_factory=...)`.

Efecto secundario a tener en cuenta: con el loop selector, uvicorn local en
Windows queda limitado por select() a unos cientos de descriptores. Para
desarrollo alcanza; en produccion corre en Linux.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable


def configurar_event_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def event_loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """Fabrica de loop a usar, o None para dejar el default de la plataforma."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop
    return None
