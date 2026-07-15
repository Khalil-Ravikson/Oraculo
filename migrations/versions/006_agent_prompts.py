"""agent_prompts

Sprint 2 (Fase 7) — histórico versionado de prompts por agente. Substitui a
chave crua `admin:system_prompt` do Redis (um único prompt "global", sem
histórico) por um esquema por-agente com histórico completo: nunca faz
`UPDATE` do texto, sempre `INSERT` de uma nova versão + flag `active`.

Índice parcial único garante no máximo 1 versão ativa por agente a nível de
banco (não só de aplicação). Sem FK física para `agentes_catalogo.nome`
(deliberado — não acopla ordem de deploy/upsert entre as duas tabelas).

Migration puramente aditiva — nenhum código lê/escreve nesta tabela ainda
(cutover entra na Fase 8).

Revision ID: 006_agent_prompts
Revises: 005_agentes_catalogo
Create Date: 2026-07-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006_agent_prompts"
down_revision: Union[str, Sequence[str], None] = "005_agentes_catalogo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_prompts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=50), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_prompts_agent_name"), "agent_prompts", ["agent_name"])
    op.create_index(
        "ux_agent_prompts_active_por_agente",
        "agent_prompts",
        ["agent_name"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )


def downgrade() -> None:
    op.drop_index("ux_agent_prompts_active_por_agente", table_name="agent_prompts")
    op.drop_index(op.f("ix_agent_prompts_agent_name"), table_name="agent_prompts")
    op.drop_table("agent_prompts")
