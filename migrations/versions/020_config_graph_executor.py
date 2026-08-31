"""config: FEATURE_GRAPH_EXECUTOR_PILOTO (dynamic)

Hub v2 Sprint 8. Adiciona 1 chave à `config_dinamica` (migration 009) —
reaproveita toda a infra da Fase 1:

  FEATURE_GRAPH_EXECUTOR_PILOTO  liga a EXECUÇÃO REAL de uma topologia de
                                 graph_studio pelo GraphExecutor. Default
                                 false — nada lê no caminho quente ainda; a
                                 flag existe para o dia em que um trecho
                                 piloto (busca/embeddings) for conectado.

Revision ID: 020_config_graph_executor
Revises: 019_mcp_servers_conexao
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa

revision: str = "020_config_graph_executor"
down_revision = "019_mcp_servers_conexao"
branch_labels = None
depends_on = None

_SEED = [
    ("FEATURE_GRAPH_EXECUTOR_PILOTO", "bool", "false"),
]


def upgrade() -> None:
    config_dinamica = sa.table(
        "config_dinamica",
        sa.column("chave", sa.String), sa.column("valor", sa.Text), sa.column("tipo", sa.String),
    )
    op.bulk_insert(config_dinamica, [{"chave": c, "tipo": t, "valor": v} for c, t, v in _SEED])

    historico = sa.table(
        "config_dinamica_historico",
        sa.column("chave", sa.String), sa.column("valor_antigo", sa.Text),
        sa.column("valor_novo", sa.Text), sa.column("versao", sa.Integer),
    )
    op.bulk_insert(
        historico,
        [{"chave": c, "valor_antigo": None, "valor_novo": v, "versao": 1} for c, _t, v in _SEED],
    )


def downgrade() -> None:
    chaves = tuple(c for c, _t, _v in _SEED)
    op.execute(sa.text("DELETE FROM config_dinamica WHERE chave IN :ch").bindparams(
        sa.bindparam("ch", value=chaves, expanding=True)))
    op.execute(sa.text("DELETE FROM config_dinamica_historico WHERE chave IN :ch").bindparams(
        sa.bindparam("ch", value=chaves, expanding=True)))
