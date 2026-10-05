"""Fixtures de los tests.

Necesitan Postgres levantado:  docker compose up -d db

Ningun test llama a un proveedor real: se reemplaza `llm.stream_completion`,
que es la unica puerta al proveedor.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]

TEST_URL = os.environ.get(
    "DATABASE_URL_TEST",
    "postgresql+psycopg://asistente:asistente@localhost:5433/asistente_test",
)
# Se setean antes de importar la app, que lee la configuracion del entorno.
os.environ["DATABASE_URL"] = TEST_URL
os.environ["DB_NULLPOOL"] = "1"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, insert, text, update  # noqa: E402

from asistente_server import llm  # noqa: E402
from asistente_server.runtime import configurar_event_loop  # noqa: E402
from asistente_server.app import app  # noqa: E402
from asistente_server.auth import new_api_key  # noqa: E402
from asistente_server.models import (  # noqa: E402
    ApiKey,
    Conversation,
    ModelPrice,
    Organization,
)

# psycopg async no corre con el loop por defecto de Windows.
configurar_event_loop()

TABLAS = '"usage", messages, conversations, api_keys, model_prices, organizations'

# Precios elegidos para que el costo salga exacto y se pueda verificar a mano.
PRECIO_ENTRADA = Decimal("1.000000")
PRECIO_CACHEADO = Decimal("0.500000")
PRECIO_SALIDA = Decimal("2.000000")


@pytest.fixture(scope="session")
def sync_engine():
    engine = create_engine(TEST_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def schema(sync_engine):
    with sync_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def limpiar(sync_engine, schema):
    with sync_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLAS} RESTART IDENTITY CASCADE"))


@pytest.fixture
def http():
    with TestClient(app) as cliente:
        yield cliente


@pytest.fixture
def organizacion(sync_engine):
    org_id = uuid.uuid4()
    raw, prefix, key_hash = new_api_key()
    with sync_engine.begin() as conn:
        conn.execute(
            insert(Organization).values(
                id=org_id,
                name="Terminal",
                markup_pct=Decimal("15.00"),
                default_model="openai/gpt-4o-mini",
            )
        )
        conn.execute(
            insert(ApiKey).values(
                id=uuid.uuid4(),
                organization_id=org_id,
                key_hash=key_hash,
                prefix=prefix,
            )
        )
    return {"id": org_id, "api_key": raw}


@pytest.fixture
def precio(sync_engine):
    price_id = uuid.uuid4()
    with sync_engine.begin() as conn:
        conn.execute(
            insert(ModelPrice).values(
                id=price_id,
                provider="openai",
                model="gpt-4o-mini",
                input_per_mtok=PRECIO_ENTRADA,
                cached_input_per_mtok=PRECIO_CACHEADO,
                output_per_mtok=PRECIO_SALIDA,
                valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
    return price_id


@pytest.fixture
def conversacion(sync_engine, organizacion):
    """Para los tests que llaman al pipeline directo.

    Normalmente la crea el endpoint en la primera pregunta.
    """
    with sync_engine.begin() as conn:
        conn.execute(
            insert(Conversation).values(
                organization_id=organizacion["id"], id="c1", external_user_id="u1"
            )
        )
    return "c1"


@pytest.fixture
def con_temas(sync_engine, organizacion):
    """Enciende el filtro de pertinencia para la organizacion del test."""

    def poner(*temas: str, filter_model: str | None = None):
        with sync_engine.begin() as conn:
            conn.execute(
                update(Organization)
                .where(Organization.id == organizacion["id"])
                .values(allowed_topics=list(temas), filter_model=filter_model)
            )
        return organizacion

    return poner


@pytest.fixture
def uso_de_ejemplo():
    return llm.TokenUsage(
        input_tokens=1000, cached_input_tokens=200, output_tokens=500, reasoning_tokens=0
    )


@pytest.fixture
def llm_falso(monkeypatch, uso_de_ejemplo):
    """Instala un proveedor falso que emite texto y, al final, el uso."""

    def instalar(textos=("Hola ", "mundo"), usage=...):
        tokens = uso_de_ejemplo if usage is ... else usage

        async def fake(**kwargs):
            for parte in textos:
                yield llm.TextChunk(parte)
            if tokens is not None:
                yield tokens

        monkeypatch.setattr(llm, "stream_completion", fake)

    return instalar


@pytest.fixture
def llm_guion(monkeypatch, uso_de_ejemplo):
    """Proveedor falso con guion: una respuesta por llamada, en orden.

    Hace falta cuando una misma pregunta genera dos llamadas al proveedor —el
    filtro y la respuesta— y cada una tiene que contestar distinto. Devuelve la
    lista donde se van guardando los kwargs de cada llamada, para poder revisar
    con que se la invoco.
    """

    def instalar(*respuestas: tuple[tuple[str, ...], object]):
        guion = list(respuestas)
        llamadas: list[dict] = []

        async def fake(**kwargs):
            llamadas.append(kwargs)
            textos, tokens = guion.pop(0) if guion else (("",), None)
            for parte in textos:
                yield llm.TextChunk(parte)
            if tokens is not None:
                yield tokens

        monkeypatch.setattr(llm, "stream_completion", fake)
        return llamadas

    return instalar
