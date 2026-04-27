"""
infrastructure/redis_client.py — v4 (RedisVL + SVS-VAMANA + backward-compat)
==============================================================================

REGRA DE OURO DESTA TRANSIÇÃO:
  Tudo que o Celery/workers precisam → permanece SÍNCRONO (sem await).
  Tudo que o FastAPI/LangGraph usam → pode ser async.

  Motivo: workers Celery rodam em threads separadas com seus próprios event
  loops (ou sem nenhum). asyncio.run() dentro de task Celery cria RuntimeError
  "cannot run nested event loop" se o worker já tem um loop ativo.

FUNÇÕES MANTIDAS SÍNCRONAS (Celery compat):
  salvar_chunk()            → ingestion/pipeline.py, tasks/ingestion_tasks.py
  deletar_chunks_por_source() → tasks_admin.py, rag_admin.py
  busca_hibrida()           → tools (calendar, edital, contatos), rag_search_service.py
  get_working_memory()      → usado em mem legado
  set_working_memory()      → idem
  get_facts() / add_fact()  → memory/long_term_memory.py

FUNÇÕES ASYNC (FastAPI/LangGraph):
  inicializar_indices()     → startup FastAPI
  get_async_chunks_index()  → RedisVLVectorAdapter

ALGORITMO SVS-VAMANA:
  Substituímos HNSW por SVS-VAMANA (graph_max_degree=32).
  ATENÇÃO: requer drop e re-ingestão se havia índice HNSW.
    redis-cli FT.DROPINDEX idx:rag:chunks DD
    redis-cli FT.DROPINDEX idx:tools DD
"""
from __future__ import annotations

import logging
import struct
from functools import lru_cache
from typing import Any

import redis
from redis.commands.search.query import Query
from redisvl.index import AsyncSearchIndex
from redisvl.schema import IndexSchema
import numpy as np
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────
VECTOR_DIM     = 3072        # gemini-embedding-001, validado em produção
IDX_CHUNKS     = "idx:rag:chunks"
IDX_TOOLS      = "idx:tools"
PREFIX_CHUNKS  = "rag:chunk:"
PREFIX_TOOLS   = "tools:emb:"
PREFIX_WORKING = "mem:work:"
PREFIX_FACTS   = "mem:facts:"
PREFIX_CHAT    = "chat:"

HNSW_M  = 16
HNSW_EF = 200


# ─── Schemas RedisVL ──────────────────────────────────────────────────────────
def _schema_chunks() -> IndexSchema:
    return IndexSchema.from_dict({
        "index": {"name": IDX_CHUNKS, "prefix": PREFIX_CHUNKS, "storage_type": "json"},
        "fields": [
            {"name": "content",     "type": "text",    "attrs": {"weight": 2.0}},
            {"name": "source",      "type": "tag"},
            {"name": "doc_type",    "type": "tag"},
            {"name": "chunk_index", "type": "numeric"},
            {"name": "semester",    "type": "tag"},      # NOVO: filtra por 2026.1/2026.2
            {"name": "event_type",  "type": "tag"},      # NOVO: matricula/prova/feriado
            {"name": "label",       "type": "text"},
            {"name": "indexed_at",  "type": "numeric"},  # NOVO: ordenar por frescor
            {
                "name": "embedding", "type": "vector",
                "attrs": {
                    "algorithm": "HNSW", "dims": VECTOR_DIM,
                    "distance_metric": "cosine", "datatype": "float32",
                    "m": 16, "ef_construction": 200
                },
            },
        ],
    })

def _schema_tools() -> IndexSchema:
    """Schema SVS-VAMANA para routing semântico."""
    return IndexSchema.from_dict({
        "index": {
            "name":         IDX_TOOLS,
            "prefix":       PREFIX_TOOLS,
            "storage_type": "json",
        },
        "fields": [
            {"name": "name",        "type": "text"},
            {"name": "description", "type": "text"},
            {
                "name": "embedding",
                "type": "vector",
                "attrs": {
                    "algorithm":       "HNSW",
                    "dims":            VECTOR_DIM,
                    "distance_metric": "cosine",
                    "datatype":        "float32",
                    "m":               16,
                    "ef_construction": 200
                },
            },
        ],
    })


