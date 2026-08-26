"""
migrations/versions/007_multi_provider.py
============================================
Suporte a multi-provider LLM (Gemini/DeepSeek/Groq):

  1. `metricas_llm.provider` — qual provedor respondeu aquela chamada
     (complementa `modelo`, que já existia mas não distinguia provedor).
  2. `agentes_catalogo.llm_provider`/`llm_model` — override opcional por
     agente (NULL = herda o provider/model global de `settings`/Redis
     `admin:llm_provider`, ver `infrastructure/adapters/llm_factory.py`).
"""
from alembic import op
import sqlalchemy as sa

revision: str = "007_multi_provider"
down_revision = "006_agent_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "metricas_llm",
        sa.Column("provider", sa.String(20), nullable=True),
    )
    op.add_column(
        "agentes_catalogo",
        sa.Column("llm_provider", sa.String(20), nullable=True),
    )
    op.add_column(
        "agentes_catalogo",
        sa.Column("llm_model", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agentes_catalogo", "llm_model")
    op.drop_column("agentes_catalogo", "llm_provider")
    op.drop_column("metricas_llm", "provider")
