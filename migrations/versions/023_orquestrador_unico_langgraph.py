"""ADR 0008 Fase 3: orquestrador único (dispatcher legado + Planner aposentados)

O `dispatcher.py` legado e o Planner foram deletados — todo assunto roda no
grafo LangGraph. Consequências no schema/dados:

  * `route_registry.owner`: GREETING/SIGAA/MEDIA_DOWNLOAD/CHECK_STATUS eram
    "langgraph_conditional" (nativas só com a flag ligada) → agora "langgraph"
    como todas as outras. `owner` fica só como registro.
  * `route_registry.planner_steps`: coluna do DAG do Planner (deletado) —
    nenhum consumidor de runtime lê. Removida.
  * `config_dinamica`: a chave `FEATURE_LANGGRAPH_NATIVE_ROUTES` (gate da
    delegação pro legado) não existe mais no código.

Espelha `route_registry._DEFAULTS`; a paridade é travada por
`tests/unit/infrastructure/test_route_registry.py`.

Revision ID: 023_orquestrador_unico_langgraph
Revises: 022_route_registry_escalar_humano
Create Date: 2026-09-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "023_orquestrador_unico_langgraph"
down_revision = "022_route_registry_escalar_humano"
branch_labels = None
depends_on = None


# Rotas que eram "langgraph_conditional" na migration 010 (downgrade restaura).
_ROTAS_EX_CONDICIONAIS = ("GREETING", "SIGAA", "MEDIA_DOWNLOAD", "CHECK_STATUS")

# planner_steps do seed original da migration 010 (downgrade repopula).
_PLANNER_STEPS_010 = {
    "GERAL": ["rag_search"], "CALENDARIO": ["rag_search"], "EDITAL": ["rag_search"],
    "CONTATOS": ["rag_search"], "WIKI": ["rag_search"], "CRUD": ["crud_tool"],
    "TICKET_ABERTURA": ["ticket_abertura"], "GREETING": ["greeting"],
    "SIGAA": ["sigaa_biblioteca"], "MEDIA_DOWNLOAD": None, "CHECK_STATUS": [],
}


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text(
        "UPDATE route_registry SET owner = 'langgraph' "
        "WHERE owner <> 'langgraph' AND tenant_id IS NULL"
    ))

    op.drop_column("route_registry", "planner_steps")

    conn.execute(sa.text(
        "DELETE FROM config_dinamica "
        "WHERE chave = 'FEATURE_LANGGRAPH_NATIVE_ROUTES' AND tenant_id IS NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    op.add_column(
        "route_registry",
        sa.Column("planner_steps", postgresql.ARRAY(sa.String()), nullable=True),
    )
    for rota, steps in _PLANNER_STEPS_010.items():
        conn.execute(
            sa.text(
                "UPDATE route_registry SET planner_steps = :steps "
                "WHERE rota = :rota AND tenant_id IS NULL"
            ),
            {"steps": steps, "rota": rota},
        )

    for rota in _ROTAS_EX_CONDICIONAIS:
        conn.execute(
            sa.text(
                "UPDATE route_registry SET owner = 'langgraph_conditional' "
                "WHERE rota = :rota AND tenant_id IS NULL"
            ),
            {"rota": rota},
        )

    existe = conn.execute(sa.text(
        "SELECT 1 FROM config_dinamica "
        "WHERE chave = 'FEATURE_LANGGRAPH_NATIVE_ROUTES' AND tenant_id IS NULL"
    )).first()
    if not existe:
        conn.execute(sa.text(
            "INSERT INTO config_dinamica (chave, valor, tipo) "
            "VALUES ('FEATURE_LANGGRAPH_NATIVE_ROUTES', 'false', 'bool')"
        ))