# ─── Clientes síncronos ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    """
    Cliente síncrono (decode_responses=False) para embeddings e estruturas binárias.
    lru_cache: uma conexão por processo, reutiliza o pool TCP.
    """
    client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=10,
        retry_on_timeout=True,
        health_check_interval=30,
        max_connections=20,
    )
    try:
        client.ping()
        logger.info("✅ Redis (bytes) conectado: %s", settings.REDIS_URL)
    except redis.ConnectionError as exc:
        logger.exception("❌ Redis (bytes) offline | causa=%s | erro: %s",
                         type(exc).__name__, exc)
        raise RuntimeError(f"Redis indisponível: {exc}") from exc
    return client


@lru_cache(maxsize=1)
def get_redis_text() -> redis.Redis:
    """
    Cliente síncrono (decode_responses=True) para texto puro.
    FIX v4: lru_cache adicionado — versão anterior criava nova conexão TCP
    a cada chamada, causando 50+ conexões abertas sob carga.
    """
    client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
        retry_on_timeout=True,
        max_connections=10,
    )
    return client


def redis_ok() -> bool:
    try:
        get_redis().ping()
        return True
    except Exception:
        return False


# ─── AsyncSearchIndex factories (para RedisVLVectorAdapter) ─────────────────

def get_async_chunks_index() -> AsyncSearchIndex:
    """
    Factory do AsyncSearchIndex para o adapter de RAG.
    Criado a cada chamada (stateless) — o pool de conexões é gerido internamente.
    """
    return AsyncSearchIndex(schema=_schema_chunks(), redis_url=settings.REDIS_URL)


def get_async_tools_index() -> AsyncSearchIndex:
    return AsyncSearchIndex(schema=_schema_tools(), redis_url=settings.REDIS_URL)


# ─── Inicialização de índices (ASYNC — chamado no startup FastAPI) ────────────

async def inicializar_indices() -> None:
    """
    Cria índices SVS-VAMANA de forma idempotente.
    DEVE ser chamado com `await` no startup do FastAPI.
    NÃO chamar de dentro de tasks Celery.
    """
    for factory, name in [(get_async_chunks_index, IDX_CHUNKS),
                          (get_async_tools_index,  IDX_TOOLS)]:
        index = factory()
        try:
            exists = await index.exists()
            if exists:
                logger.info("ℹ️  Índice '%s' já existe (SVS-VAMANA).", name)
            else:
                await index.create(overwrite=False)
                logger.info("✅ Índice '%s' criado (SVS-VAMANA, dim=%d).", name, VECTOR_DIM)
        except Exception as exc:
            logger.exception("❌ Falha ao criar índice '%s' | erro: %s", name, exc)
            raise
        finally:
            await index.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# OPERAÇÕES SÍNCRONAS — ZONA DE COMPATIBILIDADE CELERY
# Estas funções NÃO serão movidas para async enquanto o Celery estiver ativo.
# O Celery usa redis-py sync internamente; wrappers async criariam deadlocks.
# ─────────────────────────────────────────────────────────────────────────────

# src/infrastructure/redis_client.py — salvar_chunk()
def salvar_chunk(chunk_id, content, source, doc_type, embedding,
                 chunk_index=0, metadata=None):
    r = get_redis()
    key = f"{PREFIX_CHUNKS}{source}:{chunk_id}"
    doc = {
        "content":     content,
        "source":      source,
        "doc_type":    doc_type,
        "chunk_index": chunk_index,
        "embedding":   embedding,
        # NOVO: campos para filtros semânticos precisos
        "semester":    (metadata or {}).get("semester", ""),
        "event_type":  (metadata or {}).get("event_type", ""),
        "label":       (metadata or {}).get("label", ""),
        "indexed_at":  int(__import__("time").time()),
    }
    r.json().set(key, "$", doc)


def deletar_chunks_por_source(source: str) -> int:
    """Remove todos os chunks de um source (SÍNCRONO). Retorna total deletado."""
    r       = get_redis()
    pattern = f"{PREFIX_CHUNKS}{source}:*"
    deleted = 0
    cursor  = 0
    try:
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=100)
            if keys:
                r.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        logger.info("🗑️  Removidos %d chunks de '%s'", deleted, source)
    except Exception as exc:
        logger.exception(
            "❌ deletar_chunks_por_source falhou | source=%s | erro: %s",
            source, exc,
        )
    return deleted


def busca_hibrida(
    query_text:     str,
    query_embedding: list[float],
    source_filter:  str | None = None,
    k_vector:       int = 8,
    k_text:         int = 8,
    rrf_k:          int = 60,
) -> list[dict]:
    """
    Busca híbrida BM25 + Vector com RRF manual (SÍNCRONO).

    Mantida para compatibilidade com:
      - calendar_tool.py, tool_edital.py, tool_contatos.py
      - rag_search_service.py
      - calendar_parser.py

    Para novos consumers, prefer RedisVLVectorAdapter.buscar_hibrido() (async).
    Esta função continuará sendo mantida enquanto as tools síncronas existirem.
    """
    r = get_redis()

    # ── Busca vectorial ────────────────────────────────────────────────────────
    emb_bytes = np.array(query_embedding, dtype=np.float32).tobytes()

    if source_filter:
        safe = source_filter.replace(".", "\\.").replace("-", "\\-")
        vec_q_str = f"(@source:{{{safe}}})=>[KNN {k_vector} @embedding $vec AS vec_score]"
    else:
        vec_q_str = f"*=>[KNN {k_vector} @embedding $vec AS vec_score]"

    vec_query = (
        Query(vec_q_str)
        .sort_by("vec_score")
        .return_fields("content", "source", "doc_type", "chunk_index", "vec_score")
        .dialect(2)
        .paging(0, k_vector)
    )
    try:
        vec_results = r.ft(IDX_CHUNKS).search(vec_query, {"vec": emb_bytes})
        vec_docs    = vec_results.docs
    except Exception as exc:
        logger.warning("⚠️  busca_hibrida: busca vectorial falhou | causa=%s: %s",
                       type(exc).__name__, exc)
        vec_docs = []

    # ── Busca textual (BM25) ──────────────────────────────────────────────────
    safe_text = _escapar_query_redis(query_text)
    if source_filter:
        safe = source_filter.replace(".", "\\.").replace("-", "\\-")
        txt_q_str = f"(@source:{{{safe}}}) ({safe_text})"
    else:
        txt_q_str = safe_text

    txt_query = (
        Query(txt_q_str)
        .return_fields("content", "source", "doc_type", "chunk_index")
        .paging(0, k_text)
    )
    try:
        txt_results = r.ft(IDX_CHUNKS).search(txt_query)
        txt_docs    = txt_results.docs
    except Exception as exc:
        logger.warning("⚠️  busca_hibrida: busca textual falhou | causa=%s: %s",
                       type(exc).__name__, exc)
        txt_docs = []

    # ── RRF ────────────────────────────────────────────────────────────────────
    scores: dict[str, float] = {}
    for rank, doc in enumerate(vec_docs, start=1):
        scores[doc.id] = scores.get(doc.id, 0.0) + 1.0 / (rrf_k + rank)
    for rank, doc in enumerate(txt_docs, start=1):
        scores[doc.id] = scores.get(doc.id, 0.0) + 1.0 / (rrf_k + rank)

    all_docs: dict[str, Any] = {}
    for doc in vec_docs + txt_docs:
        if doc.id not in all_docs:
            all_docs[doc.id] = doc

    resultados = sorted(
        [
            {
                "id":          doc_id,
                "content":     getattr(all_docs[doc_id], "content", ""),
                "source":      getattr(all_docs[doc_id], "source", ""),
                "doc_type":    getattr(all_docs[doc_id], "doc_type", ""),
                "chunk_index": getattr(all_docs[doc_id], "chunk_index", 0),
                "rrf_score":   score,
            }
            for doc_id, score in scores.items()
            if doc_id in all_docs
        ],
        key=lambda x: x["rrf_score"],
        reverse=True,
    )

    logger.debug(
        "🔍 busca_hibrida | vec=%d txt=%d merged=%d | query='%.40s'",
        len(vec_docs), len(txt_docs), len(resultados), query_text,
    )
    return resultados


def _escapar_query_redis(texto: str) -> str:
    import re
    texto_limpo = re.sub(r'[!@\[\]{}()|~^]', ' ', texto)
    termos = texto_limpo.split()
    stopwords = {"de","do","da","o","a","os","as","e","em","para","por","com","um","uma","que","se","no","na","nos","nas"}
    filtrados = [t for t in termos if len(t) > 2 and t.lower() not in stopwords]
    if not filtrados:
        return texto[:100]
    return " | ".join(filtrados[:10])


# ─── Working Memory (síncrono — usado por adapters de memória) ────────────────

def get_working_memory(session_id: str) -> dict:
    r = get_redis_text()
    try:
        return r.hgetall(f"{PREFIX_WORKING}{session_id}") or {}
    except Exception:
        return {}


def set_working_memory(session_id: str, dados: dict, ttl: int = 1800) -> None:
    r   = get_redis_text()
    key = f"{PREFIX_WORKING}{session_id}"
    try:
        if dados:
            r.hset(key, mapping=dados)
            r.expire(key, ttl)
    except Exception as exc:
        logger.warning("⚠️  set_working_memory [%s]: %s", session_id, exc)


def get_facts(user_id: str, limit: int = 10) -> list[str]:
    r = get_redis_text()
    try:
        return r.lrange(f"{PREFIX_FACTS}{user_id}", 0, limit - 1) or []
    except Exception:
        return []


def add_fact(user_id: str, fact: str, ttl: int = 86400 * 30) -> None:
    r   = get_redis_text()
    key = f"{PREFIX_FACTS}{user_id}"
    try:
        r.lpush(key, fact)
        r.ltrim(key, 0, 49)
        r.expire(key, ttl)
    except Exception as exc:
        logger.warning("⚠️  add_fact [%s]: %s", user_id, exc)


# ─── Diagnóstico ──────────────────────────────────────────────────────────────

def diagnosticar() -> dict:
    r      = get_redis()
    r_text = get_redis_text()
    resultado: dict = {}

    try:
        cursor, keys = r.scan(0, match=f"{PREFIX_CHUNKS}*", count=1000)
        resultado["total_chunks"] = len(keys)
        sources: dict[str, int] = {}
        for key in keys:
            partes = key.decode().split(":", 3)
            if len(partes) >= 3:
                src = partes[2]
                sources[src] = sources.get(src, 0) + 1
        resultado["sources"] = sources
    except Exception as exc:
        resultado["sources"] = {"erro": str(exc)}

    for idx_name in [IDX_CHUNKS, IDX_TOOLS]:
        try:
            info = r.ft(idx_name).info()
            resultado[idx_name] = {
                "num_docs":  info.get("num_docs", 0),
                "num_terms": info.get("num_terms", 0),
                "indexing":  info.get("indexing", 0),
            }
        except Exception:
            resultado[idx_name] = {"status": "não existe"}

    try:
        info_mem = r.info("memory")
        resultado["redis_ram_mb"] = round(info_mem.get("used_memory", 0) / 1024 / 1024, 2)
    except Exception:
        pass

    return resultado


async def acquire_lock(identifier: str, ttl_seconds: int = 60) -> bool:
    """
    Tenta adquirir um lock no Redis. Retorna True se conseguiu, False se já existia.
    Usado para evitar processamento duplicado de mensagens do mesmo usuário.
    """
    r = await get_redis_text()
    lock_key = f"lock:{identifier}"
    try:
        # nx=True garante que o comando só funciona se a chave NÃO existir
        adquirido = await r.set(lock_key, "locked", ex=ttl_seconds, nx=True)
        return bool(adquirido)
    except Exception as exc:
        logger.warning("⚠️  Falha ao tentar adquirir lock para %s: %s", identifier, exc)
        # Em caso de falha no Redis, permitimos a mensagem passar para não travar o bot
        return True 

async def release_lock(identifier: str) -> None:
    """Remove o lock do usuário, permitindo novas mensagens."""
    r = await get_redis_text()
    lock_key = f"lock:{identifier}"
    try:
        await r.delete(lock_key)
    except Exception as exc:
        logger.warning("⚠️  Falha ao tentar liberar lock para %s: %s", identifier, exc)