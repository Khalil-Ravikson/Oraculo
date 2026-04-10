# src/infrastructure/database/models.py
from __future__ import annotations
from sqlalchemy import Boolean, Column, DateTime, Enum as SQLEnum, Integer, String, func
from sqlalchemy.orm import DeclarativeBase

# Importa os Enums do domínio para tipar as colunas
from src.domain.entities.enums import RoleEnum, CentroEnum, StatusMatriculaEnum

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

    role = Column(SQLEnum(RoleEnum), default=RoleEnum.publico, nullable=False)
    status = Column(SQLEnum(StatusMatriculaEnum), default=StatusMatriculaEnum.pendente, nullable=False)
    
    pode_abrir_chamado = Column(Boolean, default=True, nullable=False)
    verificado = Column(Boolean, default=False, nullable=False)

    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def display_name(self) -> str:
        return self.nome.split()[0] if self.nome else "usuário"