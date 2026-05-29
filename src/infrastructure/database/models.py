# src/infrastructure/database/models.py
from __future__ import annotations
from sqlalchemy import Boolean, Column, DateTime, Enum as SQLEnum, BigInteger, ForeignKey, Text, Integer, String, func
from sqlalchemy.orm import DeclarativeBase
# Importa os Enums do domínio para tipar as colunas
from src.domain.entities.enums import RoleEnum, CentroEnum, StatusMatriculaEnum,TurnoEnum
from datetime import datetime, timezone
from sqlalchemy.orm import relationship
class Base(DeclarativeBase):
    pass

class Pessoa(Base):
    """
    Fonte da Verdade no PostgreSQL.
    Usada apenas pelo PessoaRepository para consultar/cadastrar e montar a IdentidadeRica.
    """
    __tablename__ = "pessoas" # Letra minúscula é padrão em Postgres, mas pode usar "Pessoas" se já estiver no banco.

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    email = Column(String(200), unique=True, index=True, nullable=False)
    telefone = Column(String(20), unique=True, index=True, nullable=True)
    
    matricula = Column(String(20), unique=True, index=True, nullable=True)
    centro = Column(SQLEnum(CentroEnum), nullable=True)
    curso = Column(String(200), nullable=True)
    semestre_ingresso = Column(String(10), nullable=True)
    turno = Column(SQLEnum(TurnoEnum, name="turno_enum", create_type=False), nullable=True)
    role = Column(SQLEnum(RoleEnum), default=RoleEnum.publico, nullable=False)
    status = Column(SQLEnum(StatusMatriculaEnum), default=StatusMatriculaEnum.pendente, nullable=False)
    
    pode_abrir_chamado = Column(Boolean, default=True, nullable=False)
    verificado = Column(Boolean, default=False, nullable=False)

    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    @property
    def display_name(self) -> str:
        return self.nome.split()[0] if self.nome else "usuário"



"""
Adição ao src/infrastructure/database/models.py
================================================
Modelos ltree para a árvore institucional da UEMA (Graph RAG preparatório).

COMO USAR:
  Cole estas classes no seu models.py existente.
  Execute: alembic revision --autogenerate -m "add_ltree_models"
  E depois: alembic upgrade head

REQUER:
  1. Extensão ltree no PostgreSQL:
     Em uma migration anterior ou manualmente:
     CREATE EXTENSION IF NOT EXISTS ltree;

  2. sqlalchemy-utils>=0.41.1 no requirements.txt

ESTRUTURA ltree:
  UEMA
  UEMA.REITORIA
  UEMA.REITORIA.PROG
  UEMA.REITORIA.PROEXAE
  UEMA.CECEN
  UEMA.CECEN.ENGENHARIA_CIVIL
  UEMA.CESB
  UEMA.CTIC

BENEFÍCIOS para o RAG:
  - Queries hierárquicas: "todos os contatos do CECEN e subsetores"
  - Filtros de contexto: injetar path do aluno na query de retrieval
  - Navegação do org chart via SQL puro (sem código Python)
"""


# sqlalchemy-utils fornece o tipo Ltree nativo
try:
    from sqlalchemy_utils import LtreeType
    _LTREE_AVAILABLE = True
except ImportError:
    _LTREE_AVAILABLE = False
    LtreeType = String  # fallback para String se não instalado


# ── Nó da árvore institucional ────────────────────────────────────────────────

