"""graph_topology — composição visual de grafos (Fase 6+, Camada 3 de nós)

Plano A / adendo de nós declarativos, Camada 3 (composição visual):
docs/historico/fases_6_11_langgraph_studio.md §D ("Hub — Redesign como
Graph Studio"). `topology_json` guarda nós posicionados no canvas + arestas
entre portas — validado (tipos batem, grafo é DAG) por
`src/graph/topology_validator.py` ANTES de qualquer escrita, nunca depois.

Ainda não há execução real de uma topologia salva (GraphExecutor da visão
do roadmap não existe) — esta migration é só persistência da composição
visual, mesmo estágio de "infra pronta, sem consumidor de produção" das
migrations 013/014.

Revision ID: 015_graph_topology
Revises: 014_mcp_servers
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "015_graph_topology"
down_revision = "014_mcp_servers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_topology",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), server_default="", nullable=False),
        sa.Column("topology_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_graph_topology_tenant_name",
        "graph_topology",
        ["tenant_id", "name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ux_graph_topology_tenant_name", table_name="graph_topology")
    op.drop_table("graph_topology")
