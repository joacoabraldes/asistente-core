"""Crea una organizacion y su API key.

La key se imprime UNA sola vez: de ahi en adelante solo queda el hash.

    uv run python scripts/create_organization.py --name "Terminal" --model openai/gpt-4o-mini

Sin --temas la organizacion no se filtra: el filtro de pertinencia deja pasar
todo y no llama a ningun modelo.

    ... --temas "bonos,acciones,curvas de tasas,la plataforma"
"""

from __future__ import annotations

import argparse
import os
import uuid
from decimal import Decimal

from sqlalchemy import create_engine, insert

from asistente_server.auth import new_api_key
from asistente_server.models import ApiKey, Organization


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea una organizacion y su API key")
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", required=True, help="proveedor/modelo, por ejemplo openai/gpt-4o-mini")
    parser.add_argument("--markup", default="15.00", help="porcentaje; 15.00 es 15 %%")
    parser.add_argument("--cap", default=None, help="tope mensual en USD")
    parser.add_argument(
        "--temas",
        default="",
        help="temas habilitados separados por coma. Vacio = no se filtra nada",
    )
    parser.add_argument(
        "--filter-model",
        default=None,
        help="modelo del filtro; si no se pasa, usa el mismo que la respuesta",
    )
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Falta DATABASE_URL")

    temas = [t.strip() for t in args.temas.split(",") if t.strip()]

    org_id = uuid.uuid4()
    raw, prefix, key_hash = new_api_key()

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            insert(Organization).values(
                id=org_id,
                name=args.name,
                markup_pct=Decimal(args.markup),
                monthly_cap_usd=Decimal(args.cap) if args.cap else None,
                default_model=args.model,
                allowed_topics=temas,
                filter_model=args.filter_model,
            )
        )
        conn.execute(
            insert(ApiKey).values(
                id=uuid.uuid4(),
                organization_id=org_id,
                key_hash=key_hash,
                prefix=prefix,
            )
        )
    engine.dispose()

    print("Organizacion creada")
    print(f"  id      {org_id}")
    print(f"  nombre  {args.name}")
    print(f"  modelo  {args.model}")
    print(f"  temas   {', '.join(temas) if temas else 'ninguno, el filtro deja pasar todo'}")
    print()
    print("API key (se muestra una sola vez, guardala ahora):")
    print(f"  {raw}")


if __name__ == "__main__":
    main()
