"""Esquema inicial

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STAGES = ("filter", "embedding", "answer", "verification")


def upgrade() -> None:
    # La extension se habilita ahora aunque todavia no haya ninguna columna
    # vectorial: es una linea que evita una migracion despues.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    stage = postgresql.ENUM(*STAGES, name="usage_stage")
    stage.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("markup_pct", sa.Numeric(5, 2), nullable=False, server_default="15.00"),
        sa.Column("monthly_cap_usd", sa.Numeric(12, 2)),
        sa.Column("default_model", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_keys_organization_id", "api_keys", ["organization_id"])

    op.create_table(
        "conversations",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("id", sa.String(200), primary_key=True),
        sa.Column("external_user_id", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_messages_conversation",
        "messages",
        ["organization_id", "conversation_id", "created_at"],
    )

    op.create_table(
        "model_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("input_per_mtok", sa.Numeric(12, 6), nullable=False),
        sa.Column("cached_input_per_mtok", sa.Numeric(12, 6)),
        sa.Column("output_per_mtok", sa.Numeric(12, 6), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "model", "valid_from", name="uq_model_prices_vigencia"),
    )
    op.create_index("ix_model_prices_lookup", "model_prices", ["provider", "model", "valid_from"])

    op.create_table(
        "usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_user_id", sa.String(200), nullable=False),
        sa.Column("conversation_id", sa.String(200)),
        sa.Column(
            "stage",
            postgresql.ENUM(*STAGES, name="usage_stage", create_type=False),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("cached_input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("reasoning_tokens", sa.Integer),
        sa.Column(
            "price_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_prices.id", ondelete="RESTRICT"),
        ),
        sa.Column("cost_usd", sa.Numeric(12, 6)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_usage_org_fecha", "usage", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_table("usage")
    op.drop_table("model_prices")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("api_keys")
    op.drop_table("organizations")
    postgresql.ENUM(name="usage_stage").drop(op.get_bind(), checkfirst=True)
