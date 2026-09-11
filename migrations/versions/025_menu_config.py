"""menu_config: o menu do bot como dado (item C2.5 do plano de recuperação)

`menu_config` guarda o menu ATIVO (JSONB), versionado; `menu_config_historico`
é o log append-only que o botão "reverter" do Hub restaura. Espelha
`024_graph_spec` linha a linha — mesmo optimistic lock, mesmo histórico,
mesma linha única com `tenant_id` NULL.

Sem seed, de propósito: enquanto a tabela estiver vazia,
`application/menu/loader.py` cai no `menus/default.json` embutido (o menu do
v1, que é o que roda hoje). A primeira edição pelo painel cria a linha. Isso
evita o problema clássico de seed de conteúdo — um `INSERT` aqui viraria a
versão 1 "oficial" e passaria a competir com o arquivo em toda atualização
de código.

Revision ID: 025_menu_config
Revises: 024_graph_spec
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "025_menu_config"
down_revision = "024_graph_spec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "menu_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_menu_config_tenant",
        "menu_config",
        ["tenant_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "menu_config_historico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_menu_config_historico_versao",
        "menu_config_historico",
        ["versao"],
    )


def downgrade() -> None:
    op.drop_index("ix_menu_config_historico_versao", table_name="menu_config_historico")
    op.drop_table("menu_config_historico")
    op.drop_index("ux_menu_config_tenant", table_name="menu_config")
    op.drop_table("menu_config")
