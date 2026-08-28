"""graph_node_config — habilitar/desabilitar nós do NodeRegistry (Camada 1)

Plano A / adendo de nós declarativos, continuação da Camada 1
(docs/historico/fases_6_11_langgraph_studio.md, docs/decision_camada1_nodes.md).

Mesmo padrão de `agente_tools` (migration 012): o código (`src/graph/nodes/`)
decide QUAIS nós existem (Registry Layer); esta tabela é a Configuration
Layer — admin liga/desliga cada nó via /hub/graph-nodes, sem editar código
nem redeploy. Um `node_id` sem linha aqui é implicitamente habilitado
(mesma filosofia de `agente_tools`: ausência de registro != desabilitado).

Distinto do `graph_node_bindings` do roadmap (Fase 6+, ainda não
implementado): aquela tabela vincula um nó a uma INSTÂNCIA de provider
dentro de uma topologia de grafo específica; esta aqui é só o toggle
global "este nó está disponível para uso" — mais simples, degrau anterior.

Revision ID: 013_graph_node_config
Revises: 012_agente_tools
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "013_graph_node_config"
down_revision = "012_agente_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_node_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=80), nullable=False),
        sa.Column("habilitado", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("config_overrides", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_graph_node_config_tenant_node",
        "graph_node_config",
        ["tenant_id", "node_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ux_graph_node_config_tenant_node", table_name="graph_node_config")
    op.drop_table("graph_node_config")
