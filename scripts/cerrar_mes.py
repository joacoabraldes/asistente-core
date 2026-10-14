"""Cierra un mes: congela el resumen para que no se recalcule nunca mas.

    uv run python scripts/cerrar_mes.py --anio 2026 --mes 9
    uv run python scripts/cerrar_mes.py --anio 2026 --mes 9 --org <uuid>
    uv run python scripts/cerrar_mes.py --anio 2026 --mes 9 --forzar

Sin cierre, el resumen se vuelve a calcular en cada consulta leyendo
`markup_pct` de la organizacion. Eso significa que cambiar el markup hoy cambia
lo que se facturo en marzo, y nadie se enteraria.

Dos cosas que el script se niega a hacer:

* **Cerrar el mes en curso.** Guardaria un total parcial que despues parece
  definitivo, que es exactamente el problema que el cierre viene a resolver.
* **Volver a cerrar** un mes ya cerrado, salvo con `--forzar`. Recerrar es
  legitimo si aparecio consumo atrasado, pero tiene que ser una decision, no el
  efecto de volver a correr el script.

Lo que imprime al cerrar es lo que quedo guardado. Si la columna `sin costo` no
es cero, ese mes tiene consumo real que no se cobro: mirarlo ANTES de cerrar,
porque despues el numero queda fijo.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import select

from asistente_server.billing import MesEnCurso, YaCerrado, cerrar_mes
from asistente_server.db import get_sessionmaker
from asistente_server.models import Organization
from asistente_server.runtime import configurar_event_loop


async def correr(anio: int, mes: int, solo: str | None, forzar: bool) -> int:
    fallos = 0
    async with get_sessionmaker()() as session:
        stmt = select(Organization.id, Organization.name).order_by(Organization.name)
        if solo:
            stmt = stmt.where(Organization.id == solo)
        orgs = (await session.execute(stmt)).all()

        if not orgs:
            print("No hay organizaciones" + (f" con id {solo}" if solo else ""))
            return 1

        print(f"\nCierre de {mes:02d}/{anio}\n")
        for org_id, nombre in orgs:
            try:
                r = await cerrar_mes(session, org_id, anio, mes, forzar=forzar)
            except MesEnCurso as exc:
                print(f"  {nombre:<26} NO SE CIERRA: {exc}")
                return 1
            except YaCerrado as exc:
                print(f"  {nombre:<26} ya estaba: {exc}. Usa --forzar para recerrar")
                fallos += 1
                continue
            aviso = (
                f"  ATENCION {r.pedidos_sin_costo} pedido(s) sin costo"
                if r.pedidos_sin_costo
                else ""
            )
            print(
                f"  {nombre:<26} {r.pedidos:>5} pedidos  "
                f"proveedor US$ {r.costo_proveedor:.6f}  "
                f"a facturar US$ {r.a_facturar:.2f}{aviso}"
            )
        await session.commit()
    print()
    return 1 if fallos else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Cierra un mes de facturacion")
    parser.add_argument("--anio", type=int, required=True)
    parser.add_argument("--mes", type=int, required=True)
    parser.add_argument("--org", default=None, help="uuid de una organizacion")
    parser.add_argument(
        "--forzar", action="store_true", help="recierra un mes ya cerrado"
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("Falta DATABASE_URL")

    configurar_event_loop()
    raise SystemExit(asyncio.run(correr(args.anio, args.mes, args.org, args.forzar)))


if __name__ == "__main__":
    main()
