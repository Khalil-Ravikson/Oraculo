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


def _calcular_sha256(file_path: str) -> str:
    import hashlib
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception as e:
        logger.error("❌ Falha ao calcular sha256 para %s: %s", file_path, e)
        return ""


@celery_app.task(
    name        = "processar_documento",
    bind        = True,
    max_retries = 20,
    default_retry_delay = 10,
    queue       = "admin",
)
def processar_documento(
    self,
    file_path:       str,
    strategy_params: dict,
    chat_id:         str = "",
    completed_batches: int = 0,
    accumulated_embeddings: list = None,
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

    # Calcular hash do arquivo
    file_hash = _calcular_sha256(file_path)

    from src.infrastructure.redis_client import (
        deletar_chunks_por_source,
        acquire_token_bucket,
        get_document_hash,
        set_document_hash,
    )

    if completed_batches == 0:
        # Deduplicação incremental
        old_hash = get_document_hash(source)
        if old_hash and old_hash == file_hash:
            logger.info("♻️  [DEDUPLICAÇÃO] Hash idêntico detectado para '%s'. Ignorando ingestão.", source)
            result = {
                "ok":       True,
                "source":   source,
                "bypassed": True,
                "msg":      "Documento idêntico já ingerido. Ignorando.",
            }
            if chat_id:
                _notificar_admin(chat_id, result)
            return result

        # Se hash diferente, limpa chunks antigos antes de começar
        logger.info("🗑️  [DEDUPLICAÇÃO] Chave/Hash diferente para '%s'. Deletando chunks antigos.", source)
        deletar_chunks_por_source(source)

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
        
        # --- RATE LIMIT CONTROL: Vetorização em Lotes com Token Bucket ---
        if accumulated_embeddings is None:
            accumulated_embeddings = []
            
        batch_size = 50  # Processa 50 chunks por lote
        total_lotes = (len(textos_raw) + batch_size - 1) // batch_size
        logger.info("Iniciando vetorização: %d chunks em %d lotes (retomando do lote %d).", 
                    len(textos_raw), total_lotes, completed_batches + 1)
        
        for i in range(completed_batches * batch_size, len(textos_raw), batch_size):
            lote_atual = textos_raw[i:i + batch_size]
            num_lote = (i // batch_size) + 1
            
            # Tenta adquirir 1 token para este lote
            # Taxa limite: 15 requisições por minuto (15 RPM), refill_rate = 0.25 tokens/s
            if not acquire_token_bucket("limiter:embeddings", capacity=15, refill_rate=0.25, requested=1):
                logger.warning("🚨 [RATE LIMIT] Exaustão de tokens no bucket para o lote %d/%d. "
                               "Colocando a task em espera (Celery retry).", num_lote, total_lotes)
                # Reagenda a task no Celery desocupando o worker thread
                raise self.retry(
                    kwargs={
                        "completed_batches": num_lote - 1,
                        "accumulated_embeddings": accumulated_embeddings
                    },
                    countdown=10
                )
            
            logger.info("Vetorizando lote %d/%d (%d chunks)...", num_lote, total_lotes, len(lote_atual))
            
            # Chama a API do Gemini apenas para este lote
            embeddings_lote = emb_model.embed_documents(lote_atual)
            accumulated_embeddings.extend(embeddings_lote)
        # ------------------------------------------------

        for i, (chunk, emb) in enumerate(zip(chunks, accumulated_embeddings)):
            chunk_id = hashlib.md5(f"{source}:{i}".encode()).hexdigest()[:16]
            salvar_chunk(
                chunk_id    = chunk_id,
                content     = chunk["text_final"],
                source      = source,
                doc_type    = strategy_params.get("doc_type", "geral"),
                embedding   = emb,
                chunk_index = i,
                metadata    = {
                    **chunk.get("metadata", {}),
                    "eixo":     strategy_params.get("eixo", "Institucional"),
                    "setor":    strategy_params.get("setor", "Geral"),
                    "tipo_doc": strategy_params.get("tipo_doc", "Geral"),
                    "ano":      strategy_params.get("ano", "2026"),
                    "campus":   strategy_params.get("campus", "Todos"),
                    "label":    strategy_params.get("label", source.upper()),
                },
            )

        # Salva o hash atual no Redis após ingestão completa com sucesso
        set_document_hash(source, file_hash)

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
        from celery.exceptions import CeleryError
        if isinstance(e, CeleryError) or type(e).__name__ in ("Retry", "MaxRetriesExceededError"):
            raise e
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
        if result.get("bypassed"):
            msg = (
                f"ℹ️ *Documento idêntico já ingerido.*\n\n"
                f"📄 Ficheiro: `{result['source']}`\n\n"
                f"Nenhuma alteração detectada. Ignorando re-processamento."
            )
        else:
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