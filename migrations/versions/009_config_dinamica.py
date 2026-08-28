"""config_dinamica

Plano A / Fase 1 (docs/historico/plataforma_orientada_a_configuracao.md, Anexo I
+ §M/§N) — Dynamic Configuration. Generaliza o padrão já comprovado em
`llm_pricing` (migration 008) e `agentes_catalogo` (005): Postgres é a fonte de
verdade, o Redis é o espelho de leitura rápida (write-through, sem TTL), e toda
chave nova cai pro default hardcoded de `settings.py` em qualquer falha.

Duas tabelas:

  config_dinamica            — valor ATUAL de cada chave. Mutate-in-place com
                               controle de concorrência otimista: coluna `versao`
                               (inteiro, incrementa a cada escrita); o UPDATE do
                               repositório inclui `WHERE versao = :versao_lida` e,
                               se afetar 0 linhas, a API devolve HTTP 409 em vez
                               de aplicar um last-write-wins que apagaria a
                               mudança de outro admin (§N item 1).

  config_dinamica_historico  — append-only (nunca UPDATE/DELETE). Guarda
                               `valor_antigo`/`valor_novo` de cada escrita para o
                               botão "reverter" do Hub (§N item 3).

Coluna `tenant_id UUID NULL` nas duas tabelas (§M): hoje é sempre NULL
("config global da UEMA") e NUNCA é lida com filtro no código das Fases 1-5 —
existe só para que a Fase 9 (multi-tenancy real, condicional) seja um
`ALTER`/backfill e não uma recriação de tabela. O índice único é
`(tenant_id, chave)` com `NULLS NOT DISTINCT` (PG15+; ambos os ambientes são
PG16) para que duas linhas globais da mesma chave sejam rejeitadas mesmo com
`tenant_id` NULL.

Seed: as 7 chaves iniciais do Anexo I, com valor = default atual de `settings.py`
(a tabela nunca começa vazia — mesma decisão de 008). O histórico também recebe
a linha de baseline v1 de cada chave (`valor_antigo=NULL`), para que "reverter
para o valor original" funcione sem tratar a v1 como caso especial.

Revision ID: 009_config_dinamica
Revises: 008_llm_pricing
Create Date: 2026-08-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "009_config_dinamica"
down_revision = "008_llm_pricing"
branch_labels = None
depends_on = None


# chave -> (tipo, valor_default). Espelha `dynamic_config.ALLOWED_DYNAMIC_KEYS`
# e os defaults de `settings.py` — mantido aqui só para o seed do migration.
_SEED = [
    ("DEV_TEST_NO_DB_WRITE",              "bool", "true"),
    ("DEV_TEST_SKIP_REGISTRATION",        "bool", "false"),
    ("FEATURE_LANGGRAPH_NATIVE_ROUTES",   "bool", "false"),
    ("FEATURE_LANGGRAPH_CELERY_DISPATCH", "bool", "false"),
    ("GEMINI_MODEL",                      "str",  "gemini-2.5-flash"),
    ("RAG_CACHE_TTL_SECONDS",             "int",  "3600"),
    ("RAG_RERANKER_ENABLED",              "bool", "true"),
]


def upgrade() -> None:
    op.create_table(
        "config_dinamica",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chave", sa.String(length=80), nullable=False),
        sa.Column("valor", sa.Text(), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("versao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_config_dinamica_tenant_chave",
        "config_dinamica",
        ["tenant_id", "chave"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "config_dinamica_historico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chave", sa.String(length=80), nullable=False),
        sa.Column("valor_antigo", sa.Text(), nullable=True),
        sa.Column("valor_novo", sa.Text(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_config_dinamica_historico_chave",
        "config_dinamica_historico",
        ["chave"],
    )

    config_dinamica = sa.table(
        "config_dinamica",
        sa.column("chave", sa.String),
        sa.column("valor", sa.Text),
        sa.column("tipo", sa.String),
    )
    op.bulk_insert(
        config_dinamica,
        [{"chave": c, "tipo": t, "valor": v} for (c, t, v) in _SEED],
    )

    historico = sa.table(
        "config_dinamica_historico",
        sa.column("chave", sa.String),
        sa.column("valor_antigo", sa.Text),
        sa.column("valor_novo", sa.Text),
        sa.column("versao", sa.Integer),
    )
    op.bulk_insert(
        historico,
        [{"chave": c, "valor_antigo": None, "valor_novo": v, "versao": 1} for (c, _t, v) in _SEED],
    )


def downgrade() -> None:
    op.drop_index("ix_config_dinamica_historico_chave", table_name="config_dinamica_historico")
    op.drop_table("config_dinamica_historico")
    op.drop_index("ux_config_dinamica_tenant_chave", table_name="config_dinamica")
    op.drop_table("config_dinamica")
