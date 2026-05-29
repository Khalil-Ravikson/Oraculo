"""ltree_institutional

Revision ID: 0003_ltree_institutional
Revises: 0002_observability_tables
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy_utils import LtreeType

# METADADOS DA LINHA DO TEMPO
revision = "002_ltree_institutional"
down_revision = "001_observability_tables"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Ativa a extensão ltree no Postgres (obrigatório para a árvore da UEMA)
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree;")

    # 2. Cria a tabela unidades_institucionais
    op.create_table(
        "unidades_institucionais",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("path", LtreeType, nullable=False),
        sa.Column("sigla", sa.String(20), nullable=False),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("email", sa.String(100), nullable=True),
        sa.Column("telefone", sa.String(20), nullable=True),
        sa.Column("campus", sa.String(50), nullable=True, server_default="São Luís"),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=True),
    )
    # Índices do ltree
    op.create_index("ix_unidades_institucionais_path", "unidades_institucionais", ["path"], unique=True)
    op.execute("CREATE INDEX IF NOT EXISTS idx_unidades_path_gist ON unidades_institucionais USING GIST (path);")

    # 3. Cria a tabela documentos_unidades
    op.create_table(
        "documentos_unidades",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("unidade_id", sa.BigInteger, sa.ForeignKey("unidades_institucionais.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", sa.String(50), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("doc_type", sa.String(30), nullable=True),
        sa.Column("titulo", sa.String(200), nullable=True),
        sa.Column("indexado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_documentos_unidades_chunk_id", "documentos_unidades", ["chunk_id"])

    # 4. Injeta os dados da UEMA
    op.execute("""
        INSERT INTO unidades_institucionais (path, sigla, nome, tipo, campus) VALUES
          ('UEMA',                        'UEMA',    'Universidade Estadual do Maranhão',     'universidade',  'São Luís'),
          ('UEMA.REITORIA',               'REI',     'Reitoria',                              'reitoria',      'São Luís'),
          ('UEMA.REITORIA.PROG',          'PROG',    'Pró-Reitoria de Graduação',             'proretoria',    'São Luís'),
          ('UEMA.REITORIA.PROEXAE',       'PROEXAE', 'Pró-Reitoria de Extensão',              'proretoria',    'São Luís'),
          ('UEMA.CTIC',                   'CTIC',    'Centro de Tecnologia da Informação',    'departamento',  'São Luís'),
          ('UEMA.CECEN',                  'CECEN',   'Centro de Ciências Exatas e Naturais',  'centro',        'São Luís')
        ON CONFLICT (path) DO NOTHING;
    """)

def downgrade() -> None:
    op.drop_table("documentos_unidades")
    op.drop_table("unidades_institucionais")
    op.execute("DROP EXTENSION IF EXISTS ltree;")