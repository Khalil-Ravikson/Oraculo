"""tools_catalogo — ferramentas criadas pelo painel (Hub v2, Sprint 2)

Até aqui, adicionar uma ferramenta (capability) exigia escrever um arquivo
`capabilities/tools/tool_*.py` com o decorator `@tool` e fazer deploy. Esta
tabela abre o caminho de dado: o admin cadastra uma ferramenta pelo
`/hub/capabilities` e ela fica disponível para vincular a um agente
(`agente_tools`, migration 012) sem tocar código.

Dois tipos suportados nesta fase (decisão do dono):
  - `http`: chamada REST definida por dado (método, URL, headers, corpo).
    A URL passa por `ssrf_validator.validar_url_publica()` no cadastro.
  - `mcp`: ferramenta exposta por um servidor MCP já cadastrado em
    `mcp_servers` (migration 014). Referencia o servidor pelo nome.

Ferramenta com lógica Python arbitrária continua vindo de código (registro
explícito em `capabilities/registry.py`) — não é todo caso que vira dado.

`config` (JSONB) guarda o shape específico do tipo:
  http -> {"metodo","url","headers","corpo_template","auth":{...},"timeout_s"}
  mcp  -> {"servidor","tool_remota","args_template"}

Revision ID: 016_tools_catalogo
Revises: 015_graph_topology
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "016_tools_catalogo"
down_revision = "015_graph_topology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tools_catalogo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=60), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),  # "http" | "mcp"
        sa.Column("descricao", sa.String(length=500), server_default="", nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("permissoes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("confirmacao", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("habilitado", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_tools_catalogo_tenant_nome",
        "tools_catalogo",
        ["tenant_id", "nome"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ux_tools_catalogo_tenant_nome", table_name="tools_catalogo")
    op.drop_table("tools_catalogo")
