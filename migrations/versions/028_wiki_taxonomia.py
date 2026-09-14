"""wiki_taxonomia: classificação sistema/módulo da wiki CTIC como dado

Hoje `hierarchy.py::KNOWN_SYSTEM_HUBS` é um dict Python hardcoded — mudar
uma classificação exige editar código e fazer deploy. Esta migration cria
o lugar onde essa classificação passa a viver, editável pelo painel
(`/hub/wiki/taxonomia`).

Nasce VAZIA, de propósito — decisão do dono: zerar a taxonomia atual e
reclassificar aos poucos pelo painel, em vez de migrar o dict existente.
Enquanto a tabela estiver vazia, `resolver_taxonomia()` cai no default
("Geral"/"Geral") para toda página, igual ao que já acontece hoje pra
qualquer page_id fora do dict. `KNOWN_SYSTEM_HUBS` continua no código como
referência histórica, mas para de ser lido pelo scraper a partir desta
mudança.

Revision ID: 028_wiki_taxonomia
Revises: 027_telemetria_llm
Create Date: 2026-09-14

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "028_wiki_taxonomia"
down_revision = "027_telemetria_llm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wiki_taxonomia",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.String(length=200), nullable=False),
        sa.Column("sistema", sa.String(length=100), nullable=False),
        sa.Column("modulo", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_wiki_taxonomia_tenant_page",
        "wiki_taxonomia",
        ["tenant_id", "page_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ux_wiki_taxonomia_tenant_page", table_name="wiki_taxonomia")
    op.drop_table("wiki_taxonomia")
