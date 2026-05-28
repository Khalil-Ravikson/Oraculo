"""
src/infrastructure/cache/semantic_cache.py
==========================================
Semantic Cache com TTL inteligente por rota.

ESTRATÉGIA (2 camadas):
  Camada 1 — Exact match:
    Hash SHA256 da query normalizada → Redis GET
    TTL: igual ao TTL semântico da rota
    Latência: ~1ms

  Camada 2 — Semantic match:
    Embedding da query → busca por similaridade coseno no Redis Vector
    Threshold: por rota (dados factuais = mais restrito)
    TTL: configurado por rota
    Latência: ~10-20ms (embedding + busca)

TTL POR ROTA (evidência empírica):
  CALENDARIO  → 6h   (prazos mudam, mas não hora a hora)
  EDITAL      → 24h  (edital muda raramente)
  CONTATOS    → 48h  (contatos mudam ainda menos)
  WIKI        → 12h  (wiki atualiza ocasionalmente)
  GERAL       → 30min (ambíguo = cache curto)
  SAUDACAO    → 2h   (respostas de boas-vindas)

INVALIDAÇÃO:
  invalidar_por_rota(rota)  → deleta TODAS as entradas dessa rota
  invalidar_por_source(source) → deleta entradas de um documento específico
  Chamado automaticamente pelo admin após ingestão de novo documento.

THRESHOLD POR ROTA (Redis COSINE distance [0-2], menor = mais similar):
  Dados factuais (CALENDARIO, EDITAL, CONTATOS): 0.08 (mais restrito)
  Dados gerais (WIKI, GERAL): 0.12 (mais permissivo)

COMPATIBILIDADE:
  Totalmente compatível com redis-py síncrono existente.
  Não requer RedisVL — usa redis.commands.search diretamente.
  Funciona no DB 4 (isolado do vector store principal).
"""
from __future__ import annotations

import hashlib
import json
import logging
import struct
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import redis
from redis.commands.search.field import TagField, TextField, VectorField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.commands.search.query import Query

from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

_IDX_NAME   = "idx:semantic_cache"
_PREFIX     = "scache:"
_VECTOR_DIM = 3072   # gemini-embedding-001

# TTL em segundos por rota
_TTL_POR_ROTA: dict[str, int] = {
    "CALENDARIO":  6  * 3600,   # 6h
    "EDITAL":      24 * 3600,   # 24h
    "CONTATOS":    48 * 3600,   # 48h
    "WIKI":        12 * 3600,   # 12h
    "SAUDACAO":    2  * 3600,   # 2h
    "GERAL":       30 * 60,     # 30min
    "CRUD":        0,            # CRUD nunca cacheia (ação de escrita)
}

# Threshold Redis COSINE distance [0-2] por rota
# Menor = mais estrito (menos cache hits, mais precisão)
_THRESHOLD_POR_ROTA: dict[str, float] = {
    "CALENDARIO": 0.08,
    "EDITAL":     0.08,
    "CONTATOS":   0.07,   # contatos precisam ser muito exatos
    "WIKI":       0.12,
    "SAUDACAO":   0.15,   # saudações são mais genéricas
    "GERAL":      0.10,
    "CRUD":       0.0,    # nunca atinge threshold (não cacheia)
}


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CacheResult:
    hit:      bool
    answer:   str         = ""
    rota:     str         = ""
    score:    float       = 0.0
    layer:    str         = ""   # "exact" | "semantic" | "miss"
    age_secs: int         = 0


# ─────────────────────────────────────────────────────────────────────────────
# SemanticCache
# ─────────────────────────────────────────────────────────────────────────────

