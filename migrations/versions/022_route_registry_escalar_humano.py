"""route_registry: rota ESCALAR_HUMANO (atendimento humano)

ADR 0008 Fase 2. Nova rota terminal: quando o usuário pede pra falar com uma
pessoa, o nó `human_handoff` silencia o bot pra a sessão (Redis
`handoff:session:{id}`, TTL 24h), registra em `handoff:queue` e avisa o
suporte. owner="langgraph" (nó nativo, sem flag), agente=NULL (utilitário),
não cacheável, não permite detour.

Espelha `route_registry._DEFAULTS["ESCALAR_HUMANO"]`; a paridade é travada por
`tests/unit/infrastructure/test_route_registry.py`.

Revision ID: 022_route_registry_escalar_humano
Revises: 021_graph_topology_gatilho
Create Date: 2026-09-02

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "022_route_registry_escalar_humano"
down_revision = "021_graph_topology_gatilho"
branch_labels = None
depends_on = None


_SEED: list[dict] = [
    {
        "rota": "ESCALAR_HUMANO", "entrypoint_node": "human_handoff",
        "owner": "langgraph", "agente": None, "cacheavel": False,
        "permite_detour": False, "doc_type": None, "k": 0, "planner_steps": None,
    },
]


def upgrade() -> None:
    route_registry = sa.table(
        "route_registry",
        sa.column("rota", sa.String),
        sa.column("entrypoint_node", sa.String),
        sa.column("owner", sa.String),
        sa.column("agente", sa.String),
        sa.column("cacheavel", sa.Boolean),
        sa.column("permite_detour", sa.Boolean),
        sa.column("doc_type", sa.String),
        sa.column("k", sa.Integer),
        sa.column("planner_steps", postgresql.ARRAY(sa.String)),
    )
    # Idempotente: em banco já semeado por uma versão futura do seed de 010,
    # não duplica.
    conn = op.get_bind()
    existe = conn.execute(
        sa.text("SELECT 1 FROM route_registry WHERE rota = 'ESCALAR_HUMANO' AND tenant_id IS NULL")
    ).first()
    if not existe:
        op.bulk_insert(route_registry, _SEED)


def downgrade() -> None:
    op.execute("DELETE FROM route_registry WHERE rota = 'ESCALAR_HUMANO' AND tenant_id IS NULL")
    op.execute("DELETE FROM route_registry_historico WHERE rota = 'ESCALAR_HUMANO'")
