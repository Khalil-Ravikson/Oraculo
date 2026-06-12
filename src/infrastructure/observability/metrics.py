"""
infrastructure/observability/metrics.py — Métricas Prometheus (Singleton Safe)
================================================================================

Inclui métricas de LLM (tokens, custo) para o Dashboard Grafana.
Compatível com prometheus-fastapi-instrumentator + prometheus-client.
"""
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
    logger.warning("⚠️ prometheus_client não instalado. Métricas desativadas.")

# Buckets calibrados para o Oráculo
_REQUEST_BUCKETS   = [50, 100, 200, 500, 1000, 2000, 5000]
_DB_BUCKETS        = [5, 10, 20, 50, 100, 200, 500]
_ROUTER_BUCKETS    = [1, 5, 10, 25, 50, 100, 300]
_TOKEN_BUCKETS     = [100, 500, 1000, 2000, 5000, 10000, 20000]


def _get_or_create(metric_cls, name, documentation, labelnames=(), **kwargs):
    """
    Busca a métrica no registro global. Se já existir, retorna a existente.
    Elimina o erro 'Duplicated timeseries' em hot-reload.
    """
    if not PROMETHEUS_AVAILABLE:
        return _NoOpMetric()
    collectors = {c.describe()[0].name: c
                  for c in REGISTRY._names_to_collectors.values()
                  if hasattr(c, 'describe') and c.describe()}
    full_name = name
    if full_name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[full_name]
    return metric_cls(name, documentation, labelnames=labelnames, **kwargs)


class _NoOpMetric:
    """Stub para quando prometheus_client não está instalado."""
    def labels(self, **kwargs): return self
    def inc(self, amount=1): pass
    def set(self, value): pass
    def observe(self, value): pass


