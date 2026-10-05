"""Endpoint de mensajes.

La pregunta se guarda antes de llamar al proveedor, asi queda registrada aunque
la respuesta falle. La respuesta y el consumo se guardan al terminar el stream.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_organization
from ..config import get_settings
from ..db import get_session
from ..models import Conversation, Message, Organization
from ..pipeline.interfaces import RequestContext
from ..pipeline.runner import Pipeline
from ..pipeline.scope import ModelScopeFilter
from ..pipeline.spend import MonthlySpendGuard
from ..schemas import MessageIn

router = APIRouter(prefix="/v1", tags=["mensajes"])

# Las dos primeras capas ya son las reales, y las dos se encienden cargando una
# columna de la organizacion, no desplegando codigo: `monthly_cap_usd` para el
# tope de gasto y `allowed_topics` para el filtro de tema. En NULL y vacio
# respectivamente, dejan pasar sin costo.
pipeline = Pipeline(
    spend_guard=MonthlySpendGuard(),
    scope_filter=ModelScopeFilter(),
)


@router.post("/conversations/{conversation_id}/messages")
async def post_message(
    conversation_id: str,
    body: MessageIn,
    org: Organization = Depends(require_organization),
    session: AsyncSession = Depends(get_session),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> StreamingResponse:
    request_id = x_request_id or str(uuid.uuid4())

    # La conversacion se crea en la primera pregunta. La clave es
    # (organizacion, id), asi que dos organizaciones pueden usar el mismo id.
    await session.execute(
        pg_insert(Conversation)
        .values(
            organization_id=org.id,
            id=conversation_id,
            external_user_id=body.external_user_id,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "id"])
    )
    session.add(
        Message(
            organization_id=org.id,
            conversation_id=conversation_id,
            role="user",
            content=body.content,
        )
    )
    await session.commit()

    limit = get_settings().history_messages
    recent = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.organization_id == org.id,
                    Message.conversation_id == conversation_id,
                )
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    history = list(reversed(recent))

    ctx = RequestContext(
        organization_id=org.id,
        conversation_id=conversation_id,
        external_user_id=body.external_user_id,
        question=body.content,
        model=org.default_model,
        request_id=request_id,
    )
    return StreamingResponse(
        pipeline.run(ctx, history),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
