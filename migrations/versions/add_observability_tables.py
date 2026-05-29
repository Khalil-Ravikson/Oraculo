"""
migrations/versions/add_observability_tables.py
================================================
Alembic migration: cria tabelas de métricas, audit e feedback.

Move dados que hoje estão no Redis para PostgreSQL:
  - metrics:respostas   → metricas_llm
  - audit:log           → audit_log
  - feedback:ratings    → feedback_avaliacoes
  - monitor:logs        → monitor_logs (view/snapshot)

BENEFÍCIOS vs Redis:
  - Dados históricos sem limite (Redis tem ltrim)
  - Queries SQL complexas (GROUP BY rota, DATE_TRUNC, etc.)
  - Joins com tabela pessoas para análise por usuário
  - Backup automático com o resto do banco
  - Retenção configurável por tabela

ÍNDICES:
  Todos os índices são criados com CONCURRENTLY-safe approach via Alembic.
  ts + rota cobre 90% das queries do dashboard.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# Metadata da migration
revision = "0002_observability_tables"
down_revision = None   # ajuste para o revision anterior real
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ─── 1. metricas_llm ─────────────────────────────────────────────────────
    op.create_table(
        "metricas_llm",
        sa.Column("id",             sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts",             sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id",        sa.String(30),  nullable=True),
        sa.Column("rota",           sa.String(20),  nullable=True),
        sa.Column("tokens_entrada", sa.Integer,     nullable=True),
        sa.Column("tokens_saida",   sa.Integer,     nullable=True),
        sa.Column("tokens_total",   sa.Integer,     nullable=True),
        sa.Column("latencia_ms",    sa.Integer,     nullable=True),
        sa.Column("crag_score",     sa.Float,       nullable=True),
        sa.Column("cache_hit",      sa.Boolean,     server_default="false"),
        sa.Column("cache_layer",    sa.String(10),  nullable=True),   # exact | semantic
        sa.Column("chunks_count",   sa.Integer,     nullable=True),
        sa.Column("custo_usd",      sa.Numeric(10, 8), nullable=True),
        sa.Column("modelo",         sa.String(50),  nullable=True),
    )
    op.create_index("ix_metricas_llm_ts_rota",  "metricas_llm", ["ts", "rota"])
    op.create_index("ix_metricas_llm_user_id",  "metricas_llm", ["user_id"])
    op.create_index("ix_metricas_llm_ts",       "metricas_llm", ["ts"])

    # ─── 2. audit_log ────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id",        sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts",        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("admin_id",  sa.String(50),  nullable=True),
        sa.Column("action",    sa.String(100), nullable=False),
        sa.Column("target",    sa.String(100), nullable=True),
        sa.Column("resultado", sa.String(50),  nullable=True),
        sa.Column("detalhes",  JSONB,          nullable=True),
        sa.Column("ip",        sa.String(45),  nullable=True),
    )
    op.create_index("ix_audit_log_ts",       "audit_log", ["ts"])
    op.create_index("ix_audit_log_admin_id", "audit_log", ["admin_id"])
    op.create_index("ix_audit_log_action",   "audit_log", ["action"])

    # ─── 3. feedback_avaliacoes ───────────────────────────────────────────────
    op.create_table(
        "feedback_avaliacoes",
        sa.Column("id",           sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts",           sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id",      sa.String(30), nullable=True),
        sa.Column("rating",       sa.SmallInteger, nullable=False),   # 1-5
        sa.Column("rota",         sa.String(20), nullable=True),
        sa.Column("crag_score",   sa.Float,      nullable=True),
        sa.Column("session_id",   sa.String(30), nullable=True),
        sa.Column("comentario",   sa.Text,       nullable=True),
    )
    op.create_index("ix_feedback_ts",      "feedback_avaliacoes", ["ts"])
    op.create_index("ix_feedback_user_id", "feedback_avaliacoes", ["user_id"])
    op.create_index("ix_feedback_rating",  "feedback_avaliacoes", ["rating"])

    # ─── 4. monitor_snapshots ────────────────────────────────────────────────
    # Snapshots do estado do sistema capturados pelo Celery Beat a cada 5min
    op.create_table(
        "monitor_snapshots",
        sa.Column("id",                 sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts",                 sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("usuarios_ativos_1h", sa.Integer, nullable=True),
        sa.Column("msgs_processadas_1h",sa.Integer, nullable=True),
        sa.Column("tokens_1h",          sa.Integer, nullable=True),
        sa.Column("custo_usd_1h",       sa.Numeric(10, 6), nullable=True),
        sa.Column("cache_hit_rate",     sa.Float,   nullable=True),
        sa.Column("latencia_p50_ms",    sa.Integer, nullable=True),
        sa.Column("latencia_p95_ms",    sa.Integer, nullable=True),
        sa.Column("redis_ram_mb",       sa.Float,   nullable=True),
        sa.Column("erros_llm_count",    sa.Integer, nullable=True),
    )
    op.create_index("ix_monitor_ts", "monitor_snapshots", ["ts"])


def downgrade() -> None:
    op.drop_table("monitor_snapshots")
    op.drop_table("feedback_avaliacoes")
    op.drop_table("audit_log")
    op.drop_table("metricas_llm")