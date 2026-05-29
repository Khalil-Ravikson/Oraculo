"""
src/infrastructure/services/ingestion_service.py
--------------------------------------------------
SERVICE PURO de ingestão — sem Celery.
Workers chamam este service. Testes testam este service diretamente.

RESPONSABILIDADES:
  1. Parse do arquivo (delega ao ParserFactory)
  2. Chunking (delega ao ChunkerFactory)
  3. Embedding em batch com rate limit (Gemini API nuvem)
  4. Salva chunks no Redis (salvar_chunk)
  5. Registra metadados no Postgres (DocumentChunkRepository)

CPU-ONLY:
  Parse (PyMuPDF, Docling) → CPU
  Embeddings → Gemini API (nuvem)
  Redis → CPU
  Postgres → CPU
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_BATCH_SIZE  = 50    # chunks por chamada à API de embeddings
_RATE_SLEEP  = 12    # segundos entre batches (Gemini free: 100 req/min)


@dataclass
class IngestionResult:
    source:         str
    titulo:         str       = ""
    chunks_saved:   int       = 0
    chars_extracted: int      = 0
    parser_used:    str       = ""
    chunker_used:   str       = ""
    elapsed_ms:     int       = 0
    success:        bool      = True
    error:          str       = ""
    chunks_info:    list[dict] = field(default_factory=list)  # para Postgres


class IngestionService:
    """
    Service puro de ingestão. Injetável e testável.
    """

    def __init__(
        self,
        embedding_model: Any = None,
        chunk_repo: Any = None,
    ):
        self._emb = embedding_model
        self._chunk_repo = chunk_repo

    async def ingerir(
        self,
        file_path: str,
        doc_type:  str = "geral",
        label:     str = "",
        titulo:    str = "",
        parser:    str = "auto",
        chunker:   str = "auto",
        chunk_size: int = 400,
        overlap:    int = 60,
    ) -> IngestionResult:
        """
        Pipeline completo de ingestão assíncrono.

        Args:
          file_path:  caminho absoluto do arquivo
          doc_type:   tipo semântico ("calendario", "edital", etc.)
          label:      prefixo anti-alucinação para o LLM
          titulo:     título legível para o Metadata Registry
          parser:     "auto" | "pymupdf" | "docling" | "llamaparse" | "csv" | "txt"
          chunker:    "auto" | "recursive" | "markdown" | "semantic"
        """
        t0 = time.monotonic()
        source = os.path.basename(file_path)

        if not os.path.exists(file_path):
            return IngestionResult(source=source, success=False, error=f"Arquivo não encontrado: {file_path}")

        # 1. Parse
        try:
            parser_obj = self._get_parser(file_path, parser)
            texto: str = await asyncio.to_thread(parser_obj.parse, file_path)
        except Exception as e:
            return IngestionResult(source=source, success=False, error=f"Parse falhou: {e}")

        if not texto.strip():
            return IngestionResult(source=source, success=False, error="Texto vazio após parsing.")

        # 2. Chunk
        try:
            chunker_obj = self._get_chunker(doc_type, file_path, chunker, chunk_size, overlap)
            chunks = await asyncio.to_thread(chunker_obj.chunk, texto, source=source, doc_type=doc_type)
        except Exception as e:
            return IngestionResult(source=source, success=False, error=f"Chunking falhou: {e}")

        if not chunks:
            return IngestionResult(source=source, success=False, error="0 chunks gerados.")

        # 3. Prepara textos e prefixo anti-alucinação
        label_final = label or source.upper().replace(".", " ").replace("-", " ")
        titulo_final = titulo or label_final.title()
        prefixo = f"[{label_final} | {doc_type}]\n"

        textos_raw   = [c.text for c in chunks]
        textos_final = [prefixo + c.text for c in chunks]

        # 4. Embeddings em batches (Gemini API — respeita rate limit)
        embeddings = await self._embeddings_com_rate_limit(textos_raw)
        if not embeddings:
            return IngestionResult(source=source, success=False, error="Embedding falhou.")

        # 5. Salva no Redis + coleta info para Postgres
        from src.infrastructure.redis_client import salvar_chunk
        chunks_info: list[dict] = []
        saved = 0

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chunk_id = hashlib.md5(f"{source}:{i}".encode()).hexdigest()[:16]
            try:
                await asyncio.to_thread(
                    salvar_chunk,
                    chunk_id=chunk_id,
                    content=textos_final[i],
                    source=source,
                    doc_type=doc_type,
                    embedding=emb,
                    chunk_index=i,
                    metadata={
                        "label": label_final,
                        "titulo": titulo_final,
                        **chunk.metadata,
                    },
                )
                chunks_info.append({
                    "chunk_id":    chunk_id,
                    "source":      source,
                    "titulo":      titulo_final,
                    "doc_type":    doc_type,
                    "chunk_index": i,
                    "chars":       len(chunk.text),
                    "parser_usado":  type(parser_obj).__name__,
                    "chunker_usado": type(chunker_obj).__name__,
                    "label":       label_final,
                })
                saved += 1
            except Exception as e:
                logger.warning("⚠️  Chunk %d/%d falhou: %s", i, len(chunks), e)

        # 6. Registra no Postgres (Metadata Registry)
        if chunks_info:
            await self._registrar_postgres(chunks_info)

        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info(
            "✅ [INGESTION] %s | %d/%d chunks | %dms",
            source, saved, len(chunks), elapsed,
        )
        return IngestionResult(
            source=source,
            titulo=titulo_final,
            chunks_saved=saved,
            chars_extracted=len(texto),
            parser_used=type(parser_obj).__name__,
            chunker_used=type(chunker_obj).__name__,
            elapsed_ms=elapsed,
            success=saved > 0,
            chunks_info=chunks_info,
        )

    async def _embeddings_com_rate_limit(
        self, textos: list[str]
    ) -> list[list[float]]:
        """Embedding em batches com pausa entre lotes (Gemini rate limit)."""
        emb = self._get_embeddings()
        resultado: list[list[float]] = []
        total = len(textos)

        for i in range(0, total, _BATCH_SIZE):
            lote = textos[i:i + _BATCH_SIZE]
            num_lote = (i // _BATCH_SIZE) + 1
            total_lotes = (total + _BATCH_SIZE - 1) // _BATCH_SIZE

            logger.info(
                "📐 Embedding lote %d/%d (%d chunks)...", num_lote, total_lotes, len(lote)
            )
            try:
                vets = await asyncio.to_thread(emb.embed_documents, lote)
                resultado.extend(vets)
            except Exception as e:
                logger.error("❌ Embedding lote %d falhou: %s", num_lote, e)
                return []

            if i + _BATCH_SIZE < total:
                await asyncio.sleep(_RATE_SLEEP)

        return resultado

    async def _registrar_postgres(self, chunks_info: list[dict]) -> None:
        """Registra metadados no Postgres de forma silenciosa."""
        try:
            if self._chunk_repo is None:
                from src.infrastructure.services.rag_search_service import DocumentChunkRepository
                self._chunk_repo = DocumentChunkRepository()
            await self._chunk_repo.registrar_batch(chunks_info)
            logger.info("🗄️  Metadata Registry: %d chunks registrados no Postgres.", len(chunks_info))
        except Exception as e:
            logger.warning("⚠️  Metadata Registry falhou (ignorado): %s", e)

    def _get_parser(self, file_path: str, parser: str) -> Any:
        from src.rag.ingestion.parser_factory import ParserFactory
        if parser == "auto":
            return ParserFactory.auto(file_path)
        return ParserFactory.get(parser)

    def _get_chunker(
        self,
        doc_type: str,
        file_path: str,
        chunker: str,
        size: int,
        overlap: int,
    ) -> Any:
        from src.rag.ingestion.chunker_factory import ChunkerFactory
        if chunker == "auto":
            return ChunkerFactory.for_doc_type(doc_type, chunk_size=size, overlap=overlap)
        return ChunkerFactory.get(chunker, chunk_size=size, overlap=overlap)

    def _get_embeddings(self) -> Any:
        if self._emb is None:
            from src.rag.embeddings import get_embeddings
            self._emb = get_embeddings()
        return self._emb