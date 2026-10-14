"""Orquesta las capas y emite los eventos.

El orden es el del producto y no cambia cuando se reemplacen las
implementaciones por defecto:

    gasto -> pertinencia -> documentos -> LLM con herramientas

El precio se busca ANTES de llamar al proveedor: si falta, no se gasta un token
y no queda una fila con costo cero.
"""

from __future__ import annotations

import dataclasses
import json
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
            async for ev in self._responder(
                session, ctx, messages, self.tools.specs(), price, provider, model
            ):
                yield ev

    async def continuar(
        self, ctx: RequestContext, history: Sequence[Message]
    ) -> AsyncIterator[str]:
        """Segunda vuelta: el cliente ya devolvio el resultado de la herramienta.

        No se vuelve a pasar por el filtro de tema ni por la busqueda de
        documentos: la pregunta ya fue aprobada y el contexto ya quedo en el
        historial. El tope de gasto SI se vuelve a chequear, porque esta vuelta
        cuesta tokens igual que la primera.
        """
        async with get_sessionmaker()() as session:
            denial = await self.spend_guard.check(session, ctx)
            if denial:
                yield event("error", {"code": "spend_cap_reached", "message": denial})
                return

            provider, model = split_model(ctx.model)
            try:
                price = await current_price(session, provider, model)
            except PriceNotFound as exc:
                log.error("sin precio para %s/%s: %s", provider, model, exc)
                yield event("error", {"code": "price_not_found", "message": str(exc)})
                return

            async for ev in self._responder(
                session, ctx, self._build_messages(history, []),
                self.tools.specs(), price, provider, model,
            ):
                yield ev

    async def _responder(
        self,
        session,
        ctx: RequestContext,
        messages: list[dict],
        specs: list,
        price,
        provider: str,
        model: str,
    ) -> AsyncIterator[str]:
        """Llama al proveedor, registra el consumo y cierra.

        Dos salidas posibles y la diferencia importa:

        * **texto** -> se guarda la respuesta y se emite `done`.
        * **pedido de herramienta** -> se guarda el pedido, se emite `tool_call`
          por cada uno y **el stream termina sin `done`**. La respuesta todavia
          no existe: la trae la segunda vuelta, cuando el cliente devuelva el
          resultado por `continuar`.

        En las dos el consumo se registra igual, porque el proveedor cobro igual.
        """
        parts: list[str] = []
        pedidos: list[llm.ToolCallChunk] = []
        tokens: llm.TokenUsage | None = None
        try:
            async for item in llm.stream_completion(
                model=ctx.model, messages=messages, tools=specs or None
            ):
                if isinstance(item, llm.TextChunk):
                    parts.append(item.text)
                    yield event("token", {"text": item.text})
                elif isinstance(item, llm.ToolCallChunk):
                    pedidos.append(item)
                elif isinstance(item, llm.TokenUsage):
                    tokens = item
                else:
                    # Un tipo nuevo en la puerta del proveedor no puede pasar
                    # como si fuera uso de tokens, que es lo que hacia antes.
                    log.error("la puerta del proveedor emitio algo desconocido: %r", item)
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
                tool_calls=[
                    {"id": p.id, "name": p.name, "arguments": p.arguments}
                    for p in pedidos
                ] or None,
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

        if pedidos:
            for p in pedidos:
                if not p.arguments and p.arguments_raw:
                    log.error(
                        "el modelo mando argumentos que no son JSON para %s: %r",
                        p.name,
                        p.arguments_raw[:200],
                    )
                yield event(
                    "tool_call",
                    {"id": p.id, "name": p.name, "arguments": p.arguments},
                )
            return

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
    ) -> list[dict]:
        """El historial ya incluye la pregunta: el endpoint la guardo antes."""
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if chunks:
            contexto = "\n\n".join(f"[{c.source}] {c.text}" for c in chunks)
            messages.append(
                {
                    "role": "system",
                    "content": "Contexto de los documentos de la organizacion:\n" + contexto,
                }
            )
        for m in history:
            if m.role == "tool":
                # El resultado que devolvio el cliente, atado a su pedido.
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id or "",
                        "content": m.content,
                    }
                )
            elif m.tool_calls:
                # El turno en el que el modelo pidio herramientas. Se le
                # devuelve tal cual lo pidio, traducido al formato del
                # proveedor; sin esto, la segunda vuelta no entiende a que
                # contesta el resultado.
                messages.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(
                                        tc.get("arguments") or {}, ensure_ascii=False
                                    ),
                                },
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
            else:
                messages.append({"role": m.role, "content": m.content})
        return messages
