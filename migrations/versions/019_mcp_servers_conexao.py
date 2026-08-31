"""mcp_servers: colunas de conexão (Hub v2, Sprint 4)

Adiciona a `mcp_servers` (migration 014) o que a página `/hub/mcp-servers`
precisa para deixar de ser "só cadastro":
  - `auth_tipo` / `auth_env`: autenticação da conexão (Nenhuma / Bearer /
    API Key). `auth_env` guarda o NOME da variável de ambiente com o segredo
    (mesma decisão de `llm_providers`/`canais`).
  - `latency_ms` / `last_checked`: preenchidos pelo botão "Testar Conexão".
  - `tools_expostas` (JSONB): lista de ferramentas que o servidor expõe,
    preenchida por "Sincronizar Ferramentas" (que também as insere em
    `tools_catalogo` como tipo `mcp`).

Revision ID: 019_mcp_servers_conexao
Revises: 018_canais
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "019_mcp_servers_conexao"
down_revision = "018_canais"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("auth_tipo", sa.String(length=20), server_default="none", nullable=False))
    op.add_column("mcp_servers", sa.Column("auth_env", sa.String(length=100), server_default="", nullable=False))
    op.add_column("mcp_servers", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("mcp_servers", sa.Column("last_checked", sa.DateTime(timezone=True), nullable=True))
    op.add_column("mcp_servers", sa.Column("tools_expostas", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    for col in ("tools_expostas", "last_checked", "latency_ms", "auth_env", "auth_tipo"):
        op.drop_column("mcp_servers", col)
