"""Engine y sesiones. Async, con psycopg 3.

La misma URL sirve para el engine async de la app y para el sincronico de
Alembic: el driver psycopg soporta los dos modos.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from .config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        # Los tests corren cada request en su propio loop de eventos; un pool
        # compartido entre loops rompe. Con NullPool cada conexion es nueva.
        if os.environ.get("DB_NULLPOOL") == "1":
            kwargs = {"poolclass": NullPool}
        _engine = create_async_engine(get_settings().database_url, **kwargs)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
