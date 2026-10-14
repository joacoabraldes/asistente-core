"""Tipos de los eventos que emite el servidor."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class Usage(BaseModel):
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None


class ConsumoMensual(BaseModel):
    """Lo que devuelve `Asistente.consumo()`.

    Los montos llegan como string y pydantic los convierte a Decimal: un float
    les cambiaria el valor.
    """

    anio: int
    mes: int
    pedidos: int
    # Pedidos que el proveedor no informo. No estan sumados en los totales.
    pedidos_sin_costo: int
    costo_proveedor_usd: Decimal
    markup_pct: Decimal
    # El de la factura, en centavos.
    a_facturar_usd: Decimal
    # El mismo monto en 6 decimales: es contra el que se controla el tope, asi
    # que es el que hay que mostrar al lado del tope.
    a_facturar_exacto_usd: Decimal
    por_etapa: dict[str, Decimal] = {}
    tope_usd: Decimal | None = None
    # True cuando el mes esta cerrado: ese numero ya no cambia.
    cerrado: bool = False


class TokenEvent(BaseModel):
    text: str


class DoneEvent(BaseModel):
    request_id: str
    provider: str
    model: str
    usage: Usage | None = None
    # Llega como string justamente para no perder precision; pydantic lo
    # convierte a Decimal.
    cost_usd: Decimal | None = None


class ErrorEvent(BaseModel):
    code: str
    message: str


class OutOfScopeEvent(BaseModel):
    """Reservado: el servidor todavia no lo emite."""

    reason: str


class ToolCallEvent(BaseModel):
    """El modelo pide una herramienta y el stream termina aca.

    No llega `done` despues de esto: la respuesta todavia no existe. El cliente
    ejecuta la herramienta y llama a `Asistente.tool_results()` con el
    resultado, que abre un stream nuevo con la respuesta.

    Si `arguments` viene vacio y el modelo mando algo, es que el JSON de los
    argumentos estaba roto: queda vacio a proposito en vez de inventado.
    """

    id: str
    name: str
    arguments: dict[str, Any] = {}


class RawEvent(BaseModel):
    """Cualquier evento que el SDK todavia no conozca."""

    event: str
    data: dict[str, Any] = {}


Event = TokenEvent | DoneEvent | ErrorEvent | OutOfScopeEvent | ToolCallEvent | RawEvent
