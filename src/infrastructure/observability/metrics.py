# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 9: src/infrastructure/observability/metrics.py
# Responsabilidade: Exportação de métricas Prometheus.
# ─────────────────────────────────────────────────────────────────────────────

"""
infrastructure/observability/metrics.py — Prometheus Metrics
=============================================================
Métricas expostas em /metrics (scraping pelo Prometheus).

SEM LANGSMITH, SEM SERVIÇOS EXTERNOS.
Todas as métricas são geradas internamente e exportadas via HTTP.

MÉTRICAS IMPLEMENTADAS:
  Counters:
    oraculo_requests_total          → total de mensagens processadas
    oraculo_requests_blocked_total  → bloqueadas pelo Porteiro (não cadastrados)
    oraculo_errors_total            → erros não tratados
    oraculo_cache_hits_total        → hits no SemanticCache
    oraculo_hitl_confirmations_total→ confirmações HITL (sim/não)

  Histograms (com buckets calibrados para o nosso P99 esperado):
    oraculo_request_latency_ms      → latência total do pipeline
    oraculo_db_latency_ms           → latência do Porteiro (PostgreSQL)
    oraculo_router_latency_ms       → latência do roteamento
    oraculo_retrieval_latency_ms    → latência da busca híbrida

  Gauges:
    oraculo_active_sessions         → sessões activas (HITL + lock)
    oraculo_redis_pool_used         → conexões Redis em uso

DASHBOARD GRAFANA:
  Importar o JSON em src/infrastructure/observability/grafana_dashboard.json
  (a ser criado na próxima sprint).
"""
from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        REGISTRY,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning(
        "⚠️  prometheus_client não instalado. "
        "Adicione 'prometheus-client>=0.20.0' ao requirements.txt. "
        "Métricas desactivadas.",
    )


# ─── Registry isolado (evita conflito em testes) ──────────────────────────────

_REGISTRY = REGISTRY if PROMETHEUS_AVAILABLE else None

_REQUEST_BUCKETS   = [50, 100, 200, 500, 1000, 2000, 5000]    # ms
_DB_BUCKETS        = [5, 10, 20, 50, 100, 200, 500]            # ms
_ROUTER_BUCKETS    = [1, 5, 10, 25, 50, 100, 300]              # ms


class PrometheusMetrics:
    """
    Interface de métricas Prometheus para o Oráculo.

    Design: fail-safe — se prometheus_client não estiver instalado,
    todos os métodos são no-ops silenciosos. O sistema continua a funcionar.
    """

    def __init__(self, namespace: str = "oraculo") -> None:
        if not PROMETHEUS_AVAILABLE:
            self._enabled = False
            return

        self._enabled = True
        ns = namespace

        # ── Counters ──────────────────────────────────────────────────────────
        self._requests_total = Counter(
            f"{ns}_requests_total",
            "Total de mensagens processadas pelo Oráculo",
            ["instance"],
        )
        self._blocked_total = Counter(
            f"{ns}_requests_blocked_total",
            "Mensagens bloqueadas pelo Porteiro (não cadastrados ou inativos)",
        )
        self._errors_total = Counter(
            f"{ns}_errors_total",
            "Erros não tratados no pipeline",
            ["node"],
        )
        self._cache_hits_total = Counter(
            f"{ns}_cache_hits_total",
            "Hits no SemanticCache (respostas servidas sem LLM)",
            ["doc_type"],
        )
        self._hitl_total = Counter(
            f"{ns}_hitl_confirmations_total",
            "Confirmações Human-in-the-Loop",
            ["resultado"],   # "sim" | "não"
        )
        self._router_method_total = Counter(
            f"{ns}_router_method_total",
            "Método de roteamento utilizado",
            ["method"],      # "semantic_router" | "pydantic_llm" | "regex"
        )

        # ── Histograms ─────────────────────────────────────────────────────────
        self._request_latency = Histogram(
            f"{ns}_request_latency_ms",
            "Latência total do pipeline (webhook → resposta enviada)",
            buckets=_REQUEST_BUCKETS,
        )
        self._db_latency = Histogram(
            f"{ns}_db_latency_ms",
            "Latência do Porteiro (consulta PostgreSQL)",
            buckets=_DB_BUCKETS,
        )
        self._router_latency = Histogram(
            f"{ns}_router_latency_ms",
            "Latência do pipeline de roteamento",
            buckets=_ROUTER_BUCKETS,
        )
        self._retrieval_latency = Histogram(
            f"{ns}_retrieval_latency_ms",
            "Latência da busca híbrida (HybridQuery RedisVL)",
            buckets=_DB_BUCKETS,
        )

        # ── Gauges ─────────────────────────────────────────────────────────────
        self._active_sessions = Gauge(
            f"{ns}_active_sessions",
            "Sessões activas (HITL + lock adquirido)",
        )

        logger.info("✅ [METRICS] Prometheus iniciado | namespace=%s", ns)

    # ── Interface pública ─────────────────────────────────────────────────────

    def increment_requests_processed(self, instance: str = "default") -> None:
        if self._enabled:
            self._requests_total.labels(instance=instance).inc()

    def increment_blocked_requests(self) -> None:
        if self._enabled:
            self._blocked_total.inc()

    def increment_errors(self, node: str = "unknown") -> None:
        if self._enabled:
            self._errors_total.labels(node=node).inc()

    def increment_cache_hits(self, doc_type: str = "geral") -> None:
        if self._enabled:
            self._cache_hits_total.labels(doc_type=doc_type).inc()

    def increment_hitl(self, resultado: str) -> None:
        """resultado = "sim" | "não" """
        if self._enabled:
            self._hitl_total.labels(resultado=resultado).inc()

    def increment_router_method(self, method: str) -> None:
        if self._enabled:
            self._router_method_total.labels(method=method).inc()

    def observe_request_latency(self, ms: int) -> None:
        if self._enabled:
            self._request_latency.observe(ms)

    def observe_db_latency(self, ms: int) -> None:
        if self._enabled:
            self._db_latency.observe(ms)

    def observe_router_latency(self, ms: int) -> None:
        if self._enabled:
            self._router_latency.observe(ms)

    def observe_retrieval_latency(self, ms: int) -> None:
        if self._enabled:
            self._retrieval_latency.observe(ms)

    def set_active_sessions(self, count: int) -> None:
        if self._enabled:
            self._active_sessions.set(count)

    def generate_latest_output(self) -> tuple[bytes, str]:
        """Retorna (body, content_type) para o endpoint /metrics."""
        if not self._enabled:
            return b"# prometheus_client not installed\n", "text/plain"
        return generate_latest(), CONTENT_TYPE_LATEST

    # ── Decorator de observabilidade ──────────────────────────────────────────

    def track_latency(self, node_name: str):
        """
        Decorator para medir latência de funções async.

        Uso:
            @metrics.track_latency("retrieve_node")
            async def retrieve_node(state): ...
        """
        def decorator(fn: Callable) -> Callable:
            @wraps(fn)
            async def wrapper(*args, **kwargs):
                t0 = time.monotonic()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    ms = int((time.monotonic() - t0) * 1000)
                    if self._enabled and node_name == "retrieve":
                        self._retrieval_latency.observe(ms)
                    logger.debug(
                        "⏱️  [METRICS] %s | %dms", node_name, ms,
                    )
            return wrapper
        return decorator