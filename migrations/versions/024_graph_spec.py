"""graph_spec: topologia do grafo de orquestração como dado (ADR 0008 Fase 5)

`graph_spec` guarda a `GraphSpec` ativa (JSONB), versionada; `graph_spec_historico`
é o log append-only (o botão "reverter" do Hub restaura um snapshot). Espelha o
padrão de `009_config_dinamica` / `010_route_registry`: `versao`, `tenant_id`
NULL, histórico, revert.

Sem seed: enquanto a tabela estiver vazia, `orchestration/loader.py` cai no
`specs/default.json` embutido (a topologia que roda hoje). A 1ª escrita pelo
Hub cria a linha.

Revision ID: 024_graph_spec
Revises: 023_orquestrador_unico_langgraph
Create Date: 2026-09-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "024_graph_spec"
down_revision = "023_orquestrador_unico_langgraph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_spec",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_graph_spec_tenant",
        "graph_spec",
        ["tenant_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "graph_spec_historico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_graph_spec_historico_versao",
        "graph_spec_historico",
        ["versao"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_spec_historico_versao", table_name="graph_spec_historico")
    op.drop_table("graph_spec_historico")
    op.drop_index("ux_graph_spec_tenant", table_name="graph_spec")
    op.drop_table("graph_spec")
