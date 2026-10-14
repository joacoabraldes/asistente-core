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

from .models import MonthlyClosing, Organization, Usage

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
    # True cuando los numeros salen de un cierre guardado y no de sumar `usage`.
    cerrado: bool = False
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
    """Si el mes esta cerrado devuelve el cierre; si no, suma `usage`.

    Es en ese orden a proposito: un mes cerrado tiene que dar siempre el mismo
    numero, aunque despues cambie el markup de la organizacion o aparezca una
    fila de consumo atrasada.
    """
    org = await session.get(Organization, organization_id)
    if org is None:
        raise ValueError(f"No existe la organizacion {organization_id}")

    cierre = await session.get(MonthlyClosing, (organization_id, anio, mes))
    if cierre is not None:
        return ConsumoMensual(
            organization_id=organization_id,
            organization_name=org.name,
            anio=anio,
            mes=mes,
            markup_pct=cierre.markup_pct,
            costo_proveedor=cierre.costo_proveedor_usd,
            por_etapa={k: Decimal(v) for k, v in cierre.por_etapa.items()},
            pedidos=cierre.pedidos,
            pedidos_sin_costo=cierre.pedidos_sin_costo,
            tope_usd=org.monthly_cap_usd,
            cerrado=True,
        )
    return await _sumar_usage(session, organization_id, anio, mes)


# =========================================================== CRITICO 4 de 5 ===
# QUE HACE     el total del mes de una organizacion, y lo que se le factura
# FORMULA      costo = coalesce(sum(cost_usd), 0) agrupado por stage
#              a_facturar = costo x (100 + markup_pct) / 100, redondeado UNA
#              sola vez al final
# POR QUE ASI  sum() ignora los NULL, asi que los pedidos que el proveedor no
#              informo desapareceran del total si no se cuentan aparte: por eso
#              `pedidos_sin_costo` sale en el resumen. Y el redondeo va al
#              final porque redondear por pedido pierde centavos.
# LO VERIFICA  server/tests/test_facturacion.py::test_el_total_a_facturar_con_numeros_a_mano
#              y test_el_markup_se_aplica_una_sola_vez_al_final
# FALLA ASI    si no se mira `pedidos_sin_costo`, la factura sale de menos y
#              nada avisa: el total es un numero perfectamente plausible.
# ============================================================================
async def _sumar_usage(
    session: AsyncSession, organization_id: uuid.UUID, anio: int, mes: int
) -> ConsumoMensual:
    """Suma `usage` del mes, ignorando si hay cierre guardado."""
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


class MesEnCurso(RuntimeError):
    """No se cierra un mes que todavia no termino: el total seria parcial."""


class YaCerrado(RuntimeError):
    """El mes ya tiene un cierre guardado."""


async def cerrar_mes(
    session: AsyncSession,
    organization_id: uuid.UUID,
    anio: int,
    mes: int,
    *,
    forzar: bool = False,
    ahora: datetime | None = None,
) -> ConsumoMensual:
    """Congela el resumen del mes. Devuelve lo que quedo guardado.

    Dos negativas a proposito:

    * **No cierra un mes que no termino.** Cerrar el mes en curso guardaria un
      total parcial que despues parece definitivo, que es justo el error que
      este cierre viene a evitar.
    * **No vuelve a cerrar** uno ya cerrado, salvo `forzar=True`. Recerrar es
      legitimo si aparecio consumo atrasado, pero tiene que ser una decision
      explicita y no un efecto de volver a correr el script.
    """
    ahora = ahora or datetime.now(timezone.utc)
    _, hasta = limites_del_mes(anio, mes)
    if ahora < hasta:
        raise MesEnCurso(
            f"El mes {mes:02d}/{anio} termina el {hasta.date()}: todavia no se puede cerrar"
        )

    existente = await session.get(MonthlyClosing, (organization_id, anio, mes))
    if existente is not None and not forzar:
        raise YaCerrado(
            f"El mes {mes:02d}/{anio} ya se cerro el {existente.cerrado_at.date()}"
        )

    # Se calcula desde `usage`, no desde consumo_del_mes, que devolveria el
    # cierre anterior cuando se esta forzando.
    resumen = await _sumar_usage(session, organization_id, anio, mes)
    if existente is not None:
        await session.delete(existente)
        await session.flush()

    session.add(
        MonthlyClosing(
            organization_id=organization_id,
            anio=anio,
            mes=mes,
            markup_pct=resumen.markup_pct,
            costo_proveedor_usd=resumen.costo_proveedor,
            a_facturar_usd=resumen.a_facturar,
            pedidos=resumen.pedidos,
            pedidos_sin_costo=resumen.pedidos_sin_costo,
            por_etapa={k: str(v) for k, v in resumen.por_etapa.items()},
        )
    )
    resumen.cerrado = True
    return resumen
