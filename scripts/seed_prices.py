"""Carga precios de ejemplo en model_prices.

Los precios NO se pisan: cada cambio es una fila nueva con otro valid_from. Este
script agrega una fila por modelo con vigencia desde 2020, para poder probar.

ATENCION: son valores de ejemplo. Antes de facturar, verificalos contra la lista
de precios del proveedor.

    uv run python scripts/seed_prices.py
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from asistente_server.models import ModelPrice

DESDE = datetime(2020, 1, 1, tzinfo=timezone.utc)

# proveedor, modelo, entrada, entrada cacheada, salida  (USD por millon de tokens)
PRECIOS = [
    ("openai", "gpt-4o-mini", Decimal("0.150000"), Decimal("0.075000"), Decimal("0.600000")),
    ("openai", "gpt-4o", Decimal("2.500000"), Decimal("1.250000"), Decimal("10.000000")),
]


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Falta DATABASE_URL")

    engine = create_engine(url)
    with engine.begin() as conn:
        for provider, model, entrada, cacheada, salida in PRECIOS:
            existe = conn.execute(
                select(ModelPrice.id).where(
                    ModelPrice.provider == provider,
                    ModelPrice.model == model,
                    ModelPrice.valid_from == DESDE,
                )
            ).scalar_one_or_none()
            if existe:
                print(f"ya estaba  {provider}/{model}")
                continue
            conn.execute(
                pg_insert(ModelPrice).values(
                    id=uuid.uuid4(),
                    provider=provider,
                    model=model,
                    input_per_mtok=entrada,
                    cached_input_per_mtok=cacheada,
                    output_per_mtok=salida,
                    valid_from=DESDE,
                )
            )
            print(f"cargado    {provider}/{model}")
    engine.dispose()


if __name__ == "__main__":
    main()
