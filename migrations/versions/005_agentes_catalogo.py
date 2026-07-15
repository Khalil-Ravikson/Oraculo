"""agentes_catalogo

Sprint 2 (Fase 3) — catálogo administrável de agentes. O código
(`src/agents/bootstrap.py`) continua sendo o único lugar que decide QUAIS
classes de agente existem (ver decisão 0.1 do plano); esta tabela guarda só
o estado administrável (ativo, descricao editável, auditoria) que hoje vive
como uma flag crua no Redis (`admin:agent:{nome}:enabled`, sem histórico).

Migration puramente aditiva — nenhum código lê/escreve nesta tabela ainda
(isso entra na Fase 4).

Revision ID: 005_agentes_catalogo
Revises: e47065bc6cb9
Create Date: 2026-07-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005_agentes_catalogo"
down_revision: Union[str, Sequence[str], None] = "e47065bc6cb9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agentes_catalogo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=50), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("permissions", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agentes_catalogo_nome"), "agentes_catalogo", ["nome"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_agentes_catalogo_nome"), table_name="agentes_catalogo")
    op.drop_table("agentes_catalogo")
