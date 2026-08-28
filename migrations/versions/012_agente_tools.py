"""agente_tools + drop agentes_catalogo.permissions

Plano A / Fase 5 (docs/historico/plataforma_orientada_a_configuracao.md §K/§L.2).

O vínculo agente↔capability sai da coluna JSON solta `agentes_catalogo.permissions`
(que estava SEMPRE vazia — todo agente tem `permissions = []` no código) para
uma tabela de junção dedicada `agente_tools`:

  agente        nome do agente (registry) — sem FK física (mesma decisão de agent_prompts)
  tool          nome da capability (capabilities/registry.py)
  habilitado    admin liga/desliga o binding via /hub/capabilities

O código continua sendo quem decide QUAIS capabilities um agente tem
(`agente.tools` na classe → `bootstrap` faz upsert aqui); o admin só liga/desliga.

`agentes_catalogo.permissions` é removida — vestígio do RBAC nunca conectado.

Revision ID: 012_agente_tools
Revises: 011_config_parser
Create Date: 2026-08-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "012_agente_tools"
down_revision = "011_config_parser"
branch_labels = None
depends_on = None

# Binding inicial: as 3 capabilities de cadastro no agente `tickets` (o que
# faz CRUD de pessoa). Espelha `TicketAgent.tools` no código.
_SEED = [
    ("tickets", "get_student_info"),
    ("tickets", "update_student_email"),
    ("tickets", "update_student_telefone"),
]


def upgrade() -> None:
    op.create_table(
        "agente_tools",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agente", sa.String(length=50), nullable=False),
        sa.Column("tool", sa.String(length=60), nullable=False),
        sa.Column("habilitado", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_agente_tools_tenant_agente_tool",
        "agente_tools",
        ["tenant_id", "agente", "tool"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    agente_tools = sa.table(
        "agente_tools",
        sa.column("agente", sa.String), sa.column("tool", sa.String),
    )
    op.bulk_insert(agente_tools, [{"agente": a, "tool": t} for a, t in _SEED])

    op.drop_column("agentes_catalogo", "permissions")


def downgrade() -> None:
    op.add_column(
        "agentes_catalogo",
        sa.Column("permissions", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
    )
    op.drop_index("ux_agente_tools_tenant_agente_tool", table_name="agente_tools")
    op.drop_table("agente_tools")
