# src/application/tasks/ingestion_tasks.py
"""
Task Celery para ingestão assíncrona de documentos.
Disparada pelo admin via WhatsApp (!ingerir) ou pelo portal web.
"""
from __future__ import annotations
import logging
import os
import time

from src.infrastructure.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name        = "processar_documento",
    bind        = True,
    max_retries = 2,
    default_retry_delay = 30,
    queue       = "admin",
)
def processar_documento(
    self,
    file_path:       str,
    strategy_params: dict,
    chat_id:         str = "",
) -> dict:
    """
    Ingere um documento com a estratégia de chunking especificada.

    strategy_params:
      size:     int   — tamanho do chunk em chars (default 400)
      overlap:  int   — sobreposição em chars (default 60)
      strategy: str   — "recursive" | "markdown" | "semantic"
      doc_type: str   — "calendario" | "edital" | "contatos" | "geral"
      label:    str   — prefixo anti-alucinação
    """
    t0 = time.monotonic()

    if not os.path.exists(file_path):
        logger.error("❌ Arquivo não encontrado: %s", file_path)
        return {"ok": False, "error": f"Arquivo não encontrado: {file_path}"}

    source = os.path.basename(file_path)
    ext    = os.path.splitext(source)[1].lower()

    try:
        # ── 1. Parse ──────────────────────────────────────────────────────────
        texto = _extrair_texto(file_path, ext)
        if not texto.strip():
            return {"ok": False, "error": "Nenhum texto extraído. PDF é scan?"}

        # ── 2. Chunking ───────────────────────────────────────────────────────
        chunks = _criar_chunks(
            texto       = texto,
            source      = source,
            doc_type    = strategy_params.get("doc_type", "geral"),
            chunk_size  = strategy_params.get("size", 400),
            overlap     = strategy_params.get("overlap", 60),
            strategy    = strategy_params.get("strategy", "recursive"),
            label       = strategy_params.get("label", source.upper()),
        )

        if not chunks:
            return {"ok": False, "error": "0 chunks gerados após chunking."}

        # ── 3. Embed e salva no Redis ─────────────────────────────────────────
        from src.rag.embeddings import get_embeddings
        from src.infrastructure.redis_client import salvar_chunk
        import hashlib

        emb_model  = get_embeddings()
        textos_raw = [c["text"] for c in chunks]
        embeddings = emb_model.embed_documents(textos_raw)

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chunk_id = hashlib.md5(f"{source}:{i}".encode()).hexdigest()[:16]
            salvar_chunk(
                chunk_id    = chunk_id,
                content     = chunk["text_final"],
                source      = source,
                doc_type    = strategy_params.get("doc_type", "geral"),
                embedding   = emb,
                chunk_index = i,
                metadata    = chunk.get("metadata", {}),
            )

        ms = int((time.monotonic() - t0) * 1000)
        result = {
            "ok":     True,
            "source": source,
            "chunks": len(chunks),
            "chars":  len(texto),
            "ms":     ms,
        }

        # Notifica o admin via WhatsApp se chat_id fornecido
        if chat_id:
            _notificar_admin(chat_id, result)

        return result

    except Exception as e:
        logger.exception("❌ processar_documento falhou: %s", e)
        raise self.retry(exc=e, countdown=30)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _extrair_texto(file_path: str, ext: str) -> str:
    """Seleciona o parser automaticamente pela extensão."""
    from src.rag.ingestion.parser_factory import ParserFactory
    parser = ParserFactory.auto(file_path)
    return parser.parse(file_path)


def _criar_chunks(
    texto:      str,
    source:     str,
    doc_type:   str,
    chunk_size: int,
    overlap:    int,
    strategy:   str,
    label:      str,
) -> list[dict]:
    """Aplica chunking e adiciona prefixo hierárquico anti-alucinação."""
    from src.rag.ingestion.chunker_factory import ChunkerFactory

    chunker = ChunkerFactory.get(strategy, chunk_size=chunk_size, overlap=overlap)
    raw     = chunker.chunk(texto, source=source, doc_type=doc_type)

    prefixo = f"[{label} | {doc_type}]\n"
    return [
        {
            "text":       c.text,
            "text_final": prefixo + c.text,
            "metadata":   c.metadata,
        }
        for c in raw
        if c.text.strip()
    ]


def _notificar_admin(chat_id: str, result: dict) -> None:
    import asyncio
    try:
        from src.services.evolution_service import EvolutionService
        svc = EvolutionService()
        msg = (
            f"✅ *Ingestão concluída!*\n\n"
            f"📄 `{result['source']}`\n"
            f"🧩 Chunks: *{result['chunks']}*\n"
            f"📊 Chars: {result['chars']:,}\n"
            f"⏱  {result['ms']}ms"
        )
        asyncio.run(svc.enviar_mensagem(chat_id, msg))
    except Exception as e:
        logger.warning("⚠️  Notificação admin falhou: %s", e)