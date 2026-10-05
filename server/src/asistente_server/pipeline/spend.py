"""El tope de gasto mensual.

Es la primera capa del pipeline a proposito: es la unica que ahorra plata. Corre
antes del filtro de tema, asi que una organizacion pasada de tope no paga ni el
filtro.

Usa la misma consulta que la facturacion, en `billing.py`. Si el tope se calcula
con una suma y la factura con otra, en algun momento van a discrepar y nadie va
a saber cual esta bien.

**Compara contra lo que paga la organizacion, con el markup aplicado**, porque es
el numero que la organizacion acordo. Si se prefiere topear lo que gastamos
nosotros con el proveedor, es cambiar `a_facturar_exacto` por `costo_proveedor`
abajo.

Usa `a_facturar_exacto`, de 6 decimales, y no el monto de la factura: con montos
chicos todo lo que no llega a medio centavo se redondea a US$ 0,00 y el tope no
se enteraria de nada.

**Un tope de US$ 0,00 bloquea todo.** La columna tiene 2 decimales, asi que
cualquier valor menor a un centavo se guarda como 0,00. Es util para suspender
una organizacion, y es un error facil de cometer queriendo poner un tope chico.

Dos limites que conviene tener claros:

* El tope mira lo **ya gastado**: no puede saber cuanto va a costar el pedido que
  esta entrando. O sea, corta el pedido siguiente al que paso el tope, no al que
  lo pasa. Para que corte antes habria que estimar el costo del pedido, y
  estimarlo con un tokenizer local es justo lo que no se hace en este proyecto.
* Los pedidos sin costo registrado —los que el proveedor no informo— no suman al
  total, asi que no acercan al tope. Quedan contados aparte en el resumen.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..billing import gastado_este_mes
from .interfaces import RequestContext, SpendGuard

log = logging.getLogger(__name__)


class MonthlySpendGuard(SpendGuard):
    async def check(self, session: AsyncSession, ctx: RequestContext) -> str | None:
        resumen = await gastado_este_mes(session, ctx.organization_id)
        if resumen.tope_usd is None:
            return None

        gastado = resumen.a_facturar_exacto
        if gastado < resumen.tope_usd:
            return None

        log.warning(
            "la organizacion %s llego al tope del mes: US$ %s facturados de US$ %s",
            ctx.organization_id,
            gastado,
            resumen.tope_usd,
        )
        return (
            f"La organizacion llego a su tope de gasto del mes: "
            f"US$ {gastado} facturados de US$ {resumen.tope_usd} disponibles."
        )
