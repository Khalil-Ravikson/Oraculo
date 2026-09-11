"""
infrastructure/redis_client.py — v4 (RedisVL + HNSW + backward-compat)
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
  get_async_chunks_index()  → factory de índice async (sem consumidor hoje)

ALGORITMO — HNSW (corrigido em 2026-09-09, item A9):
  Os dois índices (`idx:rag:chunks` e `idx:tools`) são criados com
  `"algorithm": "HNSW"` — ver os schemas abaixo, M=16 e EF=200. Esta
  docstring afirmava SVS-VAMANA e dizia que ele havia "substituído o HNSW";
  isso nunca chegou ao código, e a linha de log de criação repetia a mesma
  informação errada. Trocar de algoritmo exigiria drop e reingestão:
    redis-cli FT.DROPINDEX idx:rag:chunks DD
    redis-cli FT.DROPINDEX idx:tools DD

MIGRAÇÃO — campos `sistema`/`modulo` (taxonomia wiki CTIC, adicionados para
permitir filtro por sistema institucional ex. "SIPAC" na busca híbrida):
  Alterar campos TAG em IndexSchema não é suficiente sozinho — o RediSearch
  não faz ALTER de schema. É necessário recriar o índice:
    redis-cli FT.DROPINDEX idx:rag:chunks DD   # apaga o índice E os documentos (DD)
    (reingerir tudo em seguida — os campos novos default para "Geral"/"Geral"
    em chunks já existentes que não foram reingeridos)
"""
from __future__ import annotations

import asyncio
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
def _vector_dim() -> int:
    """Dimensão do vetor do índice, derivada do provedor de embedding ativo.

    Era a constante `VECTOR_DIM = 3072`, fixa. O problema: `EMBEDDING_PROVIDER`
    é configurável e a dimensão não acompanhava — virar a chave para `local`
    deixava o índice esperando 3072 contra vetores de outra dimensão, e a
    busca passava a falhar ou devolver lixo **sem erro nenhum**.

    Import tardio de propósito: `rag/embeddings.py` puxa dependências pesadas,
    e este módulo é importado no caminho quente."""
    from src.rag.embeddings import dimensao_do_provedor

    return dimensao_do_provedor()