class SemanticCache:
    """
    Cache semântico de 2 camadas para respostas LLM.
    Thread-safe. Uma instância por processo.
    """

    def __init__(self, redis_url: str | None = None):
        # DB 4 — isolado do vector store (DB 0) e sessions (DB 3)
        url = (redis_url or settings.REDIS_URL).rstrip("/0123456789") + "/4"
        self._r = redis.Redis.from_url(
            url,
            decode_responses=False,
            socket_connect_timeout=3,
            socket_timeout=5,
            max_connections=10,
        )
        self._r_text = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=5,
            max_connections=5,
        )
        self._index_ready = False
        self._ensure_index()

    # ─── API pública ──────────────────────────────────────────────────────────

    def get(self, query: str, rota: str, embedding: list[float] | None = None) -> CacheResult:
        """
        Busca no cache. Tenta exact match primeiro, depois semântico.
        """
        if not self._pode_cachear(rota):
            return CacheResult(hit=False, layer="miss")

        # Camada 1: Exact match
        exact = self._exact_get(query, rota)
        if exact.hit:
            return exact

        # Camada 2: Semantic match (requer embedding)
        if embedding:
            semantic = self._semantic_get(embedding, rota)
            if semantic.hit:
                return semantic

        return CacheResult(hit=False, layer="miss")

    def set(
        self,
        query:     str,
        answer:    str,
        rota:      str,
        embedding: list[float] | None = None,
        metadata:  dict | None = None,
    ) -> bool:
        """
        Armazena resposta no cache (exact + semantic).
        Retorna True se salvou com sucesso.
        """
        if not self._pode_cachear(rota):
            return False
        if not answer or not answer.strip():
            return False

        ttl = _TTL_POR_ROTA.get(rota, _TTL_POR_ROTA["GERAL"])
        if ttl == 0:
            return False

        try:
            # Sempre salva exact match
            self._exact_set(query, answer, rota, ttl)

            # Salva semantic entry se tiver embedding
            if embedding and self._index_ready:
                self._semantic_set(query, answer, rota, embedding, ttl, metadata or {})

            return True
        except Exception as e:
            logger.warning("⚠️  [CACHE] Falha ao salvar: %s", e)
            return False

    def invalidar_por_rota(self, rota: str) -> int:
        """Invalida todas as entradas de uma rota. Retorna total deletado."""
        return self._deletar_por_prefixo(f"{_PREFIX}{rota.upper()}:*")

    def invalidar_por_source(self, source: str) -> int:
        """
        Invalida entradas cacheadas para um documento específico.
        Útil após re-ingestão de um PDF.
        """
        # Busca keys que contêm o source no metadata
        try:
            keys = []
            cursor = 0
            while True:
                cursor, found = self._r.scan(cursor, match=f"{_PREFIX}*", count=200)
                for k in found:
                    try:
                        meta_raw = self._r.hget(k, "source")
                        if meta_raw and source in (meta_raw.decode() if isinstance(meta_raw, bytes) else meta_raw):
                            keys.append(k)
                    except Exception:
                        pass
                if cursor == 0:
                    break
            if keys:
                self._r.delete(*keys)
            return len(keys)
        except Exception as e:
            logger.warning("⚠️  [CACHE] invalidar_por_source falhou: %s", e)
            return 0

    def stats(self) -> dict:
        """Estatísticas básicas do cache."""
        try:
            cursor = 0
            total = 0
            por_rota: dict[str, int] = {}
            while True:
                cursor, keys = self._r.scan(cursor, match=f"{_PREFIX}*", count=500)
                total += len(keys)
                for k in keys:
                    k_str = k.decode() if isinstance(k, bytes) else k
                    partes = k_str.split(":")
                    if len(partes) >= 3:
                        rota_key = partes[2] if partes[1] == "" else partes[1]
                        por_rota[rota_key] = por_rota.get(rota_key, 0) + 1
                if cursor == 0:
                    break
            return {"total_entries": total, "por_rota": por_rota}
        except Exception:
            return {"total_entries": 0}

    # ─── Exact match (camada 1) ───────────────────────────────────────────────

    def _exact_get(self, query: str, rota: str) -> CacheResult:
        try:
            key = self._exact_key(query, rota)
            raw = self._r_text.get(key)
            if raw:
                data = json.loads(raw)
                ts = data.get("ts", time.time())
                age = int(time.time() - ts)
                logger.debug("✅ [CACHE EXACT HIT] rota=%s age=%ds", rota, age)
                return CacheResult(
                    hit=True, answer=data["answer"], rota=rota,
                    score=1.0, layer="exact", age_secs=age,
                )
        except Exception as e:
            logger.debug("Cache exact get falhou: %s", e)
        return CacheResult(hit=False, layer="miss")

    def _exact_set(self, query: str, answer: str, rota: str, ttl: int) -> None:
        key = self._exact_key(query, rota)
        payload = json.dumps({"answer": answer, "rota": rota, "ts": time.time()}, ensure_ascii=False)
        self._r_text.setex(key, ttl, payload)

    def _exact_key(self, query: str, rota: str) -> str:
        normalized = self._normalize(query)
        h = hashlib.sha256(f"{rota}:{normalized}".encode()).hexdigest()[:24]
        return f"{_PREFIX}exact:{rota.upper()}:{h}"

    # ─── Semantic match (camada 2) ────────────────────────────────────────────

    def _semantic_get(self, embedding: list[float], rota: str) -> CacheResult:
        if not self._index_ready:
            return CacheResult(hit=False, layer="miss")

        threshold = _THRESHOLD_POR_ROTA.get(rota, 0.10)
        if threshold == 0.0:
            return CacheResult(hit=False, layer="miss")

        try:
            emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
            q_str = (
                f"(@rota:{{{rota.upper()}}})=>"
                f"[KNN 1 @embedding $vec AS dist]"
            )
            query = (
                Query(q_str)
                .sort_by("dist")
                .return_fields("answer", "rota", "dist", "ts")
                .dialect(2)
                .paging(0, 1)
            )
            results = self._r.ft(_IDX_NAME).search(query, {"vec": emb_bytes})
            if results.docs:
                doc = results.docs[0]
                dist = float(getattr(doc, "dist", 999))
                if dist <= threshold:
                    ts = float(getattr(doc, "ts", time.time()))
                    age = int(time.time() - ts)
                    answer = getattr(doc, "answer", "")
                    logger.info(
                        "✅ [CACHE SEMANTIC HIT] rota=%s dist=%.4f age=%ds",
                        rota, dist, age,
                    )
                    return CacheResult(
                        hit=True, answer=answer, rota=rota,
                        score=1.0 - dist, layer="semantic", age_secs=age,
                    )
        except Exception as e:
            logger.debug("Cache semantic get falhou: %s", e)
        return CacheResult(hit=False, layer="miss")

    def _semantic_set(
        self,
        query:     str,
        answer:    str,
        rota:      str,
        embedding: list[float],
        ttl:       int,
        metadata:  dict,
    ) -> None:
        try:
            h = hashlib.sha256(f"sem:{rota}:{query[:80]}:{time.time()}".encode()).hexdigest()[:20]
            key = f"{_PREFIX}sem:{rota.upper()}:{h}"
            emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
            doc = {
                "query":     query[:200],
                "answer":    answer,
                "rota":      rota.upper(),
                "ts":        time.time(),
                "source":    metadata.get("source", ""),
                "embedding": emb_bytes,
            }
            self._r.json().set(key, "$", {
                k: v for k, v in doc.items() if k != "embedding"
            })
            self._r.json().set(key, "$.embedding", list(embedding))
            self._r.expire(key, ttl)
        except Exception as e:
            logger.debug("Cache semantic set falhou: %s", e)

    # ─── Índice Redis Search ──────────────────────────────────────────────────

    def _ensure_index(self) -> None:
        """Cria índice de busca vetorial se não existir."""
        try:
            self._r.ft(_IDX_NAME).info()
            self._index_ready = True
            logger.debug("✅ [CACHE] Índice '%s' já existe.", _IDX_NAME)
        except Exception:
            try:
                schema = (
                    TagField("$.rota",   as_name="rota"),
                    TextField("$.query", as_name="query"),
                    TextField("$.answer",as_name="answer"),
                    VectorField(
                        "$.embedding",
                        "HNSW",
                        {
                            "TYPE":             "FLOAT32",
                            "DIM":              _VECTOR_DIM,
                            "DISTANCE_METRIC":  "COSINE",
                            "M":                8,
                            "EF_CONSTRUCTION":  100,
                        },
                        as_name="embedding",
                    ),
                )
                self._r.ft(_IDX_NAME).create_index(
                    schema,
                    definition=IndexDefinition(
                        prefix=[f"{_PREFIX}sem:"],
                        index_type=IndexType.JSON,
                    ),
                )
                self._index_ready = True
                logger.info("✅ [CACHE] Índice semântico criado: '%s'", _IDX_NAME)
            except Exception as e:
                logger.warning("⚠️  [CACHE] Não foi possível criar índice: %s", e)
                self._index_ready = False

    # ─── Utils ────────────────────────────────────────────────────────────────

    def _pode_cachear(self, rota: str) -> bool:
        ttl = _TTL_POR_ROTA.get(rota.upper(), -1)
        return ttl > 0

    def _deletar_por_prefixo(self, pattern: str) -> int:
        deleted = 0
        cursor = 0
        try:
            while True:
                cursor, keys = self._r.scan(cursor, match=pattern, count=200)
                if keys:
                    self._r.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("⚠️  [CACHE] deletar_por_prefixo falhou: %s", e)
        return deleted

    @staticmethod
    def _normalize(text: str) -> str:
        s = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode()
        return s.lower().strip()


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_cache_instance: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SemanticCache()
    return _cache_instance


def get_ttl_por_rota(rota: str) -> int:
    """Expõe TTL para uso externo (ex: admin panel)."""
    return _TTL_POR_ROTA.get(rota.upper(), _TTL_POR_ROTA["GERAL"])