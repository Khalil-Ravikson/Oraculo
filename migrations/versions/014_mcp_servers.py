"""mcp_servers — registro admin de servidores MCP (Fase 8, Camada 2 de nós)

Plano A / Fase 8 (docs/historico/plataforma_orientada_a_configuracao.md
§G, docs/historico/fases_6_11_langgraph_studio.md). Primeira peça do "MCP
Connection Manager" — hoje só o registro (dado), sem conexão de fato
ainda (mcp_lab/clients.py continua com as 3 URLs hardcoded do gateway
pipeworx; nenhum código lê esta tabela pra conectar de verdade). Toda
`url` gravada aqui já passou por validação SSRF
(`src/infrastructure/security/ssrf_validator.py`) no momento do registro
— ver `src/graph/mcp_server_registry.py`.

Revision ID: 014_mcp_servers
Revises: 013_graph_node_config
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "014_mcp_servers"
down_revision = "013_graph_node_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(length=500), server_default="", nullable=False),
        sa.Column("habilitado", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_mcp_servers_tenant_name",
        "mcp_servers",
        ["tenant_id", "name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ux_mcp_servers_tenant_name", table_name="mcp_servers")
    op.drop_table("mcp_servers")
