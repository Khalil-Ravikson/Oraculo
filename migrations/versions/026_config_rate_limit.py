"""config: RATE_LIMIT_MSGS e RATE_LIMIT_WINDOW_S (dynamic)

Item do v1 (2026-09-10). O limite de mensagens por pessoa era constante de
módulo — e pior, os campos que o `InputGuardrail` anunciava como configuráveis
eram lidos de lugar nenhum (ver TD-028). Agora o valor efetivo sai de
`config_dinamica`, editável em `/hub/config` sem reiniciar worker:

  RATE_LIMIT_MSGS      quantas mensagens uma pessoa pode mandar na janela.
  RATE_LIMIT_WINDOW_S  o tamanho da janela, em segundos.

Defaults iguais aos de `settings.py` — a paridade entre as três fontes
(seed, allowlist e default de código) é travada por
`tests/unit/infrastructure/test_dynamic_config.py`.

No v1 este limite vale só para mensagem que vira PERGUNTA: navegar o menu
custa zero token e não é contado.

Revision ID: 026_config_rate_limit
Revises: 025_menu_config
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa

revision: str = "026_config_rate_limit"
down_revision = "025_menu_config"
branch_labels = None
depends_on = None

_SEED = [
    ("RATE_LIMIT_MSGS", "int", "8"),
    ("RATE_LIMIT_WINDOW_S", "int", "60"),
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
