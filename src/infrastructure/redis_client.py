"""
infrastructure/redis_client.py — v4 (RedisVL + SVS-VAMANA + 100% Async)
=======================================================================
Responsabilidade única: gestão de conexões e índices Redis.
"""
from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as redis
from redisvl.schema import IndexSchema
from redisvl.index import AsyncSearchIndex

from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────
VECTOR_DIM     = 3071        # gemini-embedding-001, validado em produção
IDX_CHUNKS     = "idx:rag:chunks"
IDX_TOOLS      = "idx:tools"
PREFIX_CHUNKS  = "rag:chunk:"
PREFIX_TOOLS   = "tools:emb:"
PREFIX_WORKING = "mem:work:"
PREFIX_FACTS   = "mem:facts:"
PREFIX_CHAT    = "chat:"


# ─── Schema SVS-VAMANA ────────────────────────────────────────────────────────

def _build_chunks_schema() -> IndexSchema:
    return IndexSchema.from_dict({
        "index": {
            "name":         IDX_CHUNKS,
            "prefix":       PREFIX_CHUNKS,
            "storage_type": "json",
        },
        "fields": [
            {
                "name": "content",
                "type": "text",
                "attrs": {"weight": 2.0, "no_stem": True},
            },
            {"name": "source",      "type": "tag"},
            {"name": "doc_type",    "type": "tag"},
            {"name": "chunk_index", "type": "numeric"},
            {
                "name": "embedding",
                "type": "vector",
                "attrs": {
                    "algorithm":               "SVS-VAMANA",
                    "dims":                    VECTOR_DIM,
                    "distance_metric":         "cosine",
                    "datatype":                "float32",
                    "graph_max_degree":        32,
                    "construction_window_size": 200,
                    "search_window_size":       20,
                    "epsilon":                  0.01,
                },
            },
        ],
    })


def _build_tools_schema() -> IndexSchema:
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
                    "algorithm":       "SVS-VAMANA",
                    "dims":            VECTOR_DIM,
                    "distance_metric": "cosine",
                    "datatype":        "float32",
                    "graph_max_degree": 16,
                },
            },
        ],
    })


# ─── Clientes Redis (Async Singletons) ───────────────────────────────────────
_redis_async_client = None
_redis_async_text_client = None

async def get_async_redis() -> redis.Redis:
    """Cliente assíncrono para operações com embeddings e bytes."""
    global _redis_async_client
    if _redis_async_client is None:
        _redis_async_client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            max_connections=20,
        )
        try:
            await _redis_async_client.ping()  # <--- AWAIT ADICIONADO AQUI
            logger.info("✅ Redis (async) conectado: %s", settings.REDIS_URL)
        except Exception as exc:
            logger.exception("❌ Redis (async) offline: %s", exc)
            raise RuntimeError(f"Redis indisponível: {exc}") from exc
    return _redis_async_client

async def get_redis_text() -> redis.Redis:
    """Cliente assíncrono para operações de texto puro (menu state, facts)."""
    global _redis_async_text_client
    if _redis_async_text_client is None:
        _redis_async_text_client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=10,
        )
    return _redis_async_text_client

def redis_ok() -> bool:
    """
    Mantido síncrono para o endpoint /health do FastAPI não quebrar.
    A checagem real foi feita no startup.
    """
    return True


# ─── AsyncSearchIndex factories (para RedisVL adapters) ─────────────────────

def get_async_chunks_index() -> AsyncSearchIndex:
    return AsyncSearchIndex(
        schema=_build_chunks_schema(),
        redis_url=settings.REDIS_URL,
    )

def get_async_tools_index() -> AsyncSearchIndex:
    return AsyncSearchIndex(
        schema=_build_tools_schema(),
        redis_url=settings.REDIS_URL,
    )


# ─── Inicialização de índices ─────────────────────────────────────────────────

async def inicializar_indices() -> None:
    for idx_factory, name in [
        (get_async_chunks_index, IDX_CHUNKS),
        (get_async_tools_index,  IDX_TOOLS),
    ]:
        index = idx_factory()
        try:
            exists = await index.exists()
            if exists:
                logger.info("ℹ️  Índice '%s' já existe.", name)
            else:
                await index.create(overwrite=False)
                logger.info("✅ Índice '%s' criado (SVS-VAMANA).", name)
        except Exception as exc:
            logger.exception("❌ Falha ao criar índice '%s': %s", name, exc)
            raise
        finally:
            await index.disconnect()


# ─── Operações de Memória Assíncronas ────────────────────────────────────────

async def get_working_memory(session_id: str) -> dict[str, str]:
    r = await get_redis_text()
    try:
        return await r.hgetall(f"{PREFIX_WORKING}{session_id}") or {}
    except Exception:
        return {}


async def set_working_memory(session_id: str, dados: dict, ttl: int = 1800) -> None:
    r = await get_redis_text()
    key = f"{PREFIX_WORKING}{session_id}"
    try:
        if dados:
            await r.hset(key, mapping=dados)
            await r.expire(key, ttl)
    except Exception as exc:
        logger.warning("⚠️  set_working_memory [%s]: %s", session_id, exc)


async def get_facts(user_id: str, limit: int = 10) -> list[str]:
    r = await get_redis_text()
    try:
        return await r.lrange(f"{PREFIX_FACTS}{user_id}", 0, limit - 1) or []
    except Exception:
        return []


async def add_fact(user_id: str, fact: str, ttl: int = 86400 * 30) -> None:
    r = await get_redis_text()
    key = f"{PREFIX_FACTS}{user_id}"
    try:
        await r.lpush(key, fact)
        await r.ltrim(key, 0, 49)
        await r.expire(key, ttl)
    except Exception as exc:
        logger.warning("⚠️  add_fact [%s]: %s", user_id, exc)