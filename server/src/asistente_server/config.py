"""Configuracion desde el entorno.

Se lee con os.environ y se valida con un modelo pydantic comun, porque
pydantic-settings no esta en la lista de dependencias permitidas.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    database_url: str
    log_level: str = "INFO"
    history_messages: int = 20


@lru_cache
def get_settings() -> Settings:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta la variable DATABASE_URL")
    return Settings(
        database_url=url,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        history_messages=int(os.environ.get("HISTORY_MESSAGES", "20")),
    )
