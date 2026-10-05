"""Resumen de consumo por organizacion y por mes, y el total a facturar.

Una sola consulta sirve para las dos cosas que la usan: la facturacion y el tope
de gasto. Por eso vive aca y no adentro de ninguna de las dos.

Dos cuidados que hacen la diferencia entre un total correcto y uno que parece
correcto:

* **`sum()` ignora los NULL.** Cuando el proveedor no informa uso, la fila queda
  con `cost_usd` en NULL a proposito: es un agujero visible. Pero si se suma sin
  mirar, ese agujero desaparece del total y la factura sale de menos sin que
  nada avise. Por eso el resumen devuelve `pedidos_sin_costo` aparte, y quien
  factura decide si cobra igual o lo investiga.
* **El costo del proveedor va en 6 decimales y la factura en 2.** Se redondea
  una sola vez, al final, despues de aplicar el markup. Redondear antes acumula
  diferencia por pedido.

El mes se corta en **UTC**, que es como esta guardado `created_at`. En Argentina
eso mueve al mes siguiente los pedidos de las ultimas tres horas del ultimo dia.
Si hay que cortar en hora local, es un `AT TIME ZONE` en la consulta.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Organization, Usage

CENTAVOS = Decimal("0.01")
SEIS_DECIMALES = Decimal("0.000001")
CIEN = Decimal(100)


@dataclass(slots=True)
class ConsumoMensual:
    organization_id: uuid.UUID
    organization_name: str
    anio: int
    mes: int
    markup_pct: Decimal
    costo_proveedor: Decimal
    por_etapa: dict[str, Decimal] = field(default_factory=dict)
    pedidos: int = 0
    pedidos_sin_costo: int = 0
    # Viene de la misma fila de la organizacion, asi el tope de gasto no
    # necesita una segunda consulta.
    tope_usd: Decimal | None = None

    @property
    def _bruto(self) -> Decimal:
        """Costo del proveedor con el markup, sin redondear."""
        return self.costo_proveedor * (CIEN + self.markup_pct) / CIEN

    @property
    def a_facturar_exacto(self) -> Decimal:
        """Lo facturado en la misma precision que el costo: 6 decimales.

        Es el numero que usa el tope de gasto. Comparar el tope contra la version
        redondeada a centavos no sirve con montos chicos: todo lo que no llega a
        medio centavo se compara como US$ 0,00.
        """
        return self._bruto.quantize(SEIS_DECIMALES, rounding=ROUND_HALF_UP)

    @property
    def a_facturar(self) -> Decimal:
        """Lo que va a la factura, en centavos.

        Sale del bruto, no de `a_facturar_exacto`: redondear dos veces mueve el
        ultimo centavo en los casos justos.
        """
        return self._bruto.quantize(CENTAVOS, rounding=ROUND_HALF_UP)

    @property
    def markup_usd(self) -> Decimal:
        """Contra el exacto, no contra la factura.

        Restarle un costo de 6 decimales a una factura de 2 da negativo cuando
        el monto todavia no llega al centavo.
        """
        return self.a_facturar_exacto - self.costo_proveedor


def limites_del_mes(anio: int, mes: int) -> tuple[datetime, datetime]:
    """Desde el primer instante del mes hasta el primero del siguiente, en UTC.

    Medio abierto a proposito: `>= desde` y `< hasta`. Con `<=` al ultimo
    instante del mes, un pedido registrado en ese microsegundo entra en los dos
    meses.
    """
    if not 1 <= mes <= 12:
        raise ValueError(f"Mes invalido: {mes}")
    desde = datetime(anio, mes, 1, tzinfo=timezone.utc)
    hasta = (
        datetime(anio + 1, 1, 1, tzinfo=timezone.utc)
        if mes == 12
        else datetime(anio, mes + 1, 1, tzinfo=timezone.utc)
    )
    return desde, hasta


async def consumo_del_mes(
    session: AsyncSession, organization_id: uuid.UUID, anio: int, mes: int
) -> ConsumoMensual:
    org = await session.get(Organization, organization_id)
    if org is None:
        raise ValueError(f"No existe la organizacion {organization_id}")

    desde, hasta = limites_del_mes(anio, mes)
    filas = (
        await session.execute(
            select(
                Usage.stage,
                func.coalesce(func.sum(Usage.cost_usd), Decimal(0)).label("costo"),
                func.count().label("pedidos"),
                func.count().filter(Usage.cost_usd.is_(None)).label("sin_costo"),
            )
            .where(
                Usage.organization_id == organization_id,
                Usage.created_at >= desde,
                Usage.created_at < hasta,
            )
            .group_by(Usage.stage)
        )
    ).all()

    resumen = ConsumoMensual(
        organization_id=organization_id,
        organization_name=org.name,
        anio=anio,
        mes=mes,
        markup_pct=org.markup_pct,
        costo_proveedor=Decimal(0),
        tope_usd=org.monthly_cap_usd,
    )
    for fila in filas:
        resumen.por_etapa[fila.stage] = fila.costo
        resumen.costo_proveedor += fila.costo
        resumen.pedidos += fila.pedidos
        resumen.pedidos_sin_costo += fila.sin_costo
    return resumen


async def gastado_este_mes(
    session: AsyncSession, organization_id: uuid.UUID, ahora: datetime | None = None
) -> ConsumoMensual:
    ahora = ahora or datetime.now(timezone.utc)
    return await consumo_del_mes(session, organization_id, ahora.year, ahora.month)