class UnidadeInstitucional:
    """
    Representa um nó na hierarquia da UEMA.
    
    Exemplos de path:
      UEMA                          → Universidade raiz
      UEMA.REITORIA                 → Reitoria
      UEMA.REITORIA.PROG            → Pró-Reitoria de Graduação
      UEMA.CECEN                    → Centro de Ciências Exatas e Naturais
      UEMA.CECEN.ENGENHARIA_CIVIL   → Curso de Engenharia Civil
    """
    __tablename__ = "unidades_institucionais"

    id       = Column(BigInteger, primary_key=True, autoincrement=True)
    path     = Column(LtreeType, nullable=False, unique=True, index=True)
    sigla    = Column(String(20),  nullable=False, index=True)
    nome     = Column(String(200), nullable=False)
    tipo     = Column(String(30),  nullable=False)   # reitoria|proretoria|centro|departamento|curso
    email    = Column(String(100), nullable=True)
    telefone = Column(String(20),  nullable=True)
    campus   = Column(String(50),  nullable=True,  default="São Luís")
    ativo    = Column(Boolean,     nullable=False, default=True)
    criado_em = Column(DateTime(timezone=True),
                       server_default="now()", nullable=False)
    atualizado_em = Column(DateTime(timezone=True),
                           onupdate=datetime.now, nullable=True)

    # Relacionamento com documentos do RAG
    documentos = relationship("DocumentoUnidade", back_populates="unidade",
                              cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Unidade path={self.path} sigla={self.sigla}>"

    @property
    def nivel(self) -> int:
        """Profundidade na árvore (UEMA=1, REITORIA=2, PROG=3...)."""
        return len(str(self.path).split("."))

    @property
    def path_label(self) -> str:
        """Label legível do path para uso no RAG como metadata."""
        return str(self.path).replace(".", " > ")


class DocumentoUnidade:
    """
    Relaciona documentos do RAG (chunks no Redis) com unidades institucionais.
    Permite filtrar chunks por unidade ou subárvore institucional.
    """
    __tablename__ = "documentos_unidades"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    unidade_id    = Column(BigInteger, ForeignKey("unidades_institucionais.id",
                           ondelete="CASCADE"), nullable=False)
    chunk_id      = Column(String(50),  nullable=False, index=True)  # ID no Redis
    source        = Column(String(200), nullable=False)              # nome do arquivo
    doc_type      = Column(String(30),  nullable=True)
    titulo        = Column(String(200), nullable=True)
    indexado_em   = Column(DateTime(timezone=True),
                           server_default="now()", nullable=False)

    unidade = relationship("UnidadeInstitucional", back_populates="documentos")

    def __repr__(self) -> str:
        return f"<DocUnidade chunk={self.chunk_id} source={self.source}>"


# ── Migration SQL (para usar em alembic ou manual) ────────────────────────────

MIGRATION_SQL = """
-- Execute ANTES de rodar alembic upgrade:
CREATE EXTENSION IF NOT EXISTS ltree;

-- Seed de dados base da UEMA
INSERT INTO unidades_institucionais (path, sigla, nome, tipo, campus) VALUES
  ('UEMA',                        'UEMA',    'Universidade Estadual do Maranhão',     'universidade',  'São Luís'),
  ('UEMA.REITORIA',               'REI',     'Reitoria',                              'reitoria',      'São Luís'),
  ('UEMA.REITORIA.PROG',          'PROG',    'Pró-Reitoria de Graduação',             'proretoria',    'São Luís'),
  ('UEMA.REITORIA.PROEXAE',       'PROEXAE', 'Pró-Reitoria de Extensão',              'proretoria',    'São Luís'),
  ('UEMA.REITORIA.PRPPG',         'PRPPG',   'Pró-Reitoria de Pós-Graduação',        'proretoria',    'São Luís'),
  ('UEMA.REITORIA.PRAD',          'PRAD',    'Pró-Reitoria de Administração',         'proretoria',    'São Luís'),
  ('UEMA.CTIC',                   'CTIC',    'Centro de Tecnologia da Informação',    'departamento',  'São Luís'),
  ('UEMA.CECEN',                  'CECEN',   'Centro de Ciências Exatas e Naturais',  'centro',        'São Luís'),
  ('UEMA.CESB',                   'CESB',    'Centro de Estudos Superiores de Bacabal','centro',       'Bacabal'),
  ('UEMA.CESC',                   'CESC',    'Centro de Estudos Superiores de Caxias','centro',        'Caxias'),
  ('UEMA.CCSA',                   'CCSA',    'Centro de Ciências Sociais Aplicadas',  'centro',        'São Luís')
ON CONFLICT (path) DO NOTHING;

-- Índices para queries hierárquicas eficientes
CREATE INDEX IF NOT EXISTS idx_unidades_path_gist ON unidades_institucionais USING GIST (path);
CREATE INDEX IF NOT EXISTS idx_unidades_tipo ON unidades_institucionais (tipo);
CREATE INDEX IF NOT EXISTS idx_doc_unidade_source ON documentos_unidades (source);
"""


# ── Helper de query hierárquica ────────────────────────────────────────────────

def query_subarvore_sql(path_raiz: str) -> str:
    """
    Retorna SQL para buscar documentos de uma unidade e todos os seus filhos.
    
    Uso no RAG: quando aluno pergunta sobre "CECEN", busca também
    todos os cursos e departamentos abaixo do CECEN.
    
    Exemplo:
        sql = query_subarvore_sql("UEMA.CECEN")
        # Retorna todos os chunks de CECEN e subunidades
    """
    return f"""
        SELECT du.chunk_id, du.source, du.doc_type, u.sigla, u.nome, u.path::text
        FROM documentos_unidades du
        JOIN unidades_institucionais u ON u.id = du.unidade_id
        WHERE u.path <@ '{path_raiz}'
          AND u.ativo = true
        ORDER BY u.path
    """