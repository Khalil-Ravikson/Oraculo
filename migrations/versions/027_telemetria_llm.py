"""metricas_llm: tokens de cache/reasoning, custo aberto e origem do preço

Estende a tabela existente em vez de criar uma nova. A `metricas_llm` já é
lida pelo `/hub/llm-custo`, pelo dashboard e pelo avaliador; uma tabela
paralela dividiria a verdade em duas e obrigaria a reescrever tudo isso.

Colunas novas, e o problema que cada uma resolve:

* `request_id` — chave idempotente. Sem ela não havia como evitar evento
  duplicado num retry de task.
* `trace_id` — liga a linha ao span do OpenTelemetry.
* `tokens_cache` / `tokens_reasoning` — os providers já cobram por eles e a
  tabela não tinha onde guardar.
* `custo_entrada` / `custo_saida` / `custo_cache` / `custo_reasoning` — o
  custo era um número só, sem como auditar a composição.
* `moeda` — o valor sempre foi USD por convenção, nunca declarado.
* `pricing_source` — de onde veio o preço: oficial, OpenRouter, LiteLLM,
  PricePerToken ou nenhum.
* `cost_status` — `exact`, `estimated` ou `unknown`. **É a correção mais
  importante desta migration:** `calcular_custo_usd` devolvia `0.0` quando
  não encontrava preço, e o painel mostrava "custou zero" e "não sei quanto
  custou" como a mesma coisa.
* `token_count_status` — `actual` quando o provider devolveu o uso,
  `estimated` quando foi contado por tokenizer, `unknown` quando nenhum dos
  dois.
* `preco_input_por_1m` / `preco_output_por_1m` / `pricing_version` — o preço
  APLICADO no momento do cálculo. Sem isso, recalcular o histórico depois de
  uma mudança de tabela de preços daria outro número, e a auditoria não
  fecharia.
* `status` / `erro` — chamada que falhou também é telemetria.

Todas anuláveis e com default compatível: nenhuma linha existente é tocada, e
código que ainda não preenche as colunas continua funcionando.

Revision ID: 027_telemetria_llm
Revises: 026_config_rate_limit
Create Date: 2026-09-11

"""
from alembic import op
import sqlalchemy as sa

revision: str = "027_telemetria_llm"
down_revision = "026_config_rate_limit"
branch_labels = None
depends_on = None

# `numeric` e não `float`: custo é dinheiro. 18,10 cobre frações de centavo
# de dólar por chamada sem perder precisão na soma de milhões de linhas.
_NUMERIC = sa.Numeric(18, 10)

_COLUNAS = [
    sa.Column("request_id", sa.String(64), nullable=True),
    sa.Column("trace_id", sa.String(64), nullable=True),
    sa.Column("tokens_cache", sa.Integer(), nullable=True),
    sa.Column("tokens_reasoning", sa.Integer(), nullable=True),
    sa.Column("custo_entrada", _NUMERIC, nullable=True),
    sa.Column("custo_saida", _NUMERIC, nullable=True),
    sa.Column("custo_cache", _NUMERIC, nullable=True),
    sa.Column("custo_reasoning", _NUMERIC, nullable=True),
    sa.Column("moeda", sa.String(3), server_default="USD", nullable=True),
    sa.Column("pricing_source", sa.String(20), nullable=True),
    sa.Column("cost_status", sa.String(10), nullable=True),
    sa.Column("token_count_status", sa.String(10), nullable=True),
    sa.Column("preco_input_por_1m", _NUMERIC, nullable=True),
    sa.Column("preco_output_por_1m", _NUMERIC, nullable=True),
    sa.Column("pricing_version", sa.String(40), nullable=True),
    sa.Column("status", sa.String(10), nullable=True),
    sa.Column("erro", sa.Text(), nullable=True),
]


def upgrade() -> None:
    for coluna in _COLUNAS:
        op.add_column("metricas_llm", coluna)

    # Único e parcial: `request_id` é opcional (linha antiga não tem), mas
    # quando existe não pode repetir — é o que torna o evento idempotente.
    op.create_index(
        "ux_metricas_llm_request_id",
        "metricas_llm",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )

    # O painel filtra por origem do preço e por status do custo para mostrar
    # "quanto do gasto é estimativa".
    op.create_index("ix_metricas_llm_cost_status", "metricas_llm", ["cost_status"])
    op.create_index("ix_metricas_llm_pricing_source", "metricas_llm", ["pricing_source"])


def downgrade() -> None:
    op.drop_index("ix_metricas_llm_pricing_source", table_name="metricas_llm")
    op.drop_index("ix_metricas_llm_cost_status", table_name="metricas_llm")
    op.drop_index("ux_metricas_llm_request_id", table_name="metricas_llm")
    for coluna in reversed(_COLUNAS):
        op.drop_column("metricas_llm", coluna.name)
