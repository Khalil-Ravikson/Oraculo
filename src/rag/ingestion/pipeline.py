"""
src/rag/ingestion/pipeline.py
------------------------------
Pipeline principal de ingestão. Orquestra:
  parser → chunker → embed → write_index

DESIGN:
  IngestionPipeline é configurável via constructor injection.
  Não tem lógica de negócio — só orquestra os componentes.
  Fácil de testar com mocks de cada componente.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    source: str
    chunks_saved: int = 0
    chars_extracted: int = 0
    parser_used: str = ""
    chunker_used: str = ""
    elapsed_ms: int = 0
    success: bool = True
    error: str = ""


class IngestionPipeline:
    """
    Pipeline completo de ingestão de documentos.

    Uso:
        from src.rag.ingestion import IngestionPipeline, ParserFactory, ChunkerFactory

        pipeline = IngestionPipeline(
            parser=ParserFactory.auto("/caminho/edital.pdf"),
            chunker=ChunkerFactory.for_doc_type("edital"),
            embeddings=get_embeddings(),
        )
        result = pipeline.run("/caminho/edital.pdf", doc_type="edital")
    """

    def __init__(self, parser, chunker, embeddings, config: dict | None = None):
        self._parser = parser
        self._chunker = chunker
        self._embeddings = embeddings
        self._config = config or {}

    def run(self, file_path: str, doc_type: str = "geral", label: str = "") -> IngestionResult:
        source = os.path.basename(file_path)
        t0 = time.monotonic()

        if not os.path.exists(file_path):
            return IngestionResult(source=source, success=False, error=f"Arquivo não encontrado: {file_path}")

        # 1. Parse
        logger.info("📄 [%s] Iniciando ingestão com %s", source, type(self._parser).__name__)
        try:
            texto = self._parser.parse(file_path)
        except Exception as e:
            return IngestionResult(source=source, success=False, error=f"Parser falhou: {e}")

        if not texto.strip():
            return IngestionResult(source=source, success=False, error="Texto vazio após parsing")

        # 2. Chunk
        try:
            chunks = self._chunker.chunk(texto, source=source, doc_type=doc_type)
        except Exception as e:
            return IngestionResult(source=source, success=False, error=f"Chunker falhou: {e}")

        if not chunks:
            return IngestionResult(source=source, success=False, error="Nenhum chunk gerado")

        # 3. Aplica prefixo hierárquico anti-alucinação
        label_final = label or source.upper().replace(".", " ").replace("-", " ")
        prefixo = f"[{label_final} | {doc_type}]\n"
        textos_puro = [c.text for c in chunks]
        textos_final = [prefixo + c.text for c in chunks]

        # 4. Embed
        try:
            embeddings = self._embeddings.embed_documents(textos_puro)
        except Exception as e:
            return IngestionResult(source=source, success=False, error=f"Embedding falhou: {e}")

        # 5. Salva no Redis
        from src.infrastructure.redis_client import salvar_chunk
        saved = 0
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chunk_id = hashlib.md5(f"{source}:{i}".encode()).hexdigest()[:16]
            try:
                meta = {**chunk.metadata, "label": label_final}
                salvar_chunk(
                    chunk_id=chunk_id,
                    content=textos_final[i],
                    source=source,
                    doc_type=doc_type,
                    embedding=emb,
                    chunk_index=i,
                    metadata=meta,
                )
                saved += 1
            except Exception as e:
                logger.warning("⚠️  Chunk %d/%d falhou ao salvar: %s", i, len(chunks), e)

        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info("✅ [%s] %d/%d chunks salvos em %dms", source, saved, len(chunks), elapsed)

        return IngestionResult(
            source=source,
            chunks_saved=saved,
            chars_extracted=len(texto),
            parser_used=type(self._parser).__name__,
            chunker_used=type(self._chunker).__name__,
            elapsed_ms=elapsed,
            success=saved > 0,
        )

    @classmethod
    def build_auto(cls, file_path: str, doc_type: str = "geral") -> "IngestionPipeline":
        """
        Constrói um pipeline com seleção automática de parser e chunker.
        Conveniente para uso no admin e testes.
        """
        from src.rag.ingestion.parser_factory import ParserFactory
        from src.rag.ingestion.chunker_factory import ChunkerFactory
        from src.rag.embeddings import get_embeddings

        parser = ParserFactory.auto(file_path)
        embeddings = get_embeddings()
        chunker = ChunkerFactory.for_doc_type(doc_type, embeddings=embeddings)
        return cls(parser=parser, chunker=chunker, embeddings=embeddings)