"""Cierre de mes

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Un mes cerrado no se vuelve a calcular. Sin esto, el resumen se rearma en
    # cada consulta leyendo `markup_pct` de la organizacion, asi que cambiar el
    # markup cambia tambien lo que ya se facturo en los meses pasados.
    op.create_table(
        "monthly_closings",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("anio", sa.Integer, primary_key=True),
        sa.Column("mes", sa.Integer, primary_key=True),
        # Copia de los valores al momento del cierre. El markup se guarda aca
        # porque es el que se uso para ese mes: el de la organizacion puede
        # cambiar manana.
        sa.Column("markup_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("costo_proveedor_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("a_facturar_usd", sa.Numeric(14, 2), nullable=False),
        sa.Column("pedidos", sa.Integer, nullable=False),
        sa.Column("pedidos_sin_costo", sa.Integer, nullable=False),
        # El desglose por etapa, tal cual quedo. JSONB y no una tabla aparte
        # porque no se consulta por etapa: se lee el cierre completo.
        sa.Column("por_etapa", postgresql.JSONB, nullable=False),
        sa.Column(
            "cerrado_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("mes BETWEEN 1 AND 12", name="ck_monthly_closings_mes"),
    )


def downgrade() -> None:
    op.drop_table("monthly_closings")
