"""Endpoint de mensajes.

La pregunta se guarda antes de llamar al proveedor, asi queda registrada aunque
la respuesta falle. La respuesta y el consumo se guardan al terminar el stream.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
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
from ..schemas import MessageIn, ToolResultsIn

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


@router.post("/conversations/{conversation_id}/tool_results")
async def post_tool_results(
    conversation_id: str,
    body: ToolResultsIn,
    org: Organization = Depends(require_organization),
    session: AsyncSession = Depends(get_session),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> StreamingResponse:
    """Segunda vuelta: el cliente ejecuto la herramienta y trae el resultado.

    **El stream de la primera vuelta ya se cerro.** Cuando el modelo pide una
    herramienta, el servidor emite `tool_call` y termina; el cliente ejecuta lo
    que tenga que ejecutar y vuelve por aca con un pedido nuevo, que abre su
    propio stream.

    Es asi y no con el stream abierto esperando porque de esta forma el estado
    pendiente vive en la base: funciona con varios workers, sobrevive a que se
    reinicie el servidor, y no necesita que el proceso que atendio la primera
    vuelta sea el que atienda la segunda.

    Cada vuelta es un pedido con su propio `request_id`, asi que tiene su propia
    fila de consumo. Son dos llamadas al proveedor y se cobran las dos.
    """
    request_id = x_request_id or str(uuid.uuid4())

    # El ultimo turno del asistente tiene que ser el que pidio las herramientas.
    # Sin esta verificacion, un cliente podria inyectar resultados de
    # herramientas que el modelo nunca pidio, y eso entra al prompt.
    ultimo = (
        await session.execute(
            select(Message)
            .where(
                Message.organization_id == org.id,
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    pendientes = {tc["id"] for tc in (ultimo.tool_calls or [])} if ultimo else set()
    if not pendientes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta conversacion no tiene ningun pedido de herramienta pendiente",
        )
    desconocidos = [r.tool_call_id for r in body.results if r.tool_call_id not in pendientes]
    if desconocidos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"tool_call_id que no fueron pedidos: {', '.join(desconocidos)}",
        )

    for r in body.results:
        session.add(
            Message(
                organization_id=org.id,
                conversation_id=conversation_id,
                role="tool",
                content=r.content,
                tool_call_id=r.tool_call_id,
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

    ctx = RequestContext(
        organization_id=org.id,
        conversation_id=conversation_id,
        external_user_id=body.external_user_id,
        question="",
        model=org.default_model,
        request_id=request_id,
    )
    return StreamingResponse(
        pipeline.continuar(ctx, list(reversed(recent))),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
