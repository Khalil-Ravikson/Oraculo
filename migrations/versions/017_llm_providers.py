"""llm_providers — provedores de LLM cadastrados pelo painel (Hub v2, Sprint 3a)

Até aqui, adicionar um provedor de LLM exigia editar
`llm_provider_registry._REGISTRY` e fazer deploy. Esta tabela abre o caminho
de dado: o admin cadastra um provedor compatível com OpenAI pelo
`/hub/config` (aba Provedores) — base_url + modelo + o NOME da variável de
ambiente que guarda a chave.

Decisão do dono: a chave de API **não** fica no banco. `api_key_env` guarda
só o nome da variável (ex. "OPENAI_UEMA_KEY"); o valor continua no `.env`.
Alinhado ao §P de `plataforma_orientada_a_configuracao.md`.

`tipo`:
  - `gemini` / `deepseek` / `groq`: seeds dos provedores que já existem em
    código (`llm_provider_registry`). Ficam aqui só para o painel poder
    mostrar/ligar/desligar; a instância continua vindo do builder de código.
  - `openai_compat`: provedor novo, qualquer API `POST {base_url}/chat/
    completions` no formato OpenAI. Instanciado por `OpenAICompatibleProvider`.

Revision ID: 017_llm_providers
Revises: 016_tools_catalogo
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "017_llm_providers"
down_revision = "016_tools_catalogo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=40), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),  # gemini|deepseek|groq|openai_compat
        sa.Column("base_url", sa.String(length=300), server_default="", nullable=False),
        sa.Column("api_key_env", sa.String(length=100), server_default="", nullable=False),
        sa.Column("modelos", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("modelo_default", sa.String(length=100), server_default="", nullable=False),
        sa.Column("habilitado", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("origem", sa.String(length=10), server_default="painel", nullable=False),  # "codigo" | "painel"
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_llm_providers_tenant_nome",
        "llm_providers",
        ["tenant_id", "nome"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ux_llm_providers_tenant_nome", table_name="llm_providers")
    op.drop_table("llm_providers")
