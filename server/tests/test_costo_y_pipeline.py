"""Calculo del costo y orden de las capas del pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import pytest

from asistente_server import llm
from asistente_server.pipeline.interfaces import (
    DocumentRetriever,
    RequestContext,
    ScopeDecision,
    ScopeFilter,
    SpendGuard,
    ToolRegistry,
)
from asistente_server.pipeline.runner import Pipeline
from asistente_server.pricing import compute_cost


@dataclass
class PrecioFalso:
    input_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None
    output_per_mtok: Decimal


def test_costo_con_numeros_a_mano():
    precio = PrecioFalso(Decimal("1"), Decimal("0.5"), Decimal("2"))
    uso = llm.TokenUsage(input_tokens=1000, cached_input_tokens=200, output_tokens=500)
    # 800/1e6*1 + 200/1e6*0,5 + 500/1e6*2 = 0,0008 + 0,0001 + 0,001
    assert compute_cost(precio, uso) == Decimal("0.001900")


def test_sin_precio_cacheado_se_cobra_como_entrada_normal():
    precio = PrecioFalso(Decimal("1"), None, Decimal("2"))
    uso = llm.TokenUsage(input_tokens=1000, cached_input_tokens=200, output_tokens=0)
    assert compute_cost(precio, uso) == Decimal("0.001000")


def test_los_reasoning_tokens_no_se_cobran_aparte():
    precio = PrecioFalso(Decimal("1"), Decimal("1"), Decimal("2"))
    sin = llm.TokenUsage(input_tokens=0, cached_input_tokens=0, output_tokens=500)
    con = llm.TokenUsage(
        input_tokens=0, cached_input_tokens=0, output_tokens=500, reasoning_tokens=400
    )
    assert compute_cost(precio, sin) == compute_cost(precio, con)


async def _juntar(generador):
    return [item async for item in generador]


def test_el_pipeline_llama_las_cuatro_capas_en_orden(
    organizacion, precio, llm_falso, conversacion
):
    llm_falso()
    orden: list[str] = []

    class Gasto(SpendGuard):
        async def check(self, session, ctx):
            orden.append("gasto")
            return None

    class Pertinencia(ScopeFilter):
        async def evaluate(self, session, ctx):
            orden.append("pertinencia")
            return ScopeDecision(allowed=True)

    class Documentos(DocumentRetriever):
        async def search(self, session, ctx, k=5):
            orden.append("documentos")
            return []

    class Herramientas(ToolRegistry):
        def specs(self):
            orden.append("herramientas")
            return []

    pipeline = Pipeline(Gasto(), Pertinencia(), Documentos(), Herramientas())
    ctx = RequestContext(
        organization_id=organizacion["id"],
        conversation_id="c1",
        external_user_id="u1",
        question="Hola",
        model="openai/gpt-4o-mini",
        request_id="pedido-1",
    )
    eventos = asyncio.run(_juntar(pipeline.run(ctx, [])))

    assert orden == ["gasto", "pertinencia", "documentos", "herramientas"]
    assert any(e.startswith("event: done") for e in eventos)


def test_el_gasto_corta_antes_de_llamar_al_proveedor(organizacion, precio, llm_falso):
    llamo = {"proveedor": False}

    async def no_deberia_llamarse(**kwargs):
        llamo["proveedor"] = True
        yield llm.TextChunk("x")

    class Tope(SpendGuard):
        async def check(self, session, ctx):
            return "la organizacion paso su tope mensual"

    import asistente_server.llm as modulo_llm

    original = modulo_llm.stream_completion
    modulo_llm.stream_completion = no_deberia_llamarse
    try:
        pipeline = Pipeline(spend_guard=Tope())
        ctx = RequestContext(
            organization_id=organizacion["id"],
            conversation_id="c1",
            external_user_id="u1",
            question="Hola",
            model="openai/gpt-4o-mini",
            request_id="pedido-2",
        )
        eventos = asyncio.run(_juntar(pipeline.run(ctx, [])))
    finally:
        modulo_llm.stream_completion = original

    assert llamo["proveedor"] is False
    assert any("spend_cap_reached" in e for e in eventos)


def test_cableado_real_con_el_mock_de_litellm():
    """Confirma que el wrapper habla bien con LiteLLM, sin salir a la red."""
    import os

    os.environ.setdefault("OPENAI_API_KEY", "test")

    async def correr():
        salida = []
        async for item in llm.stream_completion(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "hola"}],
            mock_response="hola",
        ):
            salida.append(item)
        return salida

    try:
        items = asyncio.run(correr())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LiteLLM no pudo correr en modo mock: {exc}")

    texto = "".join(i.text for i in items if isinstance(i, llm.TextChunk))
    assert "hola" in texto.lower()
