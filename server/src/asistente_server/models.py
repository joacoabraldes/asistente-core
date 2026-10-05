"""Modelo de datos.

Reglas que valen para todo el archivo:
- La plata va siempre en NUMERIC y se maneja con Decimal. Nunca float.
- Los tokens los informa el proveedor; las columnas son nullable porque un
  proveedor que no los informa tiene que dejar un agujero visible, no un cero
  que despues nadie revisa.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY as PgArray
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# El valor "embedding" no se usa todavia: entra ahora porque agregarle un valor
# a un enum de Postgres mas adelante es una migracion aparte.
USAGE_STAGES = ("filter", "embedding", "answer", "verification")

usage_stage_enum = PgEnum(*USAGE_STAGES, name="usage_stage", create_type=False)


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Porcentaje, no fraccion: 15.00 significa 15 %.
    markup_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("15.00")
    )
    monthly_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    default_model: Mapped[str] = mapped_column(String(200), nullable=False)
    # Temas sobre los que la organizacion acepta preguntas. Lista vacia =
    # no se filtra nada, y el filtro no llega a llamar al modelo.
    allowed_topics: Mapped[list[str]] = mapped_column(
        PgArray(String(200)), nullable=False, server_default="{}", default=list
    )
    # Modelo con el que corre el filtro. En NULL usa default_model.
    filter_model: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Solo el hash. La key completa se muestra una sola vez, al crearla.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    """El id lo elige el cliente, asi que la clave es (organizacion, id).

    Dos organizaciones pueden llamar "c1" a su conversacion sin pisarse, y el
    cruce entre organizaciones queda impedido por construccion.
    """

    __tablename__ = "conversations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    external_user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Message(Base):
    """Lleva organization_id porque la clave de conversations es compuesta."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelPrice(Base):
    """Precios versionados: no se pisan filas, se agrega otra con otro valid_from."""

    __tablename__ = "model_prices"
    __table_args__ = (UniqueConstraint("provider", "model", "valid_from"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    input_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    cached_input_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Usage(Base):
    """Una fila por llamada al proveedor.

    cost_usd es el costo del proveedor, sin markup. El markup se aplica al
    facturar, leyendo markup_pct de la organizacion, para que cambiarlo no deje
    el historial inconsistente.
    """

    __tablename__ = "usage"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(usage_stage_enum, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    price_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("model_prices.id", ondelete="RESTRICT")
    )
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