class PrometheusMetrics:
    """
    Interface de métricas Prometheus (Singleton).
    Registra cada métrica apenas uma vez — seguro para hot-reload.
    """
    _instance: Optional["PrometheusMetrics"] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, namespace: str = "oraculo") -> None:
        if self._initialized:
            return

        self._enabled = PROMETHEUS_AVAILABLE
        ns = namespace

        # ── Counters ──────────────────────────────────────────────────────────
        self._requests_total = _get_or_create(
            Counter, f"{ns}_requests_total",
            "Total de mensagens processadas", ["instance"])

        self._blocked_total = _get_or_create(
            Counter, f"{ns}_requests_blocked_total",
            "Mensagens bloqueadas pelo admin")

        self._errors_total = _get_or_create(
            Counter, f"{ns}_errors_total",
            "Erros no pipeline por nó", ["node"])

        self._cache_hits_total = _get_or_create(
            Counter, f"{ns}_cache_hits_total",
            "Hits no Cache por tipo de documento", ["doc_type"])

        self._hitl_total = _get_or_create(
            Counter, f"{ns}_hitl_confirmations_total",
            "Confirmações HITL por resultado", ["resultado"])

        self._router_method_total = _get_or_create(
            Counter, f"{ns}_router_method_total",
            "Método de roteamento usado", ["method"])

        # ── Counters LLM (tokens e custo) ─────────────────────────────────────
        # Usado nos Painéis 2 e 3 do Grafana Dashboard
        self._llm_tokens_total = _get_or_create(
            Counter, f"{ns}_llm_tokens_total",
            "Total de tokens consumidos pelo LLM", ["direction"])
        # direction = "input" | "output"

        self._llm_cost_usd_total = _get_or_create(
            Counter, f"{ns}_llm_cost_usd_total",
            "Custo acumulado em USD das chamadas ao LLM")
        # Incrementado a cada geração com o custo estimado daquela chamada

        # ── Histograms ─────────────────────────────────────────────────────────
        self._request_latency = _get_or_create(
            Histogram, f"{ns}_request_latency_ms",
            "Latência total da requisição em ms",
            buckets=_REQUEST_BUCKETS)

        self._db_latency = _get_or_create(
            Histogram, f"{ns}_db_latency_ms",
            "Latência de operações no banco/Redis em ms",
            buckets=_DB_BUCKETS)

        self._router_latency = _get_or_create(
            Histogram, f"{ns}_router_latency_ms",
            "Latência do passo de roteamento em ms",
            buckets=_ROUTER_BUCKETS)

        self._retrieval_latency = _get_or_create(
            Histogram, f"{ns}_retrieval_latency_ms",
            "Latência da busca RAG em ms",
            buckets=_DB_BUCKETS)

        self._llm_generation_latency = _get_or_create(
            Histogram, f"{ns}_llm_generation_latency_ms",
            "Latência da geração LLM (Gemini) em ms",
            buckets=_REQUEST_BUCKETS)

        self._tokens_per_request = _get_or_create(
            Histogram, f"{ns}_tokens_per_request",
            "Distribuição de tokens por requisição",
            buckets=_TOKEN_BUCKETS)

        # ── Gauges ─────────────────────────────────────────────────────────────
        self._active_sessions = _get_or_create(
            Gauge, f"{ns}_active_sessions",
            "Número de sessões ativas no momento")

        self._crag_score_last = _get_or_create(
            Gauge, f"{ns}_crag_score_last",
            "Último CRAG score calculado (qualidade do retrieval)")

        # ── SIGAA Metrics ─────────────────────────────────────────────────────
        self._sigaa_scraping_latency = _get_or_create(
            Histogram, f"{ns}_sigaa_scraping_latency_ms",
            "Latência do scraping do SIGAA em ms", ["operacao"],
            buckets=_REQUEST_BUCKETS)
        
        self._sigaa_login_latency = _get_or_create(
            Histogram, f"{ns}_sigaa_login_latency_ms",
            "Latência de login no SIGAA em ms",
            buckets=_REQUEST_BUCKETS)

        self._sigaa_pdf_download_latency = _get_or_create(
            Histogram, f"{ns}_sigaa_pdf_download_latency_ms",
            "Latência de download de PDF do SIGAA em ms",
            buckets=_REQUEST_BUCKETS)

        self._sigaa_pdf_parsing_latency = _get_or_create(
            Histogram, f"{ns}_sigaa_pdf_parsing_latency_ms",
            "Latência do parsing de PDF do SIGAA em ms",
            buckets=_REQUEST_BUCKETS)

        self._sigaa_success_total = _get_or_create(
            Counter, f"{ns}_sigaa_success_total",
            "Total de operações bem-sucedidas do SIGAA", ["operacao"])

        self._sigaa_failure_total = _get_or_create(
            Counter, f"{ns}_sigaa_failure_total",
            "Total de falhas em operações do SIGAA", ["operacao"])

        self._sigaa_selector_changes_total = _get_or_create(
            Counter, f"{ns}_sigaa_selector_changes_total",
            "Total de alterações detectadas nos seletores HTML do SIGAA")

        self._initialized = True
        logger.info("✅ [METRICS] PrometheusMetrics inicializado (Singleton).")

    # ── Geração de output Prometheus ──────────────────────────────────────────

    def generate_latest_output(self) -> tuple[bytes, str]:
        if not self._enabled:
            return b"# prometheus_client not installed\n", "text/plain"
        return generate_latest(), CONTENT_TYPE_LATEST

    # ── Métodos de pipeline geral ─────────────────────────────────────────────

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
        if self._enabled:
            self._hitl_total.labels(resultado=resultado).inc()

    def increment_router_method(self, method: str) -> None:
        if self._enabled:
            self._router_method_total.labels(method=method).inc()

    # ── Métodos LLM (tokens e custo) ──────────────────────────────────────────

    def record_llm_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int = 0,
    ) -> None:
        """
        Registra uma chamada LLM completa: tokens, custo e latência.

        Deve ser chamado em _step_generate() após obter response.usage_metadata.

        Exemplo:
            metrics.record_llm_usage(
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=custo_usd,
                latency_ms=ms,
            )
        """
        if not self._enabled:
            return
        if input_tokens:
            self._llm_tokens_total.labels(direction="input").inc(input_tokens)
        if output_tokens:
            self._llm_tokens_total.labels(direction="output").inc(output_tokens)
        total_tokens = input_tokens + output_tokens
        if total_tokens:
            self._tokens_per_request.observe(total_tokens)
        if cost_usd:
            self._llm_cost_usd_total.inc(cost_usd)
        if latency_ms:
            self._llm_generation_latency.observe(latency_ms)

    # ── Observabilidade de latências ──────────────────────────────────────────

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

    # ── Gauges ────────────────────────────────────────────────────────────────

    def set_active_sessions(self, count: int) -> None:
        if self._enabled:
            self._active_sessions.set(count)

    def set_crag_score(self, score: float) -> None:
        if self._enabled:
            self._crag_score_last.set(round(score, 4))

    # ── SIGAA Observer Methods ────────────────────────────────────────────────

    def observe_sigaa_scraping_latency(self, operacao: str, ms: int) -> None:
        if self._enabled:
            self._sigaa_scraping_latency.labels(operacao=operacao).observe(ms)

    def observe_sigaa_login_latency(self, ms: int) -> None:
        if self._enabled:
            self._sigaa_login_latency.observe(ms)

    def observe_sigaa_pdf_download_latency(self, ms: int) -> None:
        if self._enabled:
            self._sigaa_pdf_download_latency.observe(ms)

    def observe_sigaa_pdf_parsing_latency(self, ms: int) -> None:
        if self._enabled:
            self._sigaa_pdf_parsing_latency.observe(ms)

    def increment_sigaa_success(self, operacao: str) -> None:
        if self._enabled:
            self._sigaa_success_total.labels(operacao=operacao).inc()

    def increment_sigaa_failure(self, operacao: str) -> None:
        if self._enabled:
            self._sigaa_failure_total.labels(operacao=operacao).inc()

    def increment_sigaa_selector_change(self) -> None:
        if self._enabled:
            self._sigaa_selector_changes_total.inc()

    # ── Decorator de latência ─────────────────────────────────────────────────

    def track_latency(self, node_name: str):
        """
        Decorator que mede latência de um nó assíncrono.

        Uso:
            @metrics.track_latency("retrieve")
            async def _step_retrieve(self, ctx, emit): ...
        """
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
                        elif node_name == "generate":
                            self._llm_generation_latency.observe(ms)
                        else:
                            self._db_latency.observe(ms)
                    logger.debug("⏱️ [METRICS] %s | %dms", node_name, ms)
            return wrapper
        return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Singleton accessor
# ─────────────────────────────────────────────────────────────────────────────

_metrics_instance: Optional[PrometheusMetrics] = None


def get_metrics() -> PrometheusMetrics:
    """Retorna o singleton das métricas. Thread-safe para uso em FastAPI."""
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = PrometheusMetrics()
    return _metrics_instance