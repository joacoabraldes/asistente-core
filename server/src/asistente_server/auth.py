"""API key a organizacion.

De la key se guarda solo el hash SHA-256. La key completa se muestra una sola
vez, cuando se crea.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import ApiKey, Organization

KEY_PREFIX = "ask_"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_api_key() -> tuple[str, str, str]:
    """Devuelve la key completa, el prefijo para mostrar y el hash para guardar."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:12], hash_key(raw)


async def require_organization(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Organization:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el header Authorization con la API key",
        )
    raw = authorization.split(" ", 1)[1].strip()
    stmt = (
        select(Organization)
        .join(ApiKey, ApiKey.organization_id == Organization.id)
        .where(ApiKey.key_hash == hash_key(raw), ApiKey.revoked_at.is_(None))
    )
    org = (await session.execute(stmt)).scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key invalida o revocada",
        )
    return org
