"""Las cuatro capas de control.

Hoy ninguna hace nada: lo que importa es que el pipeline ya las llame en este
orden, asi despues solo se reemplaza la implementacion por defecto.

    gasto -> pertinencia -> documentos -> LLM con herramientas

Cada capa que llama a un modelo gasta tokens, y ese gasto se registra en `usage`
con su propio `stage`: 'filter' para el de pertinencia, 'embedding' para la
busqueda en documentos, 'verification' para el control de la salida.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class RequestContext:
    organization_id: uuid.UUID
    conversation_id: str
    external_user_id: str
    question: str
    model: str
    request_id: str


@dataclass(slots=True)
class ScopeDecision:
    allowed: bool
    reason: str | None = None


@dataclass(slots=True)
class RetrievedChunk:
    text: str
    source: str
    score: float = 0.0


class SpendGuard(ABC):
    """Corta antes de gastar un token si la organizacion paso su tope."""

    @abstractmethod
    async def check(self, session: AsyncSession, ctx: RequestContext) -> str | None:
        """Devuelve el motivo del rechazo, o None si puede seguir."""


class ScopeFilter(ABC):
    """El filtro de pertinencia. Decide si la pregunta es del tema o no."""

    @abstractmethod
    async def evaluate(self, session: AsyncSession, ctx: RequestContext) -> ScopeDecision:
        ...


class DocumentRetriever(ABC):
    """Busca en los documentos de la organizacion."""

    @abstractmethod
    async def search(
        self, session: AsyncSession, ctx: RequestContext, k: int = 5
    ) -> list[RetrievedChunk]:
        ...


class ToolRegistry(ABC):
    """Herramientas que el modelo puede pedir.

    Van a haber de dos tipos: las que corre el core y las que corre el cliente.
    Para las del cliente, el core emite el evento `tool_call` y el SDK devuelve
    el resultado; por eso ese evento ya esta reservado en el contrato.
    """

    @abstractmethod
    def specs(self) -> list[dict[str, Any]]:
        ...
