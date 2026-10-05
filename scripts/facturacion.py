"""Resumen de consumo del mes, por organizacion, con el markup aplicado.

    uv run python scripts/facturacion.py                  # el mes en curso, todas
    uv run python scripts/facturacion.py --anio 2026 --mes 9
    uv run python scripts/facturacion.py --org <uuid>

Los montos salen del mismo codigo que usa el tope de gasto, en
`asistente_server.billing`. El corte del mes es en UTC.

**La columna `sin costo` no es decorativa.** Son pedidos donde el proveedor no
informo el uso, asi que no tienen costo registrado y **no estan sumados en el
total**. Si ese numero no es cero, la factura sale de menos y hay que mirar el
log de esos pedidos antes de cobrar.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone

from sqlalchemy import select

from asistente_server.billing import consumo_del_mes
from asistente_server.db import get_sessionmaker
from asistente_server.models import Organization
from asistente_server.runtime import configurar_event_loop

ETAPAS = ("filter", "embedding", "answer", "verification")


async def correr(anio: int, mes: int, solo: str | None) -> None:
    async with get_sessionmaker()() as session:
        stmt = select(Organization.id).order_by(Organization.name)
        if solo:
            stmt = stmt.where(Organization.id == solo)
        ids = (await session.execute(stmt)).scalars().all()

        if not ids:
            print("No hay organizaciones" + (f" con id {solo}" if solo else ""))
            return

        print(f"\nConsumo de {mes:02d}/{anio}  (corte en UTC)\n")
        encabezado = (
            f"{'organizacion':<26}{'pedidos':>8}{'sin costo':>11}"
            f"{'proveedor':>13}{'markup':>9}{'a facturar':>13}"
        )
        print(encabezado)
        print("-" * len(encabezado))

        total_proveedor = total_facturar = 0
        for org_id in ids:
            r = await consumo_del_mes(session, org_id, anio, mes)
            nombre = r.organization_name[:25]
            print(
                f"{nombre:<26}{r.pedidos:>8}{r.pedidos_sin_costo:>11}"
                f"{f'US$ {r.costo_proveedor:.6f}':>13}"
                f"{f'{r.markup_pct:.2f} %':>9}"
                f"{f'US$ {r.a_facturar:.2f}':>13}"
            )
            detalle = " · ".join(
                f"{etapa} US$ {r.por_etapa[etapa]:.6f}"
                for etapa in ETAPAS
                if etapa in r.por_etapa
            )
            if detalle:
                print(f"{'':<26}{detalle}")
            if r.pedidos_sin_costo:
                print(
                    f"{'':<26}ATENCION: {r.pedidos_sin_costo} pedido(s) sin costo "
                    "registrado, no estan en el total"
                )
            total_proveedor += r.costo_proveedor
            total_facturar += r.a_facturar

        print("-" * len(encabezado))
        print(
            f"{'total':<26}{'':>8}{'':>11}"
            f"{f'US$ {total_proveedor:.6f}':>13}{'':>9}"
            f"{f'US$ {total_facturar:.2f}':>13}"
        )
        print()


def main() -> None:
    ahora = datetime.now(timezone.utc)
    parser = argparse.ArgumentParser(description="Resumen de consumo del mes")
    parser.add_argument("--anio", type=int, default=ahora.year)
    parser.add_argument("--mes", type=int, default=ahora.month)
    parser.add_argument("--org", default=None, help="uuid de una organizacion")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("Falta DATABASE_URL")

    configurar_event_loop()
    asyncio.run(correr(args.anio, args.mes, args.org))


if __name__ == "__main__":
    main()
