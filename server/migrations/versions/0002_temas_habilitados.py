"""Temas habilitados por organizacion

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Una organizacion sin temas no se filtra. La lista vacia es "dejar pasar
    # todo" a proposito: asi el filtro no cuesta un token hasta que alguien
    # configure los temas, y encenderlo es cargar una fila, no desplegar codigo.
    op.add_column(
        "organizations",
        sa.Column(
            "allowed_topics",
            postgresql.ARRAY(sa.String(200)),
            nullable=False,
            server_default="{}",
        ),
    )
    # El filtro conviene correrlo con un modelo mas barato que el de la
    # respuesta. En NULL usa el mismo que la respuesta.
    op.add_column("organizations", sa.Column("filter_model", sa.String(200)))


def downgrade() -> None:
    op.drop_column("organizations", "filter_model")
    op.drop_column("organizations", "allowed_topics")