# Compatibilidade: vários pontos ainda leem a constante. Resolvida uma vez, no
# import, porque trocar o provedor exige reiniciar o processo de qualquer jeito
# (o modelo é cacheado por processo).
VECTOR_DIM     = _vector_dim()
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
        # HASH e não JSON (2026-09-11): o RedisJSON guardava o embedding como
        # array de 3072 números em texto — 81 KB por trecho, medido. Em HASH o
        # vetor vai como float32 binário e o mesmo trecho ocupa 21 KB.
        # Redução de 74%, com a busca vetorial filtrada por `doc_type` e
        # `sistema` funcionando idêntica (comparado lado a lado antes de
        # migrar). A wiki inteira saiu de ~1,34 GB para ~355 MB.
        "index": {"name": IDX_CHUNKS, "prefix": PREFIX_CHUNKS, "storage_type": "hash"},
        "fields": [
            {"name": "content",     "type": "text",    "attrs": {"weight": 2.0}},
            {"name": "source",      "type": "tag"},
            {"name": "doc_type",    "type": "tag"},
            {"name": "chunk_index", "type": "numeric"},
            # --- TAXONOMIA UEMA ---
            {"name": "eixo",        "type": "tag"},   # Institucional|Ensino|Pesquisa|Extensao|Sistemas|Servicos
            {"name": "setor",       "type": "tag"},   # PROG|PROEXAE|PPG|CTIC|Reitoria|Geral
            {"name": "tipo_doc",    "type": "tag"},   # Edital|Calendario|Resolucao|Manual|Contatos|Noticia
            {"name": "ano",         "type": "tag"},   # 2024|2025|2026
            {"name": "campus",      "type": "tag"},   # Sao_Luis|Bacabal|Imperatriz|Balsas|Caxias|Todos
            {"name": "sistema",     "type": "tag"},   # SIPAC|SIGUEMA|Geral — sistema institucional (wiki CTIC)
            {"name": "modulo",      "type": "tag"},   # Almoxarifado|Compras|... — módulo dentro do sistema
            # ----------------------
            {"name": "label",       "type": "text"},
            {"name": "indexed_at",  "type": "numeric"},
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
    """Schema HNSW para routing semântico."""
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


# ─── AsyncSearchIndex factories ────────────────────────────────────────────
# Sem consumidor desde a remoção do `RedisVLVectorAdapter` (2026-09-09, item
# A8c). Mantidas porque `inicializar_indices()` logo abaixo usa os mesmos
# schemas e porque criar um índice async é a forma correta de fazer isso a
# partir do FastAPI. Se continuarem sem chamador na próxima varredura, saem.

def get_async_chunks_index() -> AsyncSearchIndex:
    """
    Factory do AsyncSearchIndex para o adapter de RAG.
    Criado a cada chamada (stateless) — o pool de conexões é gerido internamente.
    """
    return AsyncSearchIndex(schema=_schema_chunks(), redis_url=settings.REDIS_URL)


def get_async_tools_index() -> AsyncSearchIndex:
    return AsyncSearchIndex(schema=_schema_tools(), redis_url=settings.REDIS_URL)


# ─── Inicialização de índices (ASYNC — chamado no startup FastAPI) ────────────

async def _avisar_se_dimensao_divergir(index, name: str) -> None:
    """Compara a dimensão do índice EXISTENTE com a do provedor ativo.

    É a trava que faltava para trocar de modelo de embedding com segurança.
    Sem ela, a troca "funciona": o sistema sobe, a busca roda, e devolve
    resultado sem sentido — vetores de modelos diferentes não vivem no mesmo
    espaço, mas o Redis não tem como saber disso.

    Só AVISA, não derruba. Um índice com dimensão errada é um problema de
    operação (exige drop e reingestão, decisão de quem opera), não algo que se
    resolva impedindo a API de subir — o bot ficaria fora do ar sem que a
    busca fosse o único caminho afetado."""
    try:
        info = await index.info()
        attrs = info.get("attributes", []) if isinstance(info, dict) else []
        for attr in attrs:
            # O RediSearch devolve cada atributo como lista PLANA de pares
            # ('identifier', '$.content', 'attribute', 'content', 'type', ...),
            # não como dicionário — a primeira versão desta checagem assumiu
            # dicionário e nunca disparava.
            if isinstance(attr, dict):
                campos = attr
            elif isinstance(attr, (list, tuple)):
                itens = [x.decode() if isinstance(x, (bytes, bytearray)) else str(x) for x in attr]
                campos = dict(zip(itens[::2], itens[1::2]))
            else:
                continue

            dim_atual = campos.get("dim") or campos.get("DIM")
            if dim_atual and int(dim_atual) != VECTOR_DIM:
                logger.error(
                    "🚨 [RAG] Índice '%s' tem dimensão %s, mas o provedor de "
                    "embedding ativo gera %d. A busca vai devolver resultado "
                    "sem sentido. Troca de modelo exige recriar o índice e "
                    "reingerir: FT.DROPINDEX %s DD",
                    name, dim_atual, VECTOR_DIM, name,
                )
                return
    except Exception:  # noqa: BLE001 — checagem best-effort, nunca bloqueia o boot
        logger.debug("Não foi possível conferir a dimensão de '%s'.", name, exc_info=True)


async def inicializar_indices() -> None:
    """
    Cria os índices (HNSW) de forma idempotente.
    DEVE ser chamado com `await` no startup do FastAPI.
    NÃO chamar de dentro de tasks Celery.
    """
    for factory, name in [(get_async_chunks_index, IDX_CHUNKS),
                          (get_async_tools_index,  IDX_TOOLS)]:
        index = factory()
        try:
            exists = await index.exists()
            if exists:
                await _avisar_se_dimensao_divergir(index, name)
                logger.info("ℹ️  Índice '%s' já existe (HNSW).", name)
            else:
                await index.create(overwrite=False)
                logger.info("✅ Índice '%s' criado (HNSW, dim=%d).", name, VECTOR_DIM)
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
def _vetor_bytes(embedding) -> bytes:
    """Embedding → float32 binário, no formato que o RediSearch espera em HASH.

    Aceita lista, tupla ou array. Já em bytes, passa direto — alguns
    chamadores podem ter convertido antes."""
    if isinstance(embedding, (bytes, bytearray)):
        return bytes(embedding)
    return np.asarray(embedding, dtype=np.float32).tobytes()


def salvar_chunk(chunk_id, content, source, doc_type, embedding,
                 chunk_index=0, metadata=None):
    r = get_redis()
    key = f"{PREFIX_CHUNKS}{source}:{chunk_id}"
    meta = metadata or {}
    doc = {
        "content":     content,
        "source":      source,
        "doc_type":    doc_type,
        "chunk_index": chunk_index,
        # float32 binário, não lista de floats: em HASH o Redis guarda os
        # bytes crus. Como lista JSON, cada número virava texto e o trecho
        # inteiro custava 4x mais memória.
        "embedding":   _vetor_bytes(embedding),
        # Taxonomia — usa "Geral"/"Todos" como default seguro
        "eixo":        meta.get("eixo", "Institucional"),
        "setor":       meta.get("setor", "Geral"),
        "tipo_doc":    meta.get("tipo_doc", doc_type.capitalize()),
        "ano":         meta.get("ano", "2026"),
        "campus":      meta.get("campus", "Todos"),
        "sistema":     meta.get("sistema", "Geral"),
        "modulo":      meta.get("modulo", "Geral"),
        "label":       meta.get("label", ""),
        "indexed_at":  int(__import__("time").time()),
    }
    r.hset(key, mapping=doc)

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
    metadata_filter: dict | None = None,
) -> list[dict]:
    """
    Busca híbrida BM25 + Vector com RRF manual (SÍNCRONO) e suporte a filtros de metadados.

    Mantida para compatibilidade com:
      - calendar_tool.py, tool_edital.py, tool_contatos.py
      - rag_search_service.py
      - calendar_parser.py

    **Este é o caminho de busca de produção.** A recomendação anterior aqui
    mandava preferir `RedisVLVectorAdapter.buscar_hibrido()` — esse adapter
    emitia `FT.HYBRID`, não suportado nesta versão do Redis Stack, nunca teve
    consumidor, e foi removido em 2026-09-09 (item A8c, TD-015/TD-023).
    """
    r = get_redis()

    # ── Construção dos filtros dinâmicos de metadados ─────────────────────────
    filter_parts = []
    if source_filter:
        safe = source_filter.replace(".", "\\.").replace("-", "\\-")
        filter_parts.append(f"@source:{{{safe}}}")

    if metadata_filter:
        for key, val in metadata_filter.items():
            if val:
                if isinstance(val, list):
                    # Multi-tag match (ex: @campus:{sao_luis|todos})
                    escaped_vals = [v.replace(".", "\\.").replace("-", "\\-").replace(" ", "\\ ") for v in val]
                    safe_val = "|".join(escaped_vals)
                else:
                    safe_val = str(val).replace(".", "\\.").replace("-", "\\-").replace(" ", "\\ ")
                filter_parts.append(f"@{key}:{{{safe_val}}}")

    filter_prefix = f"({' '.join(filter_parts)})" if filter_parts else "*"

    # ── Busca vectorial ────────────────────────────────────────────────────────
    emb_bytes = np.array(query_embedding, dtype=np.float32).tobytes()
    vec_q_str = f"{filter_prefix}=>[KNN {k_vector} @embedding $vec AS vec_score]"

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
    if filter_parts:
        txt_q_str = f"({' '.join(filter_parts)}) ({safe_text})"
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
    r = get_redis_text()
    lock_key = f"lock:{identifier}"
    try:
        # nx=True garante que o comando só funciona se a chave NÃO existir
        adquirido = await asyncio.to_thread(r.set, lock_key, "locked", ex=ttl_seconds, nx=True)
        return bool(adquirido)
    except Exception as exc:
        logger.warning("⚠️  Falha ao tentar adquirir lock para %s: %s", identifier, exc)
        # Em caso de falha no Redis, permitimos a mensagem passar para não travar o bot
        return True 

