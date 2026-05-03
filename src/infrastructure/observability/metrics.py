# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 9: src/infrastructure/observability/metrics.py
# Responsabilidade: Exportação de métricas Prometheus (Singleton Safe)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
        REGISTRY,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("⚠️ prometheus_client não instalado. Métricas desactivadas.")

# Buckets calibrados para o Oráculo
_REQUEST_BUCKETS   = [50, 100, 200, 500, 1000, 2000, 5000]
_DB_BUCKETS        = [5, 10, 20, 50, 100, 200, 500]
_ROUTER_BUCKETS    = [1, 5, 10, 25, 50, 100, 300]

class PrometheusMetrics:
    """
    Interface de métricas Prometheus (Singleton).
    Garante que as métricas sejam registradas apenas uma vez para evitar o ValueError.
    """
    _instance: Optional[PrometheusMetrics] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(PrometheusMetrics, cls).__new__(cls)
        return cls._instance

    def __init__(self, namespace: str = "oraculo") -> None:
        # Se já foi inicializado ou não há cliente Prometheus, encerra aqui
        if self._initialized or not PROMETHEUS_AVAILABLE:
            return

        self._enabled = True
        ns = namespace

        def _get_or_create(metric_cls, name, documentation, labelnames=(), **kwargs):
            """
            Busca a métrica no registro global. Se já existir, retorna a existente.
            Isso elimina definitivamente o erro 'Duplicated timeseries'.
            """
            if name in REGISTRY._names_to_collectors:
                return REGISTRY._names_to_collectors[name]
            return metric_cls(name, documentation, labelnames=labelnames, **kwargs)

        # ── Counters ──────────────────────────────────────────────────────────
        self._requests_total = _get_or_create(Counter, f"{ns}_requests_total", "Total de mensagens", ["instance"])
        self._blocked_total = _get_or_create(Counter, f"{ns}_requests_blocked_total", "Mensagens bloqueadas")
        self._errors_total = _get_or_create(Counter, f"{ns}_errors_total", "Erros no pipeline", ["node"])
        self._cache_hits_total = _get_or_create(Counter, f"{ns}_cache_hits_total", "Hits no Cache", ["doc_type"])
        self._hitl_total = _get_or_create(Counter, f"{ns}_hitl_confirmations_total", "Confirmações HITL", ["resultado"])
        self._router_method_total = _get_or_create(Counter, f"{ns}_router_method_total", "Método de roteamento", ["method"])

        # ── Histograms ─────────────────────────────────────────────────────────
        self._request_latency = _get_or_create(Histogram, f"{ns}_request_latency_ms", "Latência total", buckets=_REQUEST_BUCKETS)
        self._db_latency = _get_or_create(Histogram, f"{ns}_db_latency_ms", "Latência DB", buckets=_DB_BUCKETS)
        self._router_latency = _get_or_create(Histogram, f"{ns}_router_latency_ms", "Latência roteamento", buckets=_ROUTER_BUCKETS)
        self._retrieval_latency = _get_or_create(Histogram, f"{ns}_retrieval_latency_ms", "Latência busca", buckets=_DB_BUCKETS)

        # ── Gauges ─────────────────────────────────────────────────────────────
        self._active_sessions = _get_or_create(Gauge, f"{ns}_active_sessions", "Sessões activas")

        self._initialized = True
        logger.info("✅ [METRICS] Sistema de métricas carregado com Singleton.")

    def generate_latest_output(self) -> tuple[bytes, str]:
        if not self._enabled:
            return b"# prometheus_client not installed\n", "text/plain"
        return generate_latest(), CONTENT_TYPE_LATEST

    # Métodos de incremento e observação (mantidos conforme sua implementação original)
    def increment_requests_processed(self, instance: str = "default") -> None:
        if self._enabled: self._requests_total.labels(instance=instance).inc()

    def increment_blocked_requests(self) -> None:
        if self._enabled: self._blocked_total.inc()

    def increment_errors(self, node: str = "unknown") -> None:
        if self._enabled: self._errors_total.labels(node=node).inc()

    def increment_cache_hits(self, doc_type: str = "geral") -> None:
        if self._enabled: self._cache_hits_total.labels(doc_type=doc_type).inc()

    def increment_hitl(self, resultado: str) -> None:
        if self._enabled: self._hitl_total.labels(resultado=resultado).inc()

    def increment_router_method(self, method: str) -> None:
        if self._enabled: self._router_method_total.labels(method=method).inc()

    def observe_request_latency(self, ms: int) -> None:
        if self._enabled: self._request_latency.observe(ms)

    def observe_db_latency(self, ms: int) -> None:
        if self._enabled: self._db_latency.observe(ms)

    def observe_router_latency(self, ms: int) -> None:
        if self._enabled: self._router_latency.observe(ms)

    def observe_retrieval_latency(self, ms: int) -> None:
        if self._enabled: self._retrieval_latency.observe(ms)

    def set_active_sessions(self, count: int) -> None:
        if self._enabled: self._active_sessions.set(count)

    def track_latency(self, node_name: str):
        def decorator(fn: Callable) -> Callable:
            @wraps(fn)
            async def wrapper(*args, **kwargs):
                t0 = time.monotonic()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    ms = int((time.monotonic() - t0) * 1000)
                    if self._enabled:
                        if node_name == "retrieve":
                            self._retrieval_latency.observe(ms)
                        elif node_name == "router":
                            self._router_latency.observe(ms)
                    logger.debug("⏱️ [METRICS] %s | %dms", node_name, ms)
            return wrapper
        return decorator