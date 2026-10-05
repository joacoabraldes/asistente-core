"""Facturacion y tope de gasto.

Los montos se verifican contra casos con los numeros hechos a mano: un error en
la suma es silencioso, la factura sigue siendo un numero plausible.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, text, update

from asistente_sdk import Asistente, ErrorEvent
from asistente_server.billing import consumo_del_mes, limites_del_mes
from asistente_server.db import get_sessionmaker
from asistente_server.models import Organization, Usage
from asistente_server.pipeline.interfaces import RequestContext
from asistente_server.pipeline.spend import MonthlySpendGuard


def _cargar_consumo(sync_engine, org_id, filas):
    """filas: (stage, cost_usd o None, cuando)."""
    with sync_engine.begin() as conn:
        for i, (stage, costo, cuando) in enumerate(filas):
            conn.execute(
                insert(Usage).values(
                    id=uuid.uuid4(),
                    request_id=f"pedido-{i}-{uuid.uuid4().hex[:8]}",
                    organization_id=org_id,
                    external_user_id="u1",
                    conversation_id="c1",
                    stage=stage,
                    provider="openai",
                    model="gpt-4o-mini",
                    input_tokens=None if costo is None else 100,
                    cached_input_tokens=None if costo is None else 0,
                    output_tokens=None if costo is None else 10,
                    reasoning_tokens=None if costo is None else 0,
                    cost_usd=costo,
                    created_at=cuando,
                )
            )


def _resumen(org_id, anio, mes):
    async def correr():
        async with get_sessionmaker()() as session:
            return await consumo_del_mes(session, org_id, anio, mes)

    return asyncio.run(correr())


# --- los limites del mes ----------------------------------------------------


def test_el_mes_es_medio_abierto():
    desde, hasta = limites_del_mes(2026, 9)
    assert desde == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert hasta == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_diciembre_cierra_en_enero_del_ano_siguiente():
    desde, hasta = limites_del_mes(2026, 12)
    assert hasta == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_un_mes_que_no_existe_es_un_error():
    with pytest.raises(ValueError):
        limites_del_mes(2026, 13)


# --- el total ---------------------------------------------------------------


def test_el_total_a_facturar_con_numeros_a_mano(organizacion, sync_engine):
    """1,000000 de proveedor + 15 % = 1,15 facturado."""
    septiembre = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [
            ("answer", Decimal("0.600000"), septiembre),
            ("answer", Decimal("0.300000"), septiembre),
            ("filter", Decimal("0.100000"), septiembre),
        ],
    )

    r = _resumen(organizacion["id"], 2026, 9)

    assert r.costo_proveedor == Decimal("1.000000")
    assert r.por_etapa == {"answer": Decimal("0.900000"), "filter": Decimal("0.100000")}
    assert r.markup_pct == Decimal("15.00")
    assert r.a_facturar == Decimal("1.15")
    assert r.markup_usd == Decimal("0.15")
    assert r.pedidos == 3
    assert r.pedidos_sin_costo == 0


def test_el_markup_se_aplica_una_sola_vez_al_final(organizacion, sync_engine):
    """Redondear cada pedido antes de sumar da otro numero.

    Tres pedidos de 0,004 cada uno: redondeando a centavos uno por uno dan
    US$ 0,00 cada uno y el total queda en cero. Sumando primero: 0,012 por
    1,15 = 0,0138, que a centavos es US$ 0,01.
    """
    cuando = datetime(2026, 9, 2, tzinfo=timezone.utc)
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [("answer", Decimal("0.004000"), cuando)] * 3,
    )

    r = _resumen(organizacion["id"], 2026, 9)

    assert r.costo_proveedor == Decimal("0.012000")
    assert r.a_facturar == Decimal("0.01")


def test_el_markup_no_queda_negativo_con_montos_chicos(organizacion, sync_engine):
    """Con la factura en US$ 0,00 el markup no puede dar menos que cero."""
    cuando = datetime(2026, 9, 4, tzinfo=timezone.utc)
    _cargar_consumo(
        sync_engine, organizacion["id"], [("answer", Decimal("0.000149"), cuando)]
    )

    r = _resumen(organizacion["id"], 2026, 9)

    assert r.a_facturar == Decimal("0.00")
    assert r.a_facturar_exacto == Decimal("0.000171")
    assert r.markup_usd == Decimal("0.000022")


def test_los_pedidos_sin_costo_se_cuentan_aparte(organizacion, sync_engine):
    """El agujero tiene que verse: sum() lo ignora y la factura saldria de menos."""
    cuando = datetime(2026, 9, 3, tzinfo=timezone.utc)
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [
            ("answer", Decimal("0.200000"), cuando),
            ("answer", None, cuando),
            ("answer", None, cuando),
        ],
    )

    r = _resumen(organizacion["id"], 2026, 9)

    assert r.costo_proveedor == Decimal("0.200000")
    assert r.pedidos == 3
    assert r.pedidos_sin_costo == 2


def test_el_consumo_de_otro_mes_no_entra(organizacion, sync_engine):
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [
            ("answer", Decimal("1.000000"), datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)),
            ("answer", Decimal("2.000000"), datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)),
            ("answer", Decimal("4.000000"), datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)),
        ],
    )

    assert _resumen(organizacion["id"], 2026, 9).costo_proveedor == Decimal("2.000000")
    assert _resumen(organizacion["id"], 2026, 8).costo_proveedor == Decimal("1.000000")


def test_un_mes_sin_consumo_da_cero_y_no_falla(organizacion):
    r = _resumen(organizacion["id"], 2026, 1)
    assert r.costo_proveedor == Decimal(0)
    assert r.a_facturar == Decimal("0.00")
    assert r.pedidos == 0


# --- el tope de gasto -------------------------------------------------------


def _chequear_tope(org_id):
    async def correr():
        async with get_sessionmaker()() as session:
            ctx = RequestContext(
                organization_id=org_id,
                conversation_id="c1",
                external_user_id="u1",
                question="Hola",
                model="openai/gpt-4o-mini",
                request_id="pedido-tope",
            )
            return await MonthlySpendGuard().check(session, ctx)

    return asyncio.run(correr())


def _poner_tope(sync_engine, org_id, tope):
    with sync_engine.begin() as conn:
        conn.execute(
            update(Organization)
            .where(Organization.id == org_id)
            .values(monthly_cap_usd=tope)
        )


def test_sin_tope_configurado_deja_pasar(organizacion, sync_engine):
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [("answer", Decimal("999.000000"), datetime.now(timezone.utc))],
    )
    assert _chequear_tope(organizacion["id"]) is None


def test_el_tope_se_compara_contra_lo_facturado_no_contra_el_costo(
    organizacion, sync_engine
):
    """US$ 0,90 de proveedor son US$ 1,035 facturados: con tope en 1,00 corta.

    Si se comparara contra el costo del proveedor, 0,90 < 1,00 y dejaria pasar.
    """
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [("answer", Decimal("0.900000"), datetime.now(timezone.utc))],
    )
    _poner_tope(sync_engine, organizacion["id"], Decimal("1.00"))

    motivo = _chequear_tope(organizacion["id"])
    assert motivo is not None
    assert "1.035000" in motivo


def test_abajo_del_tope_deja_pasar(organizacion, sync_engine):
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [("answer", Decimal("0.500000"), datetime.now(timezone.utc))],
    )
    _poner_tope(sync_engine, organizacion["id"], Decimal("10.00"))
    assert _chequear_tope(organizacion["id"]) is None


def test_pasado_el_tope_el_endpoint_corta_antes_de_llamar_al_proveedor(
    http, organizacion, precio, llm_guion, sync_engine
):
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [("answer", Decimal("50.000000"), datetime.now(timezone.utc))],
    )
    _poner_tope(sync_engine, organizacion["id"], Decimal("10.00"))
    llamadas = llm_guion((("no deberia llamarse",), None))
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    eventos = list(
        cliente.ask_events(conversation_id="c1", external_user_id="u1", content="Hola")
    )

    errores = [e for e in eventos if isinstance(e, ErrorEvent)]
    assert len(errores) == 1
    assert errores[0].code == "spend_cap_reached"
    # Ni la respuesta ni el filtro: el tope es la primera capa.
    assert llamadas == []
    with sync_engine.begin() as conn:
        assert conn.execute(text('SELECT count(*) FROM "usage"')).scalar() == 1


# --- el endpoint ------------------------------------------------------------


def test_el_resumen_por_el_sdk(http, organizacion, sync_engine):
    cuando = datetime.now(timezone.utc)
    _cargar_consumo(
        sync_engine,
        organizacion["id"],
        [("answer", Decimal("0.800000"), cuando), ("filter", Decimal("0.200000"), cuando)],
    )
    cliente = Asistente(api_key=organizacion["api_key"], http_client=http)

    r = cliente.consumo()

    assert r.costo_proveedor_usd == Decimal("1.000000")
    assert r.a_facturar_usd == Decimal("1.15")
    assert r.a_facturar_exacto_usd == Decimal("1.150000")
    assert r.por_etapa == {"answer": Decimal("0.800000"), "filter": Decimal("0.200000")}
    assert r.pedidos == 2
    assert r.tope_usd is None


def test_cada_organizacion_ve_solo_lo_suyo(http, organizacion, sync_engine):
    """El resumen sale de la API key, no de un id que se pueda pasar."""
    otra = uuid.uuid4()
    with sync_engine.begin() as conn:
        conn.execute(
            insert(Organization).values(
                id=otra,
                name="Otra",
                markup_pct=Decimal("15.00"),
                default_model="openai/gpt-4o-mini",
            )
        )
    cuando = datetime.now(timezone.utc)
    _cargar_consumo(sync_engine, otra, [("answer", Decimal("99.000000"), cuando)])
    _cargar_consumo(sync_engine, organizacion["id"], [("answer", Decimal("1.000000"), cuando)])

    r = Asistente(api_key=organizacion["api_key"], http_client=http).consumo()

    assert r.costo_proveedor_usd == Decimal("1.000000")
