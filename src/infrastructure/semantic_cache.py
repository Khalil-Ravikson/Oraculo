"""
src/infrastructure/semantic_cache.py
------------------------------------
Semantic Cache por Rota para o Oráculo UEMA.
Usa Redis para armazenar respostas de LLM (sintetizadas) e
as reaproveita baseando-se na similaridade de cosseno da query.

TTL/threshold por rota (Fase 3.5): valores convertidos de
`regras_negocio_oraculo.md` §60-61 — a fonte original citava um arquivo
duplicado (`infrastructure/cache/semantic_cache.py`, removido nesta fase,
zero chamadores) que usava distância cosine do Redis Search (0=idêntico,
2=oposto). Este arquivo usa similaridade cosine (1=idêntico), calculada
manualmente em Python — conversão: `similaridade = 1 - distância`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from src.infrastructure import route_registry
from src.infrastructure.redis_client import get_redis_text
from src.rag.embeddings import get_embeddings

logger = logging.getLogger(__name__)


def _get_or_create_metric(metric_cls, name, documentation, labelnames=(), **kwargs):
    """Mesmo padrão de `router/supervisor.py::_get_or_create_metric` — evita
    'Duplicated timeseries in CollectorRegistry' sem acoplar este módulo
    (infraestrutura, importado cedo) à ordem de import de
    `PrometheusMetrics` (observability/metrics.py, mais alto na pilha)."""
    from prometheus_client import REGISTRY
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return metric_cls(name, documentation, labelnames=labelnames, **kwargs)


from prometheus_client import Counter as _Counter

_CACHE_RESULT = _get_or_create_metric(
    _Counter, "oraculo_semantic_cache_result_total",
    "Resultado de consulta ao Semantic Cache por rota", ["rota", "result"],
)
_CACHE_SET = _get_or_create_metric(
    _Counter, "oraculo_semantic_cache_set_total",
    "Respostas gravadas no Semantic Cache por rota", ["rota"],
)

# TTL em segundos por rota — dado factual muda pouco (cache longo), rota
# ambígua/genérica muda de sentido fácil (cache curto).
TTL_POR_ROTA: dict[str, int] = {
    "CALENDARIO": 6 * 3600,
    "EDITAL":     24 * 3600,
    "CONTATOS":   48 * 3600,
    "WIKI":       12 * 3600,
    "GERAL":      30 * 60,
}
# Fallback quando a rota não está em TTL_POR_ROTA: config dinâmica
# `RAG_CACHE_TTL_SECONDS` (default `settings.RAG_CACHE_TTL_SECONDS`).

# Threshold de similaridade cosine por rota — dado factual exige match mais
# rígido (reduz risco de cache "generoso" numa informação sensível a prazo).
THRESHOLD_POR_ROTA: dict[str, float] = {
    "CALENDARIO": 0.92,
    "EDITAL":     0.92,
    "CONTATOS":   0.93,
    "WIKI":       0.88,
    "GERAL":      0.90,
}
_THRESHOLD_DEFAULT = 0.92


class SemanticCache:
    """
    Cache Semântico por Rota.
    Guarda respostas e as recupera se a similaridade de cosseno com a nova query for >= threshold.
    """
    def __init__(self, threshold: float | None = None):
        self._threshold_override = threshold
        self._redis = get_redis_text()
        self._emb = get_embeddings()

    def _threshold_para(self, rota: str) -> float:
        if self._threshold_override is not None:
            return self._threshold_override
        return THRESHOLD_POR_ROTA.get(rota, _THRESHOLD_DEFAULT)

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        import math
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    async def get(self, query: str, rota: str) -> dict | None:
        """
        Verifica se há um cache hit para a query na rota específica.
        """
        if not route_registry.get(rota).cacheavel:
            return None

        try:
            threshold = self._threshold_para(rota)
            # CPU-bound: gerar embedding da query
            query_emb = await asyncio.to_thread(self._emb.embed_query, query)

            pattern = f"semcache:{rota}:*"
            cursor = 0
            best_match = None
            best_score = 0.0

            while True:
                cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                for key in keys:
                    data = self._redis.hgetall(key)
                    if not data:
                        continue

                    emb_str = data.get(b"embedding") or data.get("embedding")
                    if isinstance(emb_str, bytes):
                        emb_str = emb_str.decode('utf-8')

                    if not emb_str:
                        continue

                    cached_emb = json.loads(emb_str)
                    score = self._cosine_similarity(query_emb, cached_emb)

                    if score >= threshold and score > best_score:
                        best_score = score
                        resp_str = data.get(b"response") or data.get("response")
                        if isinstance(resp_str, bytes):
                            resp_str = resp_str.decode('utf-8')
                        best_match = resp_str

                if cursor == 0:
                    break

            if best_match:
                logger.info("✅ Semantic Cache hit! (score=%.3f, rota=%s)", best_score, rota)
                _CACHE_RESULT.labels(rota=rota, result="hit").inc()
                return json.loads(best_match)

            _CACHE_RESULT.labels(rota=rota, result="miss").inc()

        except Exception as e:
            logger.warning("⚠️  Falha no SemanticCache.get: %s", e)

        return None

    async def set(self, query: str, rota: str, response: dict, ttl: int | None = None) -> None:
        """
        Armazena a resposta no cache. `ttl` explícito sobrescreve o padrão
        por rota (`TTL_POR_ROTA`); omitido, resolve pela rota.
        """
        if not query or not rota or not route_registry.get(rota).cacheavel:
            return

        # `ttl` explícito > override por rota (`TTL_POR_ROTA`) > config dinâmica
        # `RAG_CACHE_TTL_SECONDS` (editável via /hub/config, sem restart).
        if ttl is not None:
            ttl_efetivo = ttl
        elif rota in TTL_POR_ROTA:
            ttl_efetivo = TTL_POR_ROTA[rota]
        else:
            from src.infrastructure import dynamic_config
            ttl_efetivo = dynamic_config.get_int("RAG_CACHE_TTL_SECONDS")

        try:
            query_emb = await asyncio.to_thread(self._emb.embed_query, query)
            import hashlib
            query_hash = hashlib.md5(query.encode()).hexdigest()
            key = f"semcache:{rota}:{query_hash}"

            mapping = {
                "query": query,
                "embedding": json.dumps(query_emb),
                "response": json.dumps(response, ensure_ascii=False)
            }
            
            self._redis.hset(key, mapping=mapping)
            self._redis.expire(key, ttl_efetivo)
            logger.debug("💾 Semantic Cache armazenado (rota=%s, ttl=%ds)", rota, ttl_efetivo)
            _CACHE_SET.labels(rota=rota).inc()

        except Exception as e:
            logger.warning("⚠️  Falha no SemanticCache.set: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Gerenciamento (admin) — antes importadas em admin_api.py/admin_commands.py
# sem existirem em lugar nenhum (ImportError em runtime); implementação real.
# ─────────────────────────────────────────────────────────────────────────────

def invalidar_cache_rota(rota: str) -> int:
    """Apaga todas as entradas de cache de uma rota. Síncrona (mesmo padrão
    dos handlers admin que a chamam, `admin_api.py`/`admin_commands.py`, que
    não são async). Retorna a quantidade de chaves removidas."""
    r = get_redis_text()
    pattern = f"semcache:{rota}:*"
    cursor = 0
    total = 0
    try:
        while True:
            cursor, keys = r.scan(cursor, match=pattern, count=100)
            if keys:
                total += r.delete(*keys)
            if cursor == 0:
                break
    except Exception as e:
        logger.warning("⚠️  Falha ao invalidar cache da rota %s: %s", rota, e)
    return total


def cache_stats() -> dict:
    """Estatísticas agregadas do cache pra exibição admin (`/hub/llm-custo`).
    Operação de scan — aceitável aqui por ser rota admin, não hot path."""
    r = get_redis_text()
    por_rota: dict[str, int] = {}
    cursor = 0
    try:
        while True:
            cursor, keys = r.scan(cursor, match="semcache:*", count=200)
            for key in keys:
                # formato: semcache:{rota}:{hash}
                partes = key.split(":", 2)
                if len(partes) >= 2:
                    rota = partes[1]
                    por_rota[rota] = por_rota.get(rota, 0) + 1
            if cursor == 0:
                break
    except Exception as e:
        logger.warning("⚠️  Falha ao coletar cache_stats: %s", e)
    return {"total_entradas": sum(por_rota.values()), "por_rota": por_rota}
