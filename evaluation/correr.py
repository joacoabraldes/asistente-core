"""Mide el filtro de pertinencia contra un set de preguntas con respuesta conocida.

    uv run python evaluation/correr.py --filtro palabras   # sin modelo, sin clave, sin base
    uv run python evaluation/correr.py --filtro modelo     # el filtro real

Lo que importa no es el porcentaje de aciertos, son los dos errores, que cuestan
cosas distintas:

* **preguntas legitimas bloqueadas**: el usuario no reclama, se va. Es el costo
  que no se ve en ninguna metrica del sistema.
* **preguntas fuera de tema que pasaron**: se paga una respuesta que no
  correspondia y el bot habla de lo que no tiene que hablar.

Un filtro que bloquea todo tiene cero del segundo error y es inservible. Por eso
el umbral se fija con los dos numeros a la vista y no a ojo.

El camino `modelo` corre el filtro de produccion contra la base, asi que cada
pregunta deja su fila en `usage` con `stage='filter'` y el costo lo calcula el
mismo codigo que va a facturar. Necesita `DATABASE_URL`, el precio del modelo
cargado en `model_prices`, y la clave del proveedor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SET = Path(__file__).with_name("set_preguntas.json")

ORGANIZACION_DE_PRUEBA = "evaluacion del filtro"


def cargar() -> dict:
    return json.loads(SET.read_text(encoding="utf-8"))


# --- medicion ----------------------------------------------------------------


def medir_por_palabras(datos: dict) -> list[dict]:
    from asistente_server.pipeline.scope import decision_por_palabras

    salida = []
    for pregunta in datos["preguntas"]:
        arranque = time.perf_counter()
        decision = decision_por_palabras(datos["temas"], pregunta["texto"])
        salida.append(
            {
                **pregunta,
                "obtenido": "permitido" if decision.allowed else "no_permitido",
                "ms": (time.perf_counter() - arranque) * 1000,
            }
        )
    return salida


async def _medir_con_modelo(datos: dict, modelo: str, corrida: str) -> list[dict]:
    from sqlalchemy import select, update

    from asistente_server.db import get_sessionmaker
    from asistente_server.models import Organization
    from asistente_server.pipeline.interfaces import RequestContext
    from asistente_server.pipeline.scope import ModelScopeFilter

    filtro = ModelScopeFilter()
    salida: list[dict] = []

    async with get_sessionmaker()() as session:
        existente = (
            await session.execute(
                select(Organization).where(Organization.name == ORGANIZACION_DE_PRUEBA)
            )
        ).scalar_one_or_none()
        if existente is None:
            org_id = uuid.uuid4()
            session.add(
                Organization(
                    id=org_id,
                    name=ORGANIZACION_DE_PRUEBA,
                    default_model=modelo,
                    allowed_topics=list(datos["temas"]),
                    filter_model=modelo,
                )
            )
        else:
            org_id = existente.id
            await session.execute(
                update(Organization)
                .where(Organization.id == org_id)
                .values(
                    allowed_topics=list(datos["temas"]),
                    filter_model=modelo,
                    default_model=modelo,
                )
            )
        await session.commit()

        for i, pregunta in enumerate(datos["preguntas"]):
            ctx = RequestContext(
                organization_id=org_id,
                conversation_id=None,
                external_user_id="evaluacion",
                question=pregunta["texto"],
                model=modelo,
                request_id=f"eval:{corrida}:{i:03d}",
            )
            arranque = time.perf_counter()
            decision = await filtro.evaluate(session, ctx)
            ms = (time.perf_counter() - arranque) * 1000
            await session.commit()
            salida.append(
                {
                    **pregunta,
                    "obtenido": "permitido" if decision.allowed else "no_permitido",
                    "motivo": decision.reason,
                    "ms": ms,
                }
            )
    return salida


async def _costo_de_la_corrida(corrida: str) -> tuple[int, Decimal | None]:
    from sqlalchemy import text

    from asistente_server.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        fila = (
            await session.execute(
                text(
                    'SELECT count(*) AS n, sum(cost_usd) AS total FROM "usage" '
                    "WHERE stage = 'filter' AND request_id LIKE :patron"
                ),
                {"patron": f"eval:{corrida}:%"},
            )
        ).one()
    return fila.n, fila.total


# --- informe -----------------------------------------------------------------


def informar(datos: dict, resultados: list[dict], etiqueta: str, costo: str) -> None:
    categorias: dict[str, list[dict]] = {}
    for r in resultados:
        categorias.setdefault(r["categoria"], []).append(r)

    print()
    print(f"set        {len(resultados)} preguntas · {len(datos['temas'])} temas")
    print(f"filtro     {etiqueta}")
    print()
    print(f"{'categoria':<18}{'n':>4}{'permitio':>10}{'bloqueo':>9}{'aciertos':>10}")
    for nombre, filas in categorias.items():
        permitio = sum(1 for f in filas if f["obtenido"] == "permitido")
        aciertos = sum(1 for f in filas if f["obtenido"] == f["esperado"])
        print(
            f"{nombre:<18}{len(filas):>4}{permitio:>10}{len(filas) - permitio:>9}"
            f"{f'{aciertos}/{len(filas)}':>10}"
        )

    legitimas = [r for r in resultados if r["esperado"] == "permitido"]
    ajenas = [r for r in resultados if r["esperado"] == "no_permitido"]
    bloqueadas = [r for r in legitimas if r["obtenido"] == "no_permitido"]
    pasaron = [r for r in ajenas if r["obtenido"] == "permitido"]
    aciertos = sum(1 for r in resultados if r["obtenido"] == r["esperado"])

    print()
    print(_linea("legitimas bloqueadas", len(bloqueadas), len(legitimas)))
    print(_linea("fuera de tema que pasaron", len(pasaron), len(ajenas)))
    print(_linea("aciertos", aciertos, len(resultados)))
    print()
    mediana = statistics.median(r["ms"] for r in resultados)
    print(f"{'latencia mediana':<28}{mediana:>8.0f} ms   medido")
    print(f"{'costo del filtro':<28}{costo:>11}")

    if bloqueadas:
        print()
        print("preguntas legitimas que bloqueo:")
        for r in bloqueadas:
            print(f"  - {r['texto']}")
    if pasaron:
        print()
        print("preguntas fuera de tema que dejo pasar:")
        for r in pasaron:
            print(f"  - {r['texto']}")


def _linea(nombre: str, parte: int, total: int) -> str:
    pct = (parte / total * 100) if total else 0.0
    return f"{nombre:<28}{f'{parte} / {total}':>10}{pct:>9.1f} %"


# --- entrada -----------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--filtro",
        choices=("palabras", "modelo"),
        default="palabras",
        help="palabras: linea de base sin modelo. modelo: el filtro de produccion",
    )
    parser.add_argument(
        "--modelo",
        default="openai/gpt-4o-mini",
        help="proveedor/modelo para --filtro modelo",
    )
    args = parser.parse_args()

    datos = cargar()

    if args.filtro == "palabras":
        informar(datos, medir_por_palabras(datos), "palabras (sin modelo)", "US$ 0,000000")
        return

    from asistente_server.runtime import configurar_event_loop

    configurar_event_loop()
    corrida = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    resultados = asyncio.run(_medir_con_modelo(datos, args.modelo, corrida))
    filas, total = asyncio.run(_costo_de_la_corrida(corrida))
    costo = f"US$ {total:.6f}" if total is not None else "sin dato"
    if filas != len(resultados):
        costo += f"  ({filas} de {len(resultados)} filas con consumo)"
    informar(datos, resultados, f"modelo {args.modelo}", costo)
    print(f"corrida    eval:{corrida}")


if __name__ == "__main__":
    main()
