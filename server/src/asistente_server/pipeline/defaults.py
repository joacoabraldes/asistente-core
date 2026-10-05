"""Implementaciones por defecto: no hacen nada y dejan pasar."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .interfaces import (
    DocumentRetriever,
    RequestContext,
    RetrievedChunk,
    ScopeDecision,
    ScopeFilter,
    SpendGuard,
    ToolRegistry,
)


class AllowAllSpendGuard(SpendGuard):
    async def check(self, session: AsyncSession, ctx: RequestContext) -> str | None:
        return None


class AllowAllScopeFilter(ScopeFilter):
    async def evaluate(self, session: AsyncSession, ctx: RequestContext) -> ScopeDecision:
        return ScopeDecision(allowed=True)


class NoDocuments(DocumentRetriever):
    async def search(
        self, session: AsyncSession, ctx: RequestContext, k: int = 5
    ) -> list[RetrievedChunk]:
        return []


class NoTools(ToolRegistry):
    def specs(self) -> list[dict[str, Any]]:
        return []
