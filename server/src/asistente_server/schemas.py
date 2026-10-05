"""Cuerpos de entrada y contenido de los eventos."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MessageIn(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class UsageOut(BaseModel):
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None


class DoneOut(BaseModel):
    request_id: str
    provider: str
    model: str
    usage: UsageOut | None = None
    # String y no float: el costo es un Decimal y no queremos perder precision
    # al serializarlo a JSON.
    cost_usd: str | None = None


class ConsumoMensualOut(BaseModel):
    """Los montos van como string por la misma razon que en DoneOut: son
    Decimal y un float les cambiaria el valor."""

    anio: int
    mes: int
    pedidos: int
    # Pedidos que el proveedor no informo: no suman al total y hay que mirarlos.
    pedidos_sin_costo: int
    costo_proveedor_usd: str
    markup_pct: str
    # El de la factura, en centavos.
    a_facturar_usd: str
    # El mismo monto en 6 decimales, que es contra el que se controla el tope.
    a_facturar_exacto_usd: str
    por_etapa: dict[str, str]
    tope_usd: str | None = None
