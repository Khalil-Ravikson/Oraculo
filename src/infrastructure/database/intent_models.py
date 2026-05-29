"""
src/infrastructure/database/intent_models.py
---------------------------------------------
Models para intents_router e document_chunks.

Separado do models.py principal para evitar conflito com Alembic existente.
Importe onde precisar: from src.infrastructure.database.intent_models import IntentRouter, DocumentChunk
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class IntentRouter(Base):
    __tablename__ = "intents_router"

    id           = Column(Integer, primary_key=True)
    nome         = Column(String(50), nullable=False, unique=True)
    regex        = Column(String(400), nullable=True)
    exemplos     = Column(ARRAY(String), default=list)
    doc_type     = Column(String(50), nullable=True)
    k_vector     = Column(Integer, default=6)
    k_text       = Column(Integer, default=8)
    ativo        = Column(Boolean, default=True)
    criado_em    = Column(DateTime(timezone=True), server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<IntentRouter nome={self.nome} ativo={self.ativo}>"


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id            = Column(Integer, primary_key=True)
    chunk_id      = Column(String(16), nullable=False, unique=True, index=True)
    source        = Column(String(300), nullable=False, index=True)
    titulo        = Column(String(500), nullable=True)
    doc_type      = Column(String(50), nullable=True, index=True)
    chunk_index   = Column(Integer, nullable=False)
    chars         = Column(Integer, nullable=True)
    parser_usado  = Column(String(50), nullable=True)
    chunker_usado = Column(String(50), nullable=True)
    label         = Column(String(300), nullable=True)
    indexado_em   = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<DocumentChunk source={self.source} idx={self.chunk_index}>"