"""Pedidos de herramienta en los mensajes

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Cuando el modelo pide una herramienta, ese turno del asistente no tiene
    # texto: tiene el pedido. Hay que guardarlo porque la segunda vuelta —la que
    # trae el resultado— necesita reconstruir la conversacion tal cual la vio el
    # modelo, incluido su propio pedido.
    op.add_column("messages", sa.Column("tool_calls", postgresql.JSONB))
    # En los mensajes con role='tool': a que pedido contesta este resultado.
    op.add_column("messages", sa.Column("tool_call_id", sa.String(100)))


def downgrade() -> None:
    op.drop_column("messages", "tool_call_id")
    op.drop_column("messages", "tool_calls")
