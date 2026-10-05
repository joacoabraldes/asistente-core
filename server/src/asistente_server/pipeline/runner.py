"""Orquesta las capas y emite los eventos.

El orden es el del producto y no cambia cuando se reemplacen las
implementaciones por defecto:

    gasto -> pertinencia -> documentos -> LLM con herramientas

El precio se busca ANTES de llamar al proveedor: si falta, no se gasta un token
y no queda una fila con costo cero.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import AsyncIterator, Sequence

from .. import llm
from ..db import get_sessionmaker
from ..models import Message
from ..pricing import PriceNotFound, compute_cost, current_price, split_model
from ..schemas import DoneOut, UsageOut
from ..sse import event
from ..usage import record_usage
from .defaults import AllowAllScopeFilter, AllowAllSpendGuard, NoDocuments, NoTools
from .interfaces import (
    DocumentRetriever,
    RequestContext,
    RetrievedChunk,
    ScopeFilter,
    SpendGuard,
    ToolRegistry,
)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Sos un asistente de la plataforma. Respondes unicamente con la informacion "
    "del contexto que se te da y sobre los temas habilitados para la "
    "organizacion. Si la respuesta no esta en el contexto, deci que no lo sabes "
    "en lugar de inventarla. No prometes nada en nombre de la empresa ni das "
    "recomendaciones de inversion."
)


class Pipeline:
    def __init__(
        self,
        spend_guard: SpendGuard | None = None,
        scope_filter: ScopeFilter | None = None,
        retriever: DocumentRetriever | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.spend_guard = spend_guard or AllowAllSpendGuard()
        self.scope_filter = scope_filter or AllowAllScopeFilter()
        self.retriever = retriever or NoDocuments()
        self.tools = tools or NoTools()

    async def run(
        self, ctx: RequestContext, history: Sequence[Message]
    ) -> AsyncIterator[str]:
        async with get_sessionmaker()() as session:
            denial = await self.spend_guard.check(session, ctx)
            if denial:
                yield event("error", {"code": "spend_cap_reached", "message": denial})
                return

            try:
                decision = await self.scope_filter.evaluate(session, ctx)
            except PriceNotFound as exc:
                log.error("sin precio para el modelo del filtro: %s", exc)
                yield event("error", {"code": "price_not_found", "message": str(exc)})
                return

            # El filtro puede haber gastado tokens: se confirma ya, sin esperar
            # la respuesta, asi el cobro no se pierde si la respuesta falla.
            await session.commit()

            if not decision.allowed:
                yield event(
                    "out_of_scope",
                    {"reason": decision.reason or "la pregunta no es de los temas habilitados"},
                )
                return

            chunks = await self.retriever.search(session, ctx)
            provider, model = split_model(ctx.model)

            try:
                price = await current_price(session, provider, model)
            except PriceNotFound as exc:
                log.error("sin precio para %s/%s: %s", provider, model, exc)
                yield event("error", {"code": "price_not_found", "message": str(exc)})
                return

            messages = self._build_messages(history, chunks)
            specs = self.tools.specs()

            parts: list[str] = []
            tokens: llm.TokenUsage | None = None
            try:
                stream = llm.stream_completion(
                    model=ctx.model, messages=messages, tools=specs or None
                )
                async for item in stream:
                    if isinstance(item, llm.TextChunk):
                        parts.append(item.text)
                        yield event("token", {"text": item.text})
                    else:
                        tokens = item
            except Exception as exc:  # noqa: BLE001 - el detalle va al log
                log.exception("fallo la llamada al proveedor")
                yield event("error", {"code": "provider_error", "message": str(exc)})
                return

            answer = "".join(parts)
            cost = None
            if tokens is None:
                log.warning(
                    "el proveedor %s no informo uso de tokens (request_id=%s): la fila de "
                    "consumo queda sin tokens ni costo",
                    provider,
                    ctx.request_id,
                )
            else:
                cost = compute_cost(price, tokens)

            session.add(
                Message(
                    organization_id=ctx.organization_id,
                    conversation_id=ctx.conversation_id,
                    role="assistant",
                    content=answer,
                )
            )
            await record_usage(
                session,
                request_id=ctx.request_id,
                organization_id=ctx.organization_id,
                external_user_id=ctx.external_user_id,
                conversation_id=ctx.conversation_id,
                stage="answer",
                provider=provider,
                model=model,
                tokens=tokens,
                price_id=price.id if tokens is not None else None,
                cost_usd=cost,
            )
            await session.commit()

            done = DoneOut(
                request_id=ctx.request_id,
                provider=provider,
                model=model,
                usage=UsageOut(**dataclasses.asdict(tokens)) if tokens else None,
                cost_usd=str(cost) if cost is not None else None,
            )
            yield event("done", done.model_dump())

    def _build_messages(
        self, history: Sequence[Message], chunks: Sequence[RetrievedChunk]
    ) -> list[dict[str, str]]:
        """El historial ya incluye la pregunta: el endpoint la guardo antes."""
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if chunks:
            contexto = "\n\n".join(f"[{c.source}] {c.text}" for c in chunks)
            messages.append(
                {
                    "role": "system",
                    "content": "Contexto de los documentos de la organizacion:\n" + contexto,
                }
            )
        messages.extend({"role": m.role, "content": m.content} for m in history)
        return messages
