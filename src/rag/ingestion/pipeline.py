"""
src/rag/ingestion/pipeline.py
------------------------------
Pipeline de ingestão: run() (síncrono, Celery) + run_async() (async, FastAPI).

REGRA: salvar_chunk() permanece SÍNCRONO por compatibilidade com Celery.
       Para chamadas async, usamos asyncio.to_thread() como wrapper.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    source:          str
    chunks_saved:    int  = 0
    chars_extracted: int  = 0
    parser_used:     str  = ""
    chunker_used:    str  = ""
    elapsed_ms:      int  = 0
    success:         bool = True
    error:           str  = ""


class IngestionPipeline:
    """
    Pipeline de ingestão com duas interfaces:
      run()       → síncrono  (Celery tasks, scripts de admin)
      run_async() → assíncrono (FastAPI endpoints, background tasks async)

    Ambas as interfaces compartilham a mesma lógica — evita duplicação.
    """

    def __init__(self, parser, chunker, embeddings, config: dict | None = None):
        self._parser     = parser
        self._chunker    = chunker
        self._embeddings = embeddings
        self._config     = config or {}

    # ─── Interface Síncrona (Celery) ──────────────────────────────────────────

    def run(self, file_path: str, doc_type: str = "geral", label: str = "") -> IngestionResult:
        """Versão síncrona — usada por tasks Celery. Não bloqueia event loop se chamada de sync."""
        source = os.path.basename(file_path)
        t0     = time.monotonic()

        result = self._execute_pipeline(file_path, doc_type, label)
        result.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return result

    # ─── Interface Assíncrona (FastAPI) ───────────────────────────────────────

    async def run_async(
        self,
        file_path: str,
        doc_type:  str = "geral",
        label:     str = "",
    ) -> IngestionResult:
        """
        Versão assíncrona do pipeline. Cada etapa CPU/IO-bound vai para thread pool.

        ESTRATÉGIA:
          - parse()          → asyncio.to_thread (IO-bound, pode demorar para PDFs grandes)
          - chunk()          → asyncio.to_thread (CPU-bound para PDFs grandes)
          - embed_documents()→ asyncio.to_thread (CPU-bound — modelo ML)
          - salvar_chunk()   → asyncio.to_thread (IO-bound Redis, mantido síncrono para Celery)

        Resultado: event loop nunca bloqueado, mesmo com PDFs de 50MB.
        """
        source = os.path.basename(file_path)
        t0     = time.monotonic()

        if not os.path.exists(file_path):
            return IngestionResult(
                source  = source,
                success = False,
                error   = f"Arquivo não encontrado: {file_path}",
            )

        # 1. Parse
        try:
            texto: str = await asyncio.to_thread(self._parser.parse, file_path)
        except Exception as exc:
            return IngestionResult(source=source, success=False, error=f"Parser: {exc}")

        if not texto.strip():
            return IngestionResult(source=source, success=False, error="Texto vazio após parsing.")

        if doc_type.lower() == "calendario":
            texto = self._enriquecer_texto_calendario(texto)

        # 2. Chunk
        try:
            chunks = await asyncio.to_thread(
                self._chunker.chunk, texto, source=source, doc_type=doc_type,
            )
        except Exception as exc:
            return IngestionResult(source=source, success=False, error=f"Chunker: {exc}")

        if not chunks:
            return IngestionResult(source=source, success=False, error="Nenhum chunk gerado.")

        label_final  = label or source.upper().replace(".", " ").replace("-", " ")
        prefixo      = f"[{label_final} | {doc_type}]\n"
        textos_puro  = [c.text for c in chunks]
        textos_final = [prefixo + c.text for c in chunks]

        # 3. Embeddings em batch (CPU-bound — modelo ML em thread pool)
        try:
            embeddings: list[list[float]] = await asyncio.to_thread(
                self._embeddings.embed_documents, textos_puro,
            )
        except Exception as exc:
            return IngestionResult(source=source, success=False, error=f"Embedding: {exc}")

        # 4. Salva no Redis (síncrono via to_thread — preserva compatibilidade Celery)
        from src.infrastructure.redis_client import salvar_chunk

        saved = 0
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chunk_id = hashlib.md5(f"{source}:{i}".encode()).hexdigest()[:16]
            try:
                await asyncio.to_thread(
                    salvar_chunk,
                    chunk_id    = chunk_id,
                    content     = textos_final[i],
                    source      = source,
                    doc_type    = doc_type,
                    embedding   = emb,
                    chunk_index = i,
                    metadata    = {**chunk.metadata, "label": label_final},
                )
                saved += 1
            except Exception as exc:
                logger.warning("⚠️  Chunk %d/%d falhou: %s", i, len(chunks), exc)

        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info(
            "✅ [%s] async | %d/%d chunks | %dms",
            source, saved, len(chunks), elapsed,
        )

        return IngestionResult(
            source          = source,
            chunks_saved    = saved,
            chars_extracted = len(texto),
            parser_used     = type(self._parser).__name__,
            chunker_used    = type(self._chunker).__name__,
            elapsed_ms      = elapsed,
            success         = saved > 0,
        )

    # ─── Lógica interna compartilhada ─────────────────────────────────────────

    def _execute_pipeline(self, file_path: str, doc_type: str, label: str) -> IngestionResult:
        """Lógica síncrona reutilizada por run() e potencialmente por run_async()."""
        source = os.path.basename(file_path)

        if not os.path.exists(file_path):
            return IngestionResult(source=source, success=False, error=f"Não encontrado: {file_path}")

        try:
            texto = self._parser.parse(file_path)
        except Exception as exc:
            return IngestionResult(source=source, success=False, error=f"Parser: {exc}")

        if not texto.strip():
            return IngestionResult(source=source, success=False, error="Texto vazio.")

        if doc_type.lower() == "calendario":
            texto = self._enriquecer_texto_calendario(texto)

        try:
            chunks = self._chunker.chunk(texto, source=source, doc_type=doc_type)
        except Exception as exc:
            return IngestionResult(source=source, success=False, error=f"Chunker: {exc}")

        if not chunks:
            return IngestionResult(source=source, success=False, error="0 chunks.")

        label_final  = label or source.upper().replace(".", " ").replace("-", " ")
        prefixo      = f"[{label_final} | {doc_type}]\n"
        textos_puro  = [c.text for c in chunks]
        textos_final = [prefixo + c.text for c in chunks]

        try:
            embeddings = self._embeddings.embed_documents(textos_puro)
        except Exception as exc:
            return IngestionResult(source=source, success=False, error=f"Embedding: {exc}")

        from src.infrastructure.redis_client import salvar_chunk

        saved = 0
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chunk_id = hashlib.md5(f"{source}:{i}".encode()).hexdigest()[:16]
            try:
                salvar_chunk(
                    chunk_id    = chunk_id,
                    content     = textos_final[i],
                    source      = source,
                    doc_type    = doc_type,
                    embedding   = emb,
                    chunk_index = i,
                    metadata    = {**chunk.metadata, "label": label_final},
                )
                saved += 1
            except Exception as exc:
                logger.warning("⚠️  Chunk %d: %s", i, exc)

        return IngestionResult(
            source          = source,
            chunks_saved    = saved,
            chars_extracted = len(texto),
            parser_used     = type(self._parser).__name__,
            chunker_used    = type(self._chunker).__name__,
            success         = saved > 0,
        )

    @staticmethod
    def _enriquecer_texto_calendario(texto: str) -> str:
        """
        Enriquece datas no formato DD/MM/YYYY adicionando a versão por extenso.
        Isso ajuda o retriever vetorial a entender consultas como "fevereiro" ou "março".
        """
        import re
        meses = {
            "01": "janeiro", "02": "fevereiro", "03": "março", "04": "abril",
            "05": "maio", "06": "junho", "07": "julho", "08": "agosto",
            "09": "setembro", "10": "outubro", "11": "novembro", "12": "dezembro"
        }
        
        def sub_data(match):
            dia, mes, ano = match.groups()
            nome_mes = meses.get(mes, "")
            if nome_mes:
                return f"{dia}/{mes}/{ano} ({dia} de {nome_mes} de {ano})"
            return match.group(0)

        # Regex para DD/MM/YYYY
        padrao = r"\b(\d{2})/(\d{2})/(\d{4})\b"
        return re.sub(padrao, sub_data, texto)

    @classmethod
    def build_auto(cls, file_path: str, doc_type: str = "geral") -> "IngestionPipeline":
        """Factory com seleção automática de parser e chunker."""
        from src.rag.ingestion.parser_factory  import ParserFactory
        from src.rag.ingestion.chunker_factory import ChunkerFactory
        from src.rag.embeddings                import get_embeddings

        return cls(
            parser     = ParserFactory.auto(file_path),
            chunker    = ChunkerFactory.for_doc_type(doc_type),
            embeddings = get_embeddings(),
        )


DOCUMENT_CONFIG: dict[str, dict] = {}
PDF_CONFIG = DOCUMENT_CONFIG


class Ingestor:
    """Compatibility shim for legacy Ingestor class."""
    def _ingerir_ficheiro(self, caminho: str) -> int:
        import os
        from src.rag.ingestion import DOCUMENT_CONFIG
        nome = os.path.basename(caminho)
        cfg = DOCUMENT_CONFIG.get(nome, {})
        
        params = {
            "size": cfg.get("chunk_size", 400),
            "overlap": cfg.get("overlap", 60),
            "strategy": cfg.get("strategy", "recursive"),
            "doc_type": cfg.get("doc_type", "geral"),
            "label": cfg.get("label", os.path.splitext(nome)[0].upper()),
        }
        
        from src.application.tasks.ingestion_tasks import processar_documento
        res = processar_documento(None, file_path=caminho, strategy_params=params)
        if res.get("ok"):
            return res.get("chunks", 0)
        return 0

    def diagnosticar(self) -> dict:
        from src.infrastructure.redis_client import diagnosticar
        return diagnosticar()