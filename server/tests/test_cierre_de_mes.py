"""El cierre de mes.

Lo que se verifica es el invariante que el cierre existe para sostener: una vez
cerrado, el mes da el mismo numero para siempre, pase lo que pase despues con la
configuracion de la organizacion.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, text, update

from asistente_server.billing import (
    MesEnCurso,
    YaCerrado,
    cerrar_mes,
    consumo_del_mes,
)
from asistente_server.db import get_sessionmaker
from asistente_server.models import Organization, Usage

SEPTIEMBRE = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
# Un instante en el que septiembre ya termino, para no depender de la fecha real.
YA_PASO = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _cargar(sync_engine, org_id, costo, stage="answer", cuando=SEPTIEMBRE):
    with sync_engine.begin() as conn:
        conn.execute(
            insert(Usage).values(
                id=uuid.uuid4(),
                request_id=f"pedido-{uuid.uuid4().hex[:10]}",
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


def _correr(coro_factory):
    async def correr():
        async with get_sessionmaker()() as session:
            resultado = await coro_factory(session)
            await session.commit()
            return resultado

    return asyncio.run(correr())


def test_cerrar_guarda_los_totales(organizacion, sync_engine):
    _cargar(sync_engine, organizacion["id"], Decimal("0.900000"))
    _cargar(sync_engine, organizacion["id"], Decimal("0.100000"), stage="filter")

    r = _correr(
        lambda s: cerrar_mes(s, organizacion["id"], 2026, 9, ahora=YA_PASO)
    )

    assert r.cerrado is True
    assert r.costo_proveedor == Decimal("1.000000")
    assert r.a_facturar == Decimal("1.15")

    with sync_engine.begin() as conn:
        fila = conn.execute(
            text(
                "SELECT markup_pct, costo_proveedor_usd, a_facturar_usd, pedidos, "
                "pedidos_sin_costo, por_etapa FROM monthly_closings"
            )
        ).one()
    assert fila.markup_pct == Decimal("15.00")
    assert fila.costo_proveedor_usd == Decimal("1.000000")
    assert fila.a_facturar_usd == Decimal("1.15")
    assert fila.pedidos == 2
    assert fila.por_etapa == {"answer": "0.900000", "filter": "0.100000"}


def test_despues_de_cerrar_cambiar_el_markup_no_mueve_el_mes(organizacion, sync_engine):
    """Es el invariante por el que existe el cierre."""
    _cargar(sync_engine, organizacion["id"], Decimal("1.000000"))
    _correr(lambda s: cerrar_mes(s, organizacion["id"], 2026, 9, ahora=YA_PASO))

    with sync_engine.begin() as conn:
        conn.execute(
            update(Organization)
            .where(Organization.id == organizacion["id"])
            .values(markup_pct=Decimal("40.00"))
        )

    r = _correr(lambda s: consumo_del_mes(s, organizacion["id"], 2026, 9))

    assert r.markup_pct == Decimal("15.00")
    assert r.a_facturar == Decimal("1.15")
    assert r.cerrado is True


def test_el_mes_sin_cerrar_si_se_mueve(organizacion, sync_engine):
    """El contraste del test anterior: sin cierre, el markup nuevo se aplica."""
    _cargar(sync_engine, organizacion["id"], Decimal("1.000000"))
    with sync_engine.begin() as conn:
        conn.execute(
            update(Organization)
            .where(Organization.id == organizacion["id"])
            .values(markup_pct=Decimal("40.00"))
        )

    r = _correr(lambda s: consumo_del_mes(s, organizacion["id"], 2026, 9))

    assert r.a_facturar == Decimal("1.40")
    assert r.cerrado is False


def test_no_se_cierra_el_mes_en_curso(organizacion, sync_engine):
    _cargar(sync_engine, organizacion["id"], Decimal("1.000000"))
    mitad_de_septiembre = datetime(2026, 9, 20, tzinfo=timezone.utc)

    with pytest.raises(MesEnCurso):
        _correr(
            lambda s: cerrar_mes(
                s, organizacion["id"], 2026, 9, ahora=mitad_de_septiembre
            )
        )


def test_cerrar_dos_veces_necesita_forzar(organizacion, sync_engine):
    _cargar(sync_engine, organizacion["id"], Decimal("1.000000"))
    _correr(lambda s: cerrar_mes(s, organizacion["id"], 2026, 9, ahora=YA_PASO))

    with pytest.raises(YaCerrado):
        _correr(lambda s: cerrar_mes(s, organizacion["id"], 2026, 9, ahora=YA_PASO))


def test_forzar_recalcula_con_el_consumo_que_aparecio_despues(organizacion, sync_engine):
    """Caso real: una fila de consumo llega tarde y hay que recerrar."""
    _cargar(sync_engine, organizacion["id"], Decimal("1.000000"))
    _correr(lambda s: cerrar_mes(s, organizacion["id"], 2026, 9, ahora=YA_PASO))

    _cargar(sync_engine, organizacion["id"], Decimal("0.500000"))
    r = _correr(
        lambda s: cerrar_mes(
            s, organizacion["id"], 2026, 9, forzar=True, ahora=YA_PASO
        )
    )

    assert r.costo_proveedor == Decimal("1.500000")
    assert r.a_facturar == Decimal("1.73")  # 1,5 x 1,15 = 1,725
    with sync_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM monthly_closings")).scalar() == 1


def test_un_mes_cerrado_ignora_el_consumo_que_llegue_despues(organizacion, sync_engine):
    """Sin --forzar el cierre manda, aunque aparezca una fila nueva."""
    _cargar(sync_engine, organizacion["id"], Decimal("1.000000"))
    _correr(lambda s: cerrar_mes(s, organizacion["id"], 2026, 9, ahora=YA_PASO))
    _cargar(sync_engine, organizacion["id"], Decimal("9.000000"))

    r = _correr(lambda s: consumo_del_mes(s, organizacion["id"], 2026, 9))

    assert r.costo_proveedor == Decimal("1.000000")
    assert r.cerrado is True


def test_el_cierre_llega_por_el_sdk(http, organizacion, sync_engine):
    from asistente_sdk import Asistente

    _cargar(sync_engine, organizacion["id"], Decimal("1.000000"))
    _correr(lambda s: cerrar_mes(s, organizacion["id"], 2026, 9, ahora=YA_PASO))

    r = Asistente(api_key=organizacion["api_key"], http_client=http).consumo(
        anio=2026, mes=9
    )

    assert r.cerrado is True
    assert r.a_facturar_usd == Decimal("1.15")
