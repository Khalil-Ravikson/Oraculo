"""config parser (dynamic)

Plano A / Fase 4 (docs/historico/plataforma_orientada_a_configuracao.md §J/§K):
"Parser: mover prioridade/enable pra config". Adiciona 2 chaves à
`config_dinamica` (migration 009) — reaproveita toda a infra da Fase 1:

  PARSER_PDF_PRIORIDADE   ordem dos parsers tentados p/ PDF com texto real
                          (lista separada por vírgula). Default = a ordem
                          hardcoded de hoje em ParserFactory.auto().
  PARSER_DESABILITADOS    parsers a pular (lista por vírgula). Generaliza
                          settings.DISABLE_DOCLING (que continua funcionando).

Revision ID: 011_config_parser
Revises: 010_route_registry
Create Date: 2026-08-27

"""
from alembic import op
import sqlalchemy as sa

revision: str = "011_config_parser"
down_revision = "010_route_registry"
branch_labels = None
depends_on = None

_SEED = [
    ("PARSER_PDF_PRIORIDADE", "str", "docling,pymupdf"),
    ("PARSER_DESABILITADOS",  "str", ""),
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
