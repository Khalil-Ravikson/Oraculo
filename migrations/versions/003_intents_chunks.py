"""add intents_router and document_chunks

Revision ID: 003_intents_chunks
Revises: 002_ltree_institutional
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# METADADOS DA LINHA DO TEMPO
revision = "003_intents_chunks"
down_revision = "002_ltree_institutional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intents_router",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("nome", sa.String(50), nullable=False, unique=True),
        sa.Column("regex", sa.String(400), nullable=True),
        sa.Column("exemplos", postgresql.ARRAY(sa.Text), server_default="{}"),
        sa.Column("doc_type", sa.String(50), nullable=True),   # hint para RAG
        sa.Column("k_vector", sa.Integer, server_default="6"),
        sa.Column("k_text", sa.Integer, server_default="8"),
        sa.Column("ativo", sa.Boolean, server_default="true"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    # Seed das intents iniciais
    op.execute("""
        INSERT INTO intents_router (nome, regex, exemplos, doc_type, k_vector, k_text) VALUES
        ('CALENDARIO',
         'matr[íi]cula|rematr|calend|semestre|prazo|início.aulas|feriado|trancamento|reingresso',
         ARRAY[
           'quando é a matrícula de veteranos',
           'prazo para trancamento de disciplinas',
           'calendário acadêmico 2026',
           'início das aulas primeiro semestre',
           'feriados do semestre'
         ],
         'calendario', 8, 10),
        ('EDITAL',
         'paes|vestibular|vaga|cota|inscri|edital|br.ppi|pcd|processo seletivo',
         ARRAY[
           'vagas por curso no PAES 2026',
           'como funciona a cota BR-PPI',
           'documentos para inscrição no vestibular',
           'cronograma do processo seletivo',
           'quantas vagas para engenharia civil'
         ],
         'edital', 6, 10),
        ('CONTATOS',
         'email|telefone|contato|endereço|ramal|coordenador|ctic|prog|reitoria|secretaria',
         ARRAY[
           'email do PROG graduação',
           'telefone da secretaria de engenharia',
           'contato do CTIC suporte TI',
           'email da reitoria UEMA',
           'como falar com a coordenação'
         ],
         'contatos', 7, 5),
        ('WIKI',
         'sigaa|senha|wifi|sistema|suporte|laborat|vpn|ti\b|glpi|chamado técnico',
         ARRAY[
           'como resetar senha do SIGAA',
           'configurar email uema no celular',
           'conectar wifi do campus',
           'abrir chamado de suporte TI',
           'acesso VPN UEMA'
         ],
         'wiki_ctic', 5, 6),
        ('CRUD',
         'alterar|atualizar|mudar|trocar|corrigir|meu email|meu telefone|minha senha',
         ARRAY[
           'quero mudar meu email cadastrado',
           'atualizar meu número de telefone',
           'como alterar meus dados',
           'corrigir minha matrícula'
         ],
         'geral', 0, 0),
        ('GREETING',
         '^(oi|olá|ola|bom dia|boa tarde|boa noite|hey|tudo bem|tudo certo)\s*[!.?]*$',
         ARRAY[
           'oi tudo bem',
           'olá bom dia',
           'boa tarde como vai',
           'oi oráculo'
         ],
         'geral', 0, 0),
        ('GERAL',
         NULL,
         ARRAY[
           'informações gerais sobre a uema',
           'dúvida sobre a universidade'
         ],
         'geral', 6, 6)
    """)

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("chunk_id", sa.String(16), nullable=False, unique=True, index=True),
        sa.Column("source", sa.String(300), nullable=False, index=True),
        sa.Column("titulo", sa.String(500), nullable=True),
        sa.Column("doc_type", sa.String(50), nullable=True, index=True),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("chars", sa.Integer, nullable=True),
        sa.Column("parser_usado", sa.String(50), nullable=True),
        sa.Column("chunker_usado", sa.String(50), nullable=True),
        sa.Column("label", sa.String(300), nullable=True),
        sa.Column("indexado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_document_chunks_source_index", "document_chunks", ["source", "chunk_index"])


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("intents_router")