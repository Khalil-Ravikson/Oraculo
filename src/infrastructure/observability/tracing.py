"""
infrastructure/observability/tracing.py — OpenTelemetry (→ Jaeger)
================================================================================
Ponto único de setup + helpers de span. Cobre exatamente a dor documentada em
`pesquisa_arquitetura_producao.md` §4.1: correlacionar um `plan_id`/`session_id`
entre FastAPI → Celery → Gemini → Redis sem grep manual em containers
separados — não é uma plataforma de tracing completa, é span nos MESMOS
pontos-único já instrumentados pra custo (`llm_factory.py::MonitoredLLMProvider`,
`audio_service.py`, `process_message_task.py`), com os atributos semânticos
oficiais `gen_ai.*` (https://opentelemetry.io/docs/specs/semconv/gen-ai/).

Desativado por padrão (`settings.ENABLE_TRACING=False`) — só liga com o
container `jaeger` no ar (profile "monitoring"). Setup e toda chamada de
span são NO-OP seguros se o SDK não estiver configurado/o exporter estiver
fora do ar: telemetria nunca pode derrubar uma resposta real ao usuário,
mesmo princípio já seguido por `metrics.py`/`pricing.py`.

NÃO instrumenta Celery automaticamente (sem `opentelemetry-instrumentation-
celery`) — este projeto tem signals customizados frágeis de event loop
persistente por worker (ver celery_app.py, achados do experimento LangGraph
com AsyncRedisSaver); um instrumentador global de terceiros correndo por
cima desses signals é risco desnecessário. Span de "Celery" aqui é criado à
mão no início de `process_message_task.py`, cobrindo a mensagem inteira.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_configurado = False


def setup_tracing(service_name: str = "oraculo") -> None:
    """Configura o TracerProvider global com exporter OTLP (gRPC) apontando
    pra `settings.OTEL_EXPORTER_ENDPOINT`. Chamar 1x por processo (FastAPI
    no startup, cada worker Celery no `worker_process_init`) — idempotente,
    seguro chamar mais de uma vez. NO-OP se `settings.ENABLE_TRACING=False`
    ou se o SDK falhar ao importar/configurar."""
    global _configurado
    if _configurado:
        return

    from src.infrastructure.settings import settings
    if not settings.ENABLE_TRACING:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        exporter = OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_ENDPOINT, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _configurado = True
        logger.info("✅ [TRACING] OpenTelemetry configurado (endpoint=%s, service=%s)",
                    settings.OTEL_EXPORTER_ENDPOINT, service_name)
    except Exception as exc:
        logger.warning("⚠️ [TRACING] Falha ao configurar OpenTelemetry (tracing desativado): %s", exc)


def instrument_fastapi(app) -> None:
    """Instrumentação automática do FastAPI — cria 1 span por request HTTP,
    isolada via middleware (não toca nada de Celery/LangGraph). NO-OP se
    tracing desativado ou se a lib de instrumentação não estiver instalada."""
    from src.infrastructure.settings import settings
    if not settings.ENABLE_TRACING:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
        logger.info("✅ [TRACING] FastAPI instrumentado.")
    except Exception as exc:
        logger.warning("⚠️ [TRACING] Falha ao instrumentar FastAPI: %s", exc)


class _NoOpSpan:
    def set_attribute(self, *a, **kw): pass
    def record_exception(self, *a, **kw): pass
    def set_status(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def get_tracer(name: str = "oraculo"):
    """Devolve um tracer real (se configurado) ou algo que devolve
    `_NoOpSpan` em `start_as_current_span` — call sites nunca precisam
    checar `settings.ENABLE_TRACING` na mão."""
    from src.infrastructure.settings import settings
    if not settings.ENABLE_TRACING:
        return _NoOpTracer()
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except Exception:
        return _NoOpTracer()


class _NoOpTracer:
    def start_as_current_span(self, *a, **kw):
        return _NoOpSpan()


def llm_span(tracer, operation: str, provider: str, modelo: str, rota: str = ""):
    """Context manager de span pra uma chamada LLM/STT/TTS, com os
    atributos semânticos oficiais `gen_ai.*` (OpenTelemetry GenAI semantic
    conventions). Uso:

        with llm_span(tracer, "chat", provider, modelo, rota) as span:
            resp = await self._provider.gerar_resposta_async(...)
            span.set_attribute("gen_ai.usage.input_tokens", resp.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", resp.output_tokens)
    """
    span_cm = tracer.start_as_current_span(f"gen_ai.{operation}")
    return _LLMSpanWrapper(span_cm, operation, provider, modelo, rota)


class _LLMSpanWrapper:
    """Aplica os atributos gen_ai.* de entrada assim que o span abre —
    encapsulado aqui pra não repetir os `set_attribute` em cada call site."""

    def __init__(self, span_cm, operation, provider, modelo, rota):
        self._cm = span_cm
        self._operation = operation
        self._provider = provider
        self._modelo = modelo
        self._rota = rota

    def __enter__(self):
        span = self._cm.__enter__()
        try:
            span.set_attribute("gen_ai.operation.name", self._operation)
            span.set_attribute("gen_ai.system", self._provider)
            span.set_attribute("gen_ai.request.model", self._modelo)
            if self._rota:
                span.set_attribute("oraculo.rota", self._rota)
        except Exception:
            pass
        return span

    def __exit__(self, *a):
        return self._cm.__exit__(*a)
