"""
infrastructure/observability/langfuse_client.py — Tracing LLM (Langfuse V2)
=============================================================================

COMPATIBILIDADE: langfuse==2.53.9 (V2)

REGRAS V2:
  - CallbackHandler() é instanciado SEM argumentos de cliente/chaves.
  - Ele lê automaticamente: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
    das variáveis de ambiente do SO/Docker.
  - Se o servidor Langfuse estiver offline, o pipeline RAG NÃO é interrompido.
    O erro é apenas logado via logger.error.

CONFIGURAÇÃO (.env / docker-compose.yml):
  LANGFUSE_SECRET_KEY=sk-lf-...
  LANGFUSE_PUBLIC_KEY=pk-lf-...
  LANGFUSE_HOST=http://langfuse:3000
"""
from __future__ import annotations

import functools
import logging
from contextlib import contextmanager
from typing import Any, Callable, Generator

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Singleton do cliente Langfuse (lazy init)
# ─────────────────────────────────────────────────────────────────────────────

_lf_instance = None


def _get_langfuse():
    """
    Retorna o singleton Langfuse ou None se indisponível.
    Nunca lança exceção — falha silenciosa com log.
    """
    global _lf_instance
    if _lf_instance is not None:
        return _lf_instance
    try:
        from src.infrastructure.settings import settings
        if not getattr(settings, "LANGFUSE_SECRET_KEY", None) or \
           not getattr(settings, "LANGFUSE_PUBLIC_KEY", None):
            logger.debug("Langfuse desabilitado: chaves não configuradas.")
            return None

        from langfuse import Langfuse
        _lf_instance = Langfuse(
            secret_key=settings.LANGFUSE_SECRET_KEY,
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            host=getattr(settings, "LANGFUSE_HOST", "http://langfuse:3000"),
            flush_at=1,
            flush_interval=0.5,
        )
        logger.info("✅ Langfuse client inicializado (V2).")
    except Exception as e:
        logger.warning("Langfuse indisponível: %s — tracing desativado.", e)
        _lf_instance = None
    return _lf_instance


# ─────────────────────────────────────────────────────────────────────────────
# CallbackHandler para LangChain — V2 COMPLIANT
# ─────────────────────────────────────────────────────────────────────────────

