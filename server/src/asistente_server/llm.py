"""Unica puerta al proveedor.

Los tests reemplazan `stream_completion` en este modulo, no LiteLLM por dentro,
asi el chunk con los tokens es exacto y controlado. Por eso el resto del codigo
tiene que llamarla como `llm.stream_completion(...)` y no importarla por nombre.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TokenUsage:
    """Lo que informo el proveedor. Nunca se calcula con un tokenizer local."""

    input_tokens: int | None = None
    cached_input_tokens: int = 0
    output_tokens: int | None = None
    reasoning_tokens: int = 0


@dataclass(slots=True)
class TextChunk:
    text: str


def _text_of(chunk: Any) -> str | None:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return None
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return None
    return getattr(delta, "content", None)


def _usage_of(chunk: Any) -> TokenUsage | None:
    raw = getattr(chunk, "usage", None)
    if raw is None:
        return None
    prompt_details = getattr(raw, "prompt_tokens_details", None)
    completion_details = getattr(raw, "completion_tokens_details", None)
    return TokenUsage(
        input_tokens=getattr(raw, "prompt_tokens", None),
        cached_input_tokens=int(getattr(prompt_details, "cached_tokens", 0) or 0)
        if prompt_details
        else 0,
        output_tokens=getattr(raw, "completion_tokens", None),
        reasoning_tokens=int(getattr(completion_details, "reasoning_tokens", 0) or 0)
        if completion_details
        else 0,
    )


async def stream_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> AsyncIterator[TextChunk | TokenUsage]:
    """Va emitiendo texto y, al final, el uso de tokens del proveedor.

    Se queda con el ULTIMO chunk que informa uso, que es el que trae el total.
    """
    import litellm

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        kwargs["tools"] = tools
    kwargs.update(extra)

    # Solo para desarrollo: con LLM_MOCK_RESPONSE seteada, LiteLLM responde ese
    # texto sin salir a la red ni pedir clave. En produccion queda sin definir.
    simulada = os.environ.get("LLM_MOCK_RESPONSE")
    if simulada and "mock_response" not in kwargs:
        kwargs["mock_response"] = simulada

    response = await litellm.acompletion(**kwargs)
    usage: TokenUsage | None = None
    async for chunk in response:
        text = _text_of(chunk)
        if text:
            yield TextChunk(text)
        reported = _usage_of(chunk)
        if reported is not None:
            usage = reported
    if usage is not None:
        yield usage
