"""
src/infrastructure/observability/metrics.py
--------------------------------------------
Métricas Prometheus expostas via /metrics.

MÉTRICAS DISPONÍVEIS:
  oraculo_requests_total          → contador de requisições por rota e status
  oraculo_tokens_total            → tokens consumidos por modelo
  oraculo_latency_seconds         → latência do pipeline RAG (histograma)
  oraculo_crag_score              → qualidade do retrieval (gauge)
  oraculo_rag_chunks_retrieved    → chunks recuperados por query
  oraculo_active_sessions         → sessões WhatsApp ativas

INTEGRAÇÃO:
  # em main.py:
  from src.infrastructure.observability.metrics import setup_metrics
  setup_metrics(app)
"""
from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Métricas (lazy init — não quebra se prometheus_client não instalado)
# ─────────────────────────────────────────────────────────────────────────────

_metrics_initialized = False
_counters: dict = {}
_histograms: dict = {}
_gauges: dict = {}


def _init_metrics():
    global _metrics_initialized, _counters, _histograms, _gauges
    if _metrics_initialized:
        return

    try:
        from prometheus_client import Counter, Histogram, Gauge

        _counters["requests"] = Counter(
            "oraculo_requests_total",
            "Total de requisições processadas",
            ["route", "status", "role"],
        )
        _counters["tokens"] = Counter(
            "oraculo_tokens_total",
            "Tokens LLM consumidos",
            ["model", "type"],  # type: input | output
        )
        _counters["rag_queries"] = Counter(
            "oraculo_rag_queries_total",
            "Queries RAG executadas",
            ["source", "method"],
        )
        _histograms["latency"] = Histogram(
            "oraculo_latency_seconds",
            "Latência do pipeline completo",
            ["route"],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
        )
        _histograms["rag_latency"] = Histogram(
            "oraculo_rag_latency_seconds",
            "Latência do retrieval RAG",
            ["route"],
            buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 3.0],
        )
        _gauges["crag_score"] = Gauge(
            "oraculo_crag_score",
            "CRAG score médio das últimas queries",
        )
        _gauges["active_sessions"] = Gauge(
            "oraculo_active_sessions",
            "Sessões WhatsApp ativas (chat:* no Redis)",
        )
        _gauges["rag_chunks"] = Gauge(
            "oraculo_rag_chunks_total",
            "Total de chunks indexados no Redis",
        )

        _metrics_initialized = True
        logger.info("✅ Métricas Prometheus inicializadas.")

    except ImportError:
        logger.info("ℹ️  prometheus_client não instalado — métricas desativadas.")
    except Exception as e:
        logger.warning("⚠️  Erro ao inicializar métricas: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def record_request(route: str, status: str, role: str = "guest") -> None:
    _init_metrics()
    c = _counters.get("requests")
    if c:
        try:
            c.labels(route=route, status=status, role=role).inc()
        except Exception:
            pass


def record_tokens(input_tokens: int, output_tokens: int, model: str = "gemini") -> None:
    _init_metrics()
    c = _counters.get("tokens")
    if c:
        try:
            c.labels(model=model, type="input").inc(input_tokens)
            c.labels(model=model, type="output").inc(output_tokens)
        except Exception:
            pass


def record_latency(route: str, seconds: float) -> None:
    _init_metrics()
    h = _histograms.get("latency")
    if h:
        try:
            h.labels(route=route).observe(seconds)
        except Exception:
            pass


def record_rag(route: str, method: str, latency_s: float, crag_score: float = 0.0) -> None:
    _init_metrics()
    try:
        c = _counters.get("rag_queries")
        if c:
            c.labels(source=route, method=method).inc()
        h = _histograms.get("rag_latency")
        if h:
            h.labels(route=route).observe(latency_s)
        g = _gauges.get("crag_score")
        if g:
            g.set(crag_score)
    except Exception:
        pass


def update_active_sessions() -> None:
    """Atualiza gauge de sessões ativas (chama do beat a cada minuto)."""
    _init_metrics()
    g = _gauges.get("active_sessions")
    if not g:
        return
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        _, keys = r.scan(0, match="chat:*", count=500)
        g.set(len(keys))
    except Exception:
        pass


def update_rag_chunks() -> None:
    """Atualiza gauge de chunks RAG (chama após ingestão)."""
    _init_metrics()
    g = _gauges.get("rag_chunks")
    if not g:
        return
    try:
        from src.infrastructure.redis_client import get_redis, PREFIX_CHUNKS
        r = get_redis()
        _, keys = r.scan(0, match=f"{PREFIX_CHUNKS}*", count=2000)
        g.set(len(keys))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI integration
# ─────────────────────────────────────────────────────────────────────────────

def setup_metrics(app) -> None:
    """
    Monta o endpoint /metrics no app FastAPI.
    Chame em create_app() no main.py.
    """
    try:
        from prometheus_client import make_asgi_app
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)
        _init_metrics()
        logger.info("✅ /metrics endpoint montado.")
    except ImportError:
        logger.info("ℹ️  prometheus_client não instalado — /metrics indisponível.")
        logger.info("    Instale com: pip install prometheus-client")
    except Exception as e:
        logger.warning("⚠️  Falha ao montar /metrics: %s", e)


def measure(route: str):
    """
    Decorator que mede latência e registra métricas automaticamente.

    Uso:
        @measure("CALENDARIO")
        async def minha_funcao():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            status = "ok"
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                raise
            finally:
                elapsed = time.monotonic() - t0
                record_latency(route, elapsed)
                record_request(route, status)
        return wrapper
    return decorator