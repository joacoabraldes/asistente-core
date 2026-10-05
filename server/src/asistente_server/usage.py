"""Registro de consumo. Idempotente por request_id.

Reintentar una pregunta no puede cobrarla dos veces: el insert choca contra el
unique de request_id y no hace nada.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .llm import TokenUsage
from .models import Usage


async def record_usage(
    session: AsyncSession,
    *,
    request_id: str,
    organization_id: uuid.UUID,
    external_user_id: str,
    conversation_id: str | None,
    stage: str,
    provider: str,
    model: str,
    tokens: TokenUsage | None,
    price_id: uuid.UUID | None,
    cost_usd: Decimal | None,
) -> bool:
    """Inserta la fila. Devuelve False si ese request_id ya estaba registrado."""
    stmt = (
        pg_insert(Usage)
        .values(
            id=uuid.uuid4(),
            request_id=request_id,
            organization_id=organization_id,
            external_user_id=external_user_id,
            conversation_id=conversation_id,
            stage=stage,
            provider=provider,
            model=model,
            input_tokens=tokens.input_tokens if tokens else None,
            cached_input_tokens=tokens.cached_input_tokens if tokens else None,
            output_tokens=tokens.output_tokens if tokens else None,
            reasoning_tokens=tokens.reasoning_tokens if tokens else None,
            price_id=price_id,
            cost_usd=cost_usd,
        )
        .on_conflict_do_nothing(index_elements=["request_id"])
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)
