"""
infrastructure/observability/langfuse_client.py — Tracing LLM (Sprint 1)
=========================================================================

POR QUE LANGFUSE E NÃO LANGSMITH?
  - Open-source, self-hosted (compliance total, zero dados para fora)
  - Container incluído no docker-compose.yml
  - SDK Python simples: @observe decorator + context managers
  - Integra com a nossa arquitetura sem poluir o domínio

COMO USAR:
  1. Sobre qualquer função que chama LLM:
       from src.infrastructure.observability.langfuse_client import observe_llm
       @observe_llm(name="rag_generation")
       async def minha_funcao():
           ...

  2. Contexto manual (para spans customizados):
       with langfuse_span("vector_search", input={"query": q}) as span:
           resultados = busca_hibrida(...)
           span.update(output={"chunks": len(resultados)})

  3. Score de qualidade (CRAG):
       langfuse_score(trace_id, "crag_score", 0.85)

CONFIGURAÇÃO (.env):
  LANGFUSE_SECRET_KEY=sk-lf-...
  LANGFUSE_PUBLIC_KEY=pk-lf-...
  LANGFUSE_HOST=http://langfuse:3000   ← serviço no docker-compose
"""
from __future__ import annotations

import functools
import logging
from contextlib import contextmanager
from typing import Any, Callable, Generator
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
logger = logging.getLogger(__name__)
# Singleton robusto
_lf_instance: Langfuse | None = None
# ─────────────────────────────────────────────────────────────────────────────
# Setup do cliente Langfuse (lazy init)
# ─────────────────────────────────────────────────────────────────────────────

def _get_langfuse() -> Langfuse | None:
    global _lf_instance
    if _lf_instance is None:
        try:
            from src.infrastructure.settings import settings
            if not settings.LANGFUSE_SECRET_KEY or not settings.LANGFUSE_PUBLIC_KEY:
                return None
            _lf_instance = Langfuse(
                secret_key=settings.LANGFUSE_SECRET_KEY,
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                host=settings.LANGFUSE_HOST,
                flush_at=1,          # ← flush imediato, não em batch
                flush_interval=0.5,  # ← máx 500ms de espera
            )
        except Exception as e:
            logger.warning("Langfuse indisponível: %s", e)
    return _lf_instance

def get_langfuse_handler(session_id: str = "", user_id: str = "") -> CallbackHandler | None:
    """Retorna handler pronto para passar ao LangChain .ainvoke()"""
    from src.infrastructure.settings import settings
    
    if not settings.LANGFUSE_SECRET_KEY or not settings.LANGFUSE_PUBLIC_KEY:
        return None

    # Na V2, passamos as credenciais direto pro CallbackHandler
    return CallbackHandler(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
        session_id=session_id or None,
        user_id=user_id or None,
        metadata={"framework": "oraculo_uema_v5"},
        flush_at=1,          # ← flush imediato
        flush_interval=0.5,  # ← máx 500ms de espera
    )

def flush_langfuse() -> None:
    lf = _get_langfuse()
    if lf:
        try:
            lf.flush()
        except Exception as e:
            logger.warning("Langfuse flush falhou: %s", e)

def langfuse() -> Any | None:
    """Accessor para o singleton do cliente Langfuse."""
    global _langfuse_client
    if _langfuse_client is None:
        _langfuse_client = _get_langfuse()
    return _langfuse_client


# ─────────────────────────────────────────────────────────────────────────────
# Decorator @observe_llm
# ─────────────────────────────────────────────────────────────────────────────