async def release_lock(identifier: str) -> None:
    """Remove o lock do usuário, permitindo novas mensagens."""
    r = get_redis_text()
    lock_key = f"lock:{identifier}"
    try:
        await asyncio.to_thread(r.delete, lock_key)
    except Exception as exc:
        logger.warning("⚠️  Falha ao tentar liberar lock para %s: %s", identifier, exc)


def acquire_token_bucket(key: str, capacity: int = 15, refill_rate: float = 0.25, requested: int = 1) -> bool:
    """
    Algoritmo Token Bucket atômico executado via script Lua no Redis.
    SÍNCRONO — compatível com workers Celery.
    """
    r = get_redis()
    lua_script = """
    local key = KEYS[1]
    local capacity = tonumber(ARGV[1])
    local refill_rate = tonumber(ARGV[2])
    local requested = tonumber(ARGV[3])
    local now = tonumber(ARGV[4])

    local data = redis.call('HMGET', key, 'tokens', 'last_updated')
    local tokens = tonumber(data[1])
    local last_updated = tonumber(data[2])

    if not tokens then
        tokens = capacity
        last_updated = now
    else
        local elapsed = math.max(0, now - last_updated)
        tokens = math.min(capacity, tokens + elapsed * refill_rate)
    end

    if tokens >= requested then
        tokens = tokens - requested
        redis.call('HMSET', key, 'tokens', tokens, 'last_updated', now)
        redis.call('EXPIRE', key, 86400)
        return 1
    else
        return 0
    end
    """
    import time
    now = time.time()
    try:
        res = r.eval(lua_script, 1, key, capacity, refill_rate, requested, now)
        return int(res) == 1
    except Exception as exc:
        logger.error("❌ Erro ao executar acquire_token_bucket para %s: %s", key, exc)
        # Fallback seguro para não travar o fluxo caso o Redis esteja com problemas de eval
        return True


