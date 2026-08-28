"""route_registry

Plano A / Fase 2 (docs/historico/plataforma_orientada_a_configuracao.md §D/§K).
Colapsa numa tabela os dicts/frozensets hardcoded de rota→EXECUÇÃO que hoje
vivem espalhados e precisam ficar sincronizados à mão:

  * `contracts.ROTAS_SEM_CACHE`                       → coluna `cacheavel`
  * `dispatcher.py::_ROTA_PARA_AGENTE`                → coluna `agente` (circuit-breaker)
  * `dispatcher_langgraph.py::_ROTA_TO_ROUTE`/`_ROUTE_TO_ROTA` → coluna `entrypoint_node`
  * `dispatcher_langgraph.py::_ROTAS_LANGGRAPH` / `_ROTAS_DETOUR_RAG` /
    `_ROTAS_LANGGRAPH_NATIVAS_CONDICIONAIS`           → colunas `owner` + `permite_detour`
  * `supervisor.py::_dag_hint_para_rota::_HINTS`      → colunas `doc_type` / `k` / `planner_steps`

NÃO cobre a CLASSIFICAÇÃO (regex/embeddings/doc_type-k para o Redis
`router:config`) — isso continua em `intents_router` (migration 003), que
permanece intocada. `router:config` (quando presente) ainda tem precedência
sobre este registry no `_dag_hint_para_rota`.

Mesma mecânica de `config_dinamica` (migration 009): Postgres é a fonte de
verdade, `route_registry.py` espelha no Redis para o caminho quente síncrono,
`versao` dá optimistic lock (409 no Hub), `route_registry_historico` guarda um
snapshot da linha inteira por versão (revert = restaurar o snapshot).
`tenant_id UUID NULL` (§M), índice único `(tenant_id, rota)` NULLS NOT DISTINCT.

Seed: as 11 rotas de `contracts.ROTAS_VALIDAS` com os valores hardcoded de hoje
+ baseline v1 no histórico (a tabela nunca começa vazia — mesma decisão de 008/009).

`owner`:
  langgraph              — tratada nativamente pelo grafo (7 rotas)
  langgraph_conditional  — nativa só com FEATURE_LANGGRAPH_NATIVE_ROUTES ligada;
                           senão delega pra dispatcher.py legado (4 rotas)
  legacy                 — sempre delegada pra dispatcher.py (nenhuma rota hoje)

Revision ID: 010_route_registry
Revises: 009_config_dinamica
Create Date: 2026-08-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "010_route_registry"
down_revision = "009_config_dinamica"
branch_labels = None
depends_on = None


# rota -> valores hardcoded de hoje. Espelha `route_registry._DEFAULTS`.
_SEED: list[dict] = [
    {"rota": "GERAL",           "entrypoint_node": "rag",            "owner": "langgraph",             "agente": "academic_knowledge", "cacheavel": True,  "permite_detour": True,  "doc_type": "geral",     "k": 6, "planner_steps": ["rag_search"]},
    {"rota": "CALENDARIO",      "entrypoint_node": "rag",            "owner": "langgraph",             "agente": "academic_knowledge", "cacheavel": True,  "permite_detour": True,  "doc_type": "calendario","k": 8, "planner_steps": ["rag_search"]},
    {"rota": "EDITAL",          "entrypoint_node": "rag",            "owner": "langgraph",             "agente": "academic_knowledge", "cacheavel": True,  "permite_detour": True,  "doc_type": "edital",    "k": 10,"planner_steps": ["rag_search"]},
    {"rota": "CONTATOS",        "entrypoint_node": "rag",            "owner": "langgraph",             "agente": "academic_knowledge", "cacheavel": True,  "permite_detour": True,  "doc_type": "contatos",  "k": 6, "planner_steps": ["rag_search"]},
    {"rota": "WIKI",            "entrypoint_node": "rag",            "owner": "langgraph",             "agente": "academic_knowledge", "cacheavel": True,  "permite_detour": True,  "doc_type": "wiki_ctic", "k": 6, "planner_steps": ["rag_search"]},
    {"rota": "CRUD",            "entrypoint_node": "crud",           "owner": "langgraph",             "agente": "tickets",            "cacheavel": False, "permite_detour": False, "doc_type": None,        "k": 0, "planner_steps": ["crud_tool"]},
    {"rota": "TICKET_ABERTURA", "entrypoint_node": "ticket",         "owner": "langgraph",             "agente": "tickets",            "cacheavel": True,  "permite_detour": False, "doc_type": None,        "k": 0, "planner_steps": ["ticket_abertura"]},
    {"rota": "GREETING",        "entrypoint_node": "greeting",       "owner": "langgraph_conditional", "agente": None,                 "cacheavel": False, "permite_detour": False, "doc_type": None,        "k": 0, "planner_steps": ["greeting"]},
    {"rota": "SIGAA",           "entrypoint_node": "sigaa",          "owner": "langgraph_conditional", "agente": "sigaa",              "cacheavel": False, "permite_detour": False, "doc_type": None,        "k": 0, "planner_steps": ["sigaa_biblioteca"]},
    {"rota": "MEDIA_DOWNLOAD",  "entrypoint_node": "media_download", "owner": "langgraph_conditional", "agente": None,                 "cacheavel": False, "permite_detour": False, "doc_type": None,        "k": 0, "planner_steps": None},
    {"rota": "CHECK_STATUS",    "entrypoint_node": "check_status",   "owner": "langgraph_conditional", "agente": None,                 "cacheavel": False, "permite_detour": False, "doc_type": None,        "k": 0, "planner_steps": []},
]

def upgrade() -> None:
    op.create_table(
        "route_registry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rota", sa.String(length=40), nullable=False),
        sa.Column("entrypoint_node", sa.String(length=40), nullable=False),
        sa.Column("owner", sa.String(length=24), nullable=False),
        sa.Column("agente", sa.String(length=50), nullable=True),
        sa.Column("cacheavel", sa.Boolean(), nullable=False),
        sa.Column("permite_detour", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("doc_type", sa.String(length=30), nullable=True),
        sa.Column("k", sa.Integer(), nullable=True),
        sa.Column("planner_steps", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_route_registry_tenant_rota",
        "route_registry",
        ["tenant_id", "rota"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "route_registry_historico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rota", sa.String(length=40), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_route_registry_historico_rota",
        "route_registry_historico",
        ["rota"],
    )

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
    op.bulk_insert(route_registry, _SEED)

    historico = sa.table(
        "route_registry_historico",
        sa.column("rota", sa.String),
        sa.column("versao", sa.Integer),
        sa.column("snapshot", postgresql.JSONB),
    )
    op.bulk_insert(
        historico,
        [{"rota": row["rota"], "versao": 1, "snapshot": {**row, "versao": 1}} for row in _SEED],
    )


def downgrade() -> None:
    op.drop_index("ix_route_registry_historico_rota", table_name="route_registry_historico")
    op.drop_table("route_registry_historico")
    op.drop_index("ux_route_registry_tenant_rota", table_name="route_registry")
    op.drop_table("route_registry")