def get_langfuse_handler(session_id: str = "", user_id: str = ""):
    """
    Retorna o LangChain CallbackHandler compatível com Langfuse V2.

    Na V2, o CallbackHandler NÃO aceita `client`, `secret_key` ou `public_key`
    no construtor. Ele lê LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY e
    LANGFUSE_HOST diretamente das variáveis de ambiente.

    Retorna None se Langfuse estiver offline — o pipeline continua normalmente.
    """
    try:
        from langfuse.langchain import CallbackHandler  # langfuse >= 2.x

        # ✅ V2: instanciado SEM secret_key/public_key/client
        handler = CallbackHandler(
            session_id=session_id or None,
            user_id=user_id or None,
            metadata={"framework": "oraculo_uema_v5"},
        )
        return handler

    except ImportError:
        logger.warning("langfuse não instalado — sem tracing LangChain.")
        return None
    except Exception as e:
        logger.error("Langfuse CallbackHandler falhou: %s — pipeline continua.", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Score de qualidade (ex: CRAG score, token_cost)
# ─────────────────────────────────────────────────────────────────────────────

def langfuse_score(trace_id: str, name: str, value: float, comment: str = "") -> None:
    """
    Registra um score no Langfuse. NO-OP se offline.
    Nunca interrompe o pipeline.
    """
    lf = _get_langfuse()
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
        # ✅ Erro logado, pipeline NÃO interrompido
        logger.error("Langfuse score falhou (score=%s, value=%s): %s", name, value, e)


def safe_score_from_handler(handler, name: str, value: float, comment: str = "") -> None:
    """
    Registra score usando o handler do LangChain de forma resiliente.
    Lida com a API interna da V2 sem quebrar se offline.
    """
    if handler is None:
        return
    try:
        trace_id = handler.get_trace_id()
        if trace_id:
            handler.langfuse.score(
                trace_id=trace_id,
                name=name,
                value=round(value, 6),
                comment=comment,
            )
    except Exception as e:
        # ✅ Nunca propaga — apenas loga
        logger.error("Langfuse handler.score falhou (%s=%s): %s — ignorado.", name, value, e)


def safe_flush_handler(handler) -> None:
    """Faz flush do handler de forma resiliente. NO-OP se None ou offline."""
    if handler is None:
        return
    try:
        handler.langfuse.flush()
    except Exception as e:
        logger.error("Langfuse flush falhou: %s — ignorado.", e)


# ─────────────────────────────────────────────────────────────────────────────
# Flush global no shutdown do FastAPI
# ─────────────────────────────────────────────────────────────────────────────

def flush_langfuse() -> None:
    """
    Chame no shutdown do FastAPI:
        @app.on_event("shutdown")
        async def shutdown():
            flush_langfuse()
    """
    lf = _get_langfuse()
    if lf:
        try:
            lf.flush()
            logger.info("✅ Langfuse: spans flushed com sucesso.")
        except Exception as e:
            logger.warning("Langfuse flush falhou: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Decorator @observe_llm
# ─────────────────────────────────────────────────────────────────────────────

def observe_llm(name: str = "", capture_input: bool = True, capture_output: bool = True):
    """
    Decorator que cria um span Langfuse para funções que chamam LLM.
    NO-OP se Langfuse estiver offline.

    Uso:
        @observe_llm(name="gemini_rag_generation")
        async def gerar_resposta(prompt: str) -> str: ...
    """
    def decorator(func: Callable) -> Callable:
        span_name = name or func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            lf = _get_langfuse()
            if lf is None:
                return await func(*args, **kwargs)
            trace = generation = None
            try:
                trace = lf.trace(name=span_name)
                generation = trace.generation(
                    name=span_name,
                    input=_safe_input(args, kwargs) if capture_input else None,
                )
            except Exception as e:
                logger.error("Langfuse trace init falhou: %s", e)

            try:
                result = await func(*args, **kwargs)
                if generation:
                    try:
                        generation.end(output=_safe_output(result) if capture_output else None)
                    except Exception as e:
                        logger.error("Langfuse generation.end falhou: %s", e)
                return result
            except Exception as exc:
                if generation:
                    try:
                        generation.end(level="ERROR", status_message=str(exc)[:300])
                    except Exception:
                        pass
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            lf = _get_langfuse()
            if lf is None:
                return func(*args, **kwargs)
            generation = None
            try:
                trace = lf.trace(name=span_name)
                generation = trace.generation(
                    name=span_name,
                    input=_safe_input(args, kwargs) if capture_input else None,
                )
            except Exception as e:
                logger.error("Langfuse trace init falhou: %s", e)
            try:
                result = func(*args, **kwargs)
                if generation:
                    try:
                        generation.end(output=_safe_output(result) if capture_output else None)
                    except Exception as e:
                        logger.error("Langfuse generation.end falhou: %s", e)
                return result
            except Exception as exc:
                if generation:
                    try:
                        generation.end(level="ERROR", status_message=str(exc)[:300])
                    except Exception:
                        pass
                raise

        import asyncio
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Context manager para spans customizados
# ─────────────────────────────────────────────────────────────────────────────

class _NoOpSpan:
    def update(self, **kwargs): pass
    def end(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


@contextmanager
def langfuse_span(name: str, input: Any = None, metadata: dict | None = None):
    """
    Context manager para spans customizados. NO-OP se offline.

    Uso:
        with langfuse_span("vector_search", input={"query": q}) as span:
            resultados = busca_hibrida(query)
            span.update(output={"n_chunks": len(resultados)})
    """
    lf = _get_langfuse()
    if lf is None:
        yield _NoOpSpan()
        return

    span = None
    try:
        trace = lf.trace(name=name)
        span = trace.span(name=name, input=input, metadata=metadata)
    except Exception as e:
        logger.error("Langfuse span init falhou: %s", e)
        yield _NoOpSpan()
        return

    try:
        yield span
        try:
            span.end()
        except Exception as e:
            logger.error("Langfuse span.end falhou: %s", e)
    except Exception as exc:
        try:
            span.end(level="ERROR", status_message=str(exc)[:300])
        except Exception:
            pass
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _safe_input(args: tuple, kwargs: dict) -> dict:
    try:
        return {
            "args":   [str(a)[:500] for a in args if not callable(a)],
            "kwargs": {k: str(v)[:500] for k, v in kwargs.items()},
        }
    except Exception:
        return {}


def _safe_output(result: Any) -> Any:
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