def get_document_hash(source: str) -> str | None:
    """Retorna o hash SHA-256 do documento no Redis (SÍNCRONO)."""
    try:
        r = get_redis_text()
        return r.hget("ingest:hashes", source)
    except Exception as exc:
        logger.warning("⚠️  Falha ao obter hash do documento %s: %s", source, exc)
        return None


def set_document_hash(source: str, sha256_hash: str) -> None:
    """Salva o hash SHA-256 do documento no Redis (SÍNCRONO)."""
    try:
        r = get_redis_text()
        r.hset("ingest:hashes", source, sha256_hash)
    except Exception as exc:
        logger.warning("⚠️  Falha ao salvar hash do documento %s: %s", source, exc)


def registrar_tokens_redis(session_id: str, input_tokens: int, output_tokens: int) -> None:
    """Registra de forma incremental o uso de tokens para uma sessão (SÍNCRONO)."""
    try:
        r = get_redis_text()
        key = f"eval:tokens:{session_id}"
        r.hincrby(key, "input", input_tokens)
        r.hincrby(key, "output", output_tokens)
        r.expire(key, 3600)  # 1 hora de TTL é suficiente para avaliações
    except Exception as exc:
        logger.warning("⚠️  Falha ao registrar tokens no Redis para %s: %s", session_id, exc)


def obter_tokens_redis(session_id: str) -> tuple[int, int]:
    """Obtém os tokens acumulados (entrada, saída) para uma sessão (SÍNCRONO)."""
    try:
        r = get_redis_text()
        key = f"eval:tokens:{session_id}"
        data = r.hgetall(key)
        if data:
            return int(data.get("input", 0)), int(data.get("output", 0))
    except Exception:
        pass
    return 0, 0