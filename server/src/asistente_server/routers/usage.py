"""Consumo del mes de la organizacion que pregunta.

Cada organizacion ve **solo lo suyo**: el resumen se arma con la organizacion que
sale de la API key, no con un id que venga en el pedido. Un resumen de otra
organizacion, o de todas, necesita un permiso de administrador que todavia no
existe; para eso esta `scripts/facturacion.py`, que corre contra la base.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_organization
from ..billing import consumo_del_mes
from ..db import get_session
from ..models import Organization
from ..schemas import ConsumoMensualOut

router = APIRouter(prefix="/v1", tags=["consumo"])


@router.get("/usage/summary", response_model=ConsumoMensualOut)
async def usage_summary(
    anio: int | None = Query(default=None, ge=2020, le=2100),
    mes: int | None = Query(default=None, ge=1, le=12),
    org: Organization = Depends(require_organization),
    session: AsyncSession = Depends(get_session),
) -> ConsumoMensualOut:
    ahora = datetime.now(timezone.utc)
    resumen = await consumo_del_mes(
        session, org.id, anio or ahora.year, mes or ahora.month
    )
    return ConsumoMensualOut(
        anio=resumen.anio,
        mes=resumen.mes,
        pedidos=resumen.pedidos,
        pedidos_sin_costo=resumen.pedidos_sin_costo,
        costo_proveedor_usd=str(resumen.costo_proveedor),
        markup_pct=str(resumen.markup_pct),
        a_facturar_usd=str(resumen.a_facturar),
        a_facturar_exacto_usd=str(resumen.a_facturar_exacto),
        por_etapa={etapa: str(monto) for etapa, monto in resumen.por_etapa.items()},
        tope_usd=str(resumen.tope_usd) if resumen.tope_usd is not None else None,
    )
