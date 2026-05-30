"""
WorkerRegistry — Único ponto de despacho de workers.
O CognitiveOS só conhece nomes, não implementações.
"""
from __future__ import annotations
import logging
from typing import Callable

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable] = {}

_QUEUES: dict[str, str] = {
    "rag_search":       "rag_search",
    "synthesis":        "synthesis",
    "reranker":         "rag_search",
    "graph_extractor":  "graph",
    "memory_manager":   "default",
    "db_connector":     "default",
    "action":           "default",
    "audio_to_text":    "media",
    "text_to_audio":    "media",
    "ytb_download":     "media",
    "insta_download":   "media",
    "greeting":         "default",
    "crud_confirm":     "default",
}


def register(name: str):
    """Decorator que registra um Celery task no registry."""
    def decorator(fn: Callable):
        _REGISTRY[name] = fn
        logger.debug("✅ [REGISTRY] Worker registrado: '%s'", name)
        return fn
    return decorator


def dispatch(worker_name: str, event: dict) -> str | None:
    """
    Despacha para o worker registrado.
    Retorna task_id ou None se worker não existir.
    """
    fn = _REGISTRY.get(worker_name)
    if fn is None:
        logger.error(
            "❌ [REGISTRY] Worker '%s' não registrado. Disponíveis: %s",
            worker_name, list(_REGISTRY.keys())
        )
        return None
    queue = _QUEUES.get(worker_name, "default")
    result = fn.apply_async(args=[event], queue=queue)
    logger.debug("📤 [REGISTRY] Despachado '%s' → queue='%s' task=%s",
                 worker_name, queue, result.id[:8])
    return result.id


def available() -> list[str]:
    return list(_REGISTRY.keys())