def observe_llm(
    name: str = "",
    capture_input: bool = True,
    capture_output: bool = True,
):
    """
    Decorator que cria um span Langfuse para funções que chamam LLM.
    
    Uso:
        @observe_llm(name="gemini_rag_generation")
        async def gerar_resposta(prompt: str) -> str:
            ...

    O decorator é NO-OP se Langfuse estiver offline — sem impacto na produção.
    """
    def decorator(func: Callable) -> Callable:
        span_name = name or func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            lf = langfuse()
            if lf is None:
                return await func(*args, **kwargs)

            trace = lf.trace(name=span_name)
            generation = trace.generation(
                name=span_name,
                input=_safe_input(args, kwargs) if capture_input else None,
            )
            try:
                result = await func(*args, **kwargs)
                if capture_output:
                    generation.end(output=_safe_output(result))
                else:
                    generation.end()
                return result
            except Exception as e:
                generation.end(
                    level="ERROR",
                    status_message=str(e)[:300],
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            lf = langfuse()
            if lf is None:
                return func(*args, **kwargs)

            trace = lf.trace(name=span_name)
            generation = trace.generation(
                name=span_name,
                input=_safe_input(args, kwargs) if capture_input else None,
            )
            try:
                result = func(*args, **kwargs)
                if capture_output:
                    generation.end(output=_safe_output(result))
                else:
                    generation.end()
                return result
            except Exception as e:
                generation.end(level="ERROR", status_message=str(e)[:300])
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Context manager para spans customizados
# ─────────────────────────────────────────────────────────────────────────────

class _NoOpSpan:
    """Span de no-op quando Langfuse está offline."""
    def update(self, **kwargs): pass
    def end(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


@contextmanager
def langfuse_span(
    name: str,
    input: Any = None,
    metadata: dict | None = None,
) -> Generator[_NoOpSpan, None, None]:
    """
    Context manager para criar spans customizados.

    Uso:
        with langfuse_span("vector_search", input={"query": q}) as span:
            resultados = busca_hibrida(query)
            span.update(output={"n_chunks": len(resultados), "crag": 0.85})
    """
    lf = langfuse()
    if lf is None:
        yield _NoOpSpan()
        return

    trace = lf.trace(name=name)
    span  = trace.span(name=name, input=input, metadata=metadata)
    try:
        yield span
        span.end()
    except Exception as e:
        span.end(level="ERROR", status_message=str(e)[:300])
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Score de qualidade (ex: CRAG score)
# ─────────────────────────────────────────────────────────────────────────────

def langfuse_score(trace_id: str, name: str, value: float, comment: str = "") -> None:
    """
    Registra um score de qualidade no Langfuse.
    Ideal para o CRAG score gerado pelo retriever.

    Uso (em node_rag):
        langfuse_score(trace_id, "crag_score", resultado.crag_score)
        langfuse_score(trace_id, "n_chunks",   len(resultado.chunks))
    """
    lf = langfuse()
    if lf is None:
        return
    try:
        lf.score(
            trace_id=trace_id,
            name=name,
            value=value,
            comment=comment[:200] if comment else "",
        )
    except Exception as e:
        logger.debug("⚠️  Langfuse score falhou: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Flush no shutdown
# ─────────────────────────────────────────────────────────────────────────────

def flush_langfuse() -> None:
    """
    Deve ser chamado no shutdown do FastAPI para garantir que todos os
    spans pendentes sejam enviados ao servidor Langfuse antes de encerrar.

    No main.py:
        @app.on_event("shutdown")
        async def shutdown():
            from src.infrastructure.observability.langfuse_client import flush_langfuse
            flush_langfuse()
    """
    lf = langfuse()
    if lf:
        try:
            lf.flush()
            logger.info("✅ Langfuse: spans flushed com sucesso.")
        except Exception as e:
            logger.warning("⚠️  Langfuse flush falhou: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _safe_input(args: tuple, kwargs: dict) -> dict:
    """Serializa input de forma segura (evita objetos não-serializáveis)."""
    try:
        return {
            "args":   [str(a)[:500] for a in args if not callable(a)],
            "kwargs": {k: str(v)[:500] for k, v in kwargs.items()},
        }
    except Exception:
        return {}


def _safe_output(result: Any) -> Any:
    """Serializa output de forma segura."""
    try:
        if hasattr(result, "conteudo"):
            return {
                "conteudo":      str(result.conteudo)[:1000],
                "input_tokens":  getattr(result, "input_tokens", 0),
                "output_tokens": getattr(result, "output_tokens", 0),
                "sucesso":       getattr(result, "sucesso", True),
            }
        return str(result)[:1000]
    except Exception:
        return {}