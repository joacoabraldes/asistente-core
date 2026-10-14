"""Unica puerta al proveedor.

Emite tres cosas y en este orden: `TextChunk` mientras llega texto,
`ToolCallChunk` por cada herramienta que el modelo pida, y al final `TokenUsage`
con el consumo. El orden importa: el pipeline tiene que saber que hay un pedido
de herramienta **antes** de cerrar la respuesta.

Los tests reemplazan `stream_completion` en este modulo, no LiteLLM por dentro,
asi el chunk con los tokens es exacto y controlado. Por eso el resto del codigo
tiene que llamarla como `llm.stream_completion(...)` y no importarla por nombre.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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


@dataclass(slots=True)
class ToolCallChunk:
    """Un pedido de herramienta, ya completo.

    El proveedor lo manda en pedazos —primero el id y el nombre, despues los
    argumentos de a trozos de texto— asi que `stream_completion` los junta y
    emite esto una sola vez al final.

    `arguments` es el JSON parseado. Si el modelo mandara un JSON roto, queda
    vacio y el texto crudo queda en `arguments_raw`: **un agujero visible en vez
    de un diccionario inventado**, que es la misma regla que con los tokens.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    arguments_raw: str = ""


def _text_of(chunk: Any) -> str | None:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return None
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return None
    return getattr(delta, "content", None)


def _acumular_tool_calls(chunk: Any, parciales: dict[int, dict]) -> None:
    """Junta los pedazos de cada pedido de herramienta, por indice.

    El proveedor manda el id y el nombre una vez, y los argumentos repartidos en
    varios chunks. El indice es lo que los une.
    """
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return
    delta = getattr(choices[0], "delta", None)
    for tc in (getattr(delta, "tool_calls", None) or []) if delta else []:
        i = int(getattr(tc, "index", 0) or 0)
        acum = parciales.setdefault(i, {"id": "", "name": "", "arguments": ""})
        if getattr(tc, "id", None):
            acum["id"] = tc.id
        fn = getattr(tc, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                acum["name"] = fn.name
            if getattr(fn, "arguments", None):
                acum["arguments"] += fn.arguments


def _cerrar_tool_calls(parciales: dict[int, dict]) -> list[ToolCallChunk]:
    salida: list[ToolCallChunk] = []
    for i in sorted(parciales):
        acum = parciales[i]
        crudo = acum["arguments"]
        try:
            args = json.loads(crudo) if crudo else {}
            if not isinstance(args, dict):
                args, crudo = {}, crudo
        except json.JSONDecodeError:
            args = {}
        salida.append(
            ToolCallChunk(
                id=acum["id"] or f"call_{i}",
                name=acum["name"],
                arguments=args,
                arguments_raw=crudo,
            )
        )
    return salida


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


# =========================================================== CRITICO 1 de 5 ===
# QUE HACE     la unica puerta al proveedor, y la fuente de los tokens que se
#              cobran
# FORMULA      emite TextChunk mientras llega texto, ToolCallChunk por cada
#              herramienta pedida, y al final TokenUsage con el consumo. El uso
#              sale del ULTIMO chunk que lo informa, que es el que trae el total
# POR QUE ASI  los tokens los informa el proveedor y nunca se estiman con un
#              tokenizer local: lo que se factura tiene que ser lo que se pago
# LO VERIFICA  server/tests/test_costo_y_pipeline.py::test_cableado_real_con_el_mock_de_litellm
#              y server/tests/test_herramientas.py, las siete
# FALLA ASI    hasta hoy se tragaba los pedidos de herramienta: leia solo
#              delta.content, asi que un pedido no emitia nada, el pipeline
#              guardaba una respuesta vacia, registraba el consumo y mandaba un
#              `done` como si hubiera contestado. No rompia, mentia.
# ============================================================================
async def stream_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> AsyncIterator[TextChunk | ToolCallChunk | TokenUsage]:
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
    parciales: dict[int, dict] = {}
    async for chunk in response:
        text = _text_of(chunk)
        if text:
            yield TextChunk(text)
        _acumular_tool_calls(chunk, parciales)
        reported = _usage_of(chunk)
        if reported is not None:
            usage = reported
    # Los pedidos de herramienta van despues del texto y antes del uso: el
    # pipeline necesita saber que la respuesta no termino antes de cerrar.
    for pedido in _cerrar_tool_calls(parciales):
        yield pedido
    if usage is not None:
        yield usage
