"""canais — instâncias de comunicação cadastradas pelo painel (Hub v2, Sprint 3b)

Até aqui, a instância do WhatsApp (Evolution API) vinha só de
`settings.EVOLUTION_*` (.env). Esta tabela permite cadastrar/ver instâncias
pelo `/hub/config` (aba Integradores): nome, URL base, instância, webhook, e
o status (via chamada à Evolution).

Escopo desta fase (decisão do dono): **só conectar instância existente** —
ver status (QR / conectado), reconectar QR, editar webhook. Criar uma
instância nova na Evolution pelo painel fica para depois.

`config` (JSONB): {"base_url", "api_key_env", "instance"}. A chave da
Evolution segue no `.env` — `api_key_env` guarda só o nome da variável
(mesma decisão de `llm_providers`).

O caminho de envio/recebimento de mensagem (EvolutionAdapter/EvolutionService)
continua lendo de `settings` nesta fase — a tabela seeda com os mesmos
valores, então o comportamento não muda. Migrar o hot path para config por
linha é follow-up, seguro só quando existir uma 2ª instância real.

Revision ID: 018_canais
Revises: 017_llm_providers
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "018_canais"
down_revision = "017_llm_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canais",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=80), nullable=False),
        sa.Column("tipo", sa.String(length=30), server_default="whatsapp_evolution", nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("webhook_url", sa.String(length=500), server_default="", nullable=False),
        sa.Column("habilitado", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("origem", sa.String(length=10), server_default="painel", nullable=False),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_canais_tenant_nome",
        "canais",
        ["tenant_id", "nome"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ux_canais_tenant_nome", table_name="canais")
    op.drop_table("canais")
