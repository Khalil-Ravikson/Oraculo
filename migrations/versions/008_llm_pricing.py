"""llm_pricing

Sprint 3 — tabela de preços editável sem rebuild. `pricing.py::_PRECOS`
(dicionário Python hardcoded) era a única fonte de preço/1M tokens — mudar
um preço (ex.: DeepSeek/Groq mudarem valores, ver notas.md §13.8 item 3)
exigia rebuild da imagem. Esta tabela vira a fonte de verdade editável via
`/hub/llm-custo`; `_PRECOS` passa a ser só o seed inicial/fallback.

Revision ID: 008_llm_pricing
Revises: 007_multi_provider
Create Date: 2026-08-17

"""
from alembic import op
import sqlalchemy as sa

revision: str = "008_llm_pricing"
down_revision = "007_multi_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_pricing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("modelo", sa.String(length=50), nullable=False),
        sa.Column("input_por_1m", sa.Numeric(10, 4), nullable=False),
        sa.Column("output_por_1m", sa.Numeric(10, 4), nullable=False),
        sa.Column("cache_por_1m", sa.Numeric(10, 4), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_por", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "modelo", name="uq_llm_pricing_provider_modelo"),
    )

    # Seed: mesmos valores hoje hardcoded em pricing.py::_PRECOS, pra tabela
    # nunca começar vazia (custo $0 até o primeiro edit manual seria pior
    # que os valores hardcoded atuais).
    llm_pricing = sa.table(
        "llm_pricing",
        sa.column("provider", sa.String),
        sa.column("modelo", sa.String),
        sa.column("input_por_1m", sa.Numeric),
        sa.column("output_por_1m", sa.Numeric),
        sa.column("cache_por_1m", sa.Numeric),
    )
    op.bulk_insert(llm_pricing, [
        {"provider": "gemini",   "modelo": "gemini-2.5-flash",      "input_por_1m": 0.30,  "output_por_1m": 2.50, "cache_por_1m": None},
        {"provider": "gemini",   "modelo": "gemini-2.5-flash-lite", "input_por_1m": 0.10,  "output_por_1m": 0.40, "cache_por_1m": None},
        {"provider": "gemini",   "modelo": "gemini-2.5-pro",        "input_por_1m": 1.25,  "output_por_1m": 10.00, "cache_por_1m": None},
        {"provider": "deepseek", "modelo": "deepseek-chat",         "input_por_1m": 0.20,  "output_por_1m": 1.20, "cache_por_1m": 0.02},
        {"provider": "groq",     "modelo": "llama-3.3-70b-versatile", "input_por_1m": 0.59, "output_por_1m": 0.79, "cache_por_1m": None},
        {"provider": "groq",     "modelo": "openai/gpt-oss-120b",   "input_por_1m": 0.15,  "output_por_1m": 0.60, "cache_por_1m": None},
        {"provider": "groq",     "modelo": "openai/gpt-oss-20b",    "input_por_1m": 0.075, "output_por_1m": 0.30, "cache_por_1m": None},
        {"provider": "gemini-embedding", "modelo": "gemini-embedding-001", "input_por_1m": 0.15, "output_por_1m": 0.0, "cache_por_1m": None},
    ])


def downgrade() -> None:
    op.drop_table("llm_pricing")
