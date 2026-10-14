"""Precio vigente y calculo del costo. Todo en Decimal, nunca float."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .llm import TokenUsage
from .models import ModelPrice

MTOK = Decimal(1_000_000)
SEIS_DECIMALES = Decimal("0.000001")


class PriceNotFound(RuntimeError):
    """Falta el precio del modelo. No se cobra 0: eso se descubre en la factura."""


def split_model(full_model: str) -> tuple[str, str]:
    """De "openai/gpt-4o-mini" saca el proveedor y el modelo."""
    if "/" not in full_model:
        raise ValueError(
            "El modelo tiene que venir como proveedor/modelo, llego: " + repr(full_model)
        )
    provider, model = full_model.split("/", 1)
    return provider, model


async def current_price(
    session: AsyncSession, provider: str, model: str, at: datetime | None = None
) -> ModelPrice:
    at = at or datetime.now(timezone.utc)
    stmt = (
        select(ModelPrice)
        .where(
            ModelPrice.provider == provider,
            ModelPrice.model == model,
            ModelPrice.valid_from <= at,
        )
        .order_by(ModelPrice.valid_from.desc())
        .limit(1)
    )
    price = (await session.execute(stmt)).scalar_one_or_none()
    if price is None:
        raise PriceNotFound(
            f"No hay precio cargado para {provider}/{model}. "
            "Cargalo en model_prices antes de usar ese modelo."
        )
    return price


# =========================================================== CRITICO 2 de 5 ===
# QUE HACE     el costo de una llamada, en dolares
# FORMULA      (entrada - cacheados)/1e6 x precio_entrada
#              + cacheados/1e6 x precio_cacheado
#              + salida/1e6 x precio_salida,  a 6 decimales ROUND_HALF_UP
# POR QUE ASI  los cacheados se cobran aparte y mas baratos, asi que restarlos
#              de la entrada no es opcional: contarlos dos veces infla la
#              factura. Los de razonamiento NO se suman: el proveedor ya los
#              incluye en los de salida.
# LO VERIFICA  server/tests/test_costo_y_pipeline.py::test_costo_con_numeros_a_mano
#              y test_los_reasoning_tokens_no_se_cobran_aparte
# FALLA ASI    un factor 1e6 mal puesto da un costo mil veces mayor o menor y
#              sigue siendo un numero. Se descubre en la factura del proveedor,
#              no antes.
# ============================================================================
def compute_cost(price: ModelPrice, usage: TokenUsage) -> Decimal:
    """Costo del proveedor, sin markup.

    Los reasoning_tokens no se cobran aparte: los proveedores ya los cuentan
    dentro de los tokens de salida, asi que sumarlos seria contar dos veces.
    """
    cached = Decimal(usage.cached_input_tokens or 0)
    billable_input = Decimal(usage.input_tokens or 0) - cached
    if billable_input < 0:
        billable_input = Decimal(0)
    output = Decimal(usage.output_tokens or 0)
    cached_rate = (
        price.cached_input_per_mtok
        if price.cached_input_per_mtok is not None
        else price.input_per_mtok
    )
    total = (
        billable_input / MTOK * price.input_per_mtok
        + cached / MTOK * cached_rate
        + output / MTOK * price.output_per_mtok
    )
    return total.quantize(SEIS_DECIMALES, rounding=ROUND_HALF_UP)
