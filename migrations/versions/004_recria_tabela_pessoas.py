"""recria_tabelas_base

Revision ID: 004_recria_tabela_pessoas
Revises: 003_intents_chunks
Create Date: 2026-05-29 14:37:39.316552

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e47065bc6cb9'
down_revision: Union[str, Sequence[str], None] = '003_intents_chunks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Cria APENAS a tabela pessoas que sumiu no reset
    op.create_table('pessoas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=200), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('telefone', sa.String(length=20), nullable=True),
        sa.Column('matricula', sa.String(length=20), nullable=True),
        sa.Column('centro', sa.Enum('CECEN', 'CESB', 'CESC', 'CCSA', 'CEEA', 'CCS', 'CCT', 'CESBA', 'OUTRO', name='centroenum'), nullable=True),
        sa.Column('curso', sa.String(length=200), nullable=True),
        sa.Column('semestre_ingresso', sa.String(length=10), nullable=True),
        sa.Column('turno', sa.Enum('MATUTINO', 'VESPERTINO', 'NOTURNO', name='turno_enum'), nullable=True),
        sa.Column('role', sa.Enum('publico', 'estudante', 'servidor', 'professor', 'coordenador', 'admin', name='roleenum'), nullable=False),
        sa.Column('status', sa.Enum('ativo', 'inativo', 'trancado', 'pendente', name='statusmatriculaenum'), nullable=False),
        sa.Column('pode_abrir_chamado', sa.Boolean(), nullable=False),
        sa.Column('verificado', sa.Boolean(), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('atualizado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pessoas_email'), 'pessoas', ['email'], unique=True)
    op.create_index(op.f('ix_pessoas_id'), 'pessoas', ['id'], unique=False)
    op.create_index(op.f('ix_pessoas_matricula'), 'pessoas', ['matricula'], unique=True)
    op.create_index(op.f('ix_pessoas_telefone'), 'pessoas', ['telefone'], unique=True)

    # Ajuste inofensivo do ARRAY que o Alembic detectou
    op.alter_column('intents_router', 'exemplos',
               existing_type=postgresql.ARRAY(sa.TEXT()),
               type_=postgresql.ARRAY(sa.String()),
               existing_nullable=True,
               existing_server_default=sa.text("'{}'::text[]"))


def downgrade() -> None:
    # Remove apenas a tabela pessoas e reverte o array
    op.alter_column('intents_router', 'exemplos',
               existing_type=postgresql.ARRAY(sa.String()),
               type_=postgresql.ARRAY(sa.TEXT()),
               existing_nullable=True,
               existing_server_default=sa.text("'{}'::text[]"))
               
    op.drop_index(op.f('ix_pessoas_telefone'), table_name='pessoas')
    op.drop_index(op.f('ix_pessoas_matricula'), table_name='pessoas')
    op.drop_index(op.f('ix_pessoas_id'), table_name='pessoas')
    op.drop_index(op.f('ix_pessoas_email'), table_name='pessoas')
    op.drop_table('pessoas')