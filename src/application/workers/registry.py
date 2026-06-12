"""
WorkerRegistry — Único ponto de despacho de workers.
Implementa Autodiscovery: lê a pasta e auto-registra os workers sem imports manuais.
"""
from __future__ import annotations

import logging
import importlib
import pkgutil
from typing import Callable

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable] = {}
_WORKERS_LOADED: bool = False  # Flag para garantir que só lemos a pasta 1 vez

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
    "greeting":         "celery",
    "crud_confirm":     "default",
    "sigaa_biblioteca": "default",
    "sigaa_extensao":   "default",
    "sigaa_processos":  "default",
    "sigaa_notas":      "default",
    "sigaa_indice":     "default",
    "sigaa_historico":  "default",
    "sigaa_estrutura":  "default",
    "sigaa_turmas":     "default",
    "sigaa_calendario": "default",
}


def register(name: str):
    """Decorator que registra um Celery task no registry."""
    def decorator(fn: Callable):
        _REGISTRY[name] = fn
        logger.debug("✅ [REGISTRY] Worker registrado: '%s'", name)
        return fn
    return decorator


def _autodiscover_workers():
    """
    Escaneia a pasta src/application/workers e importa automaticamente 
    todos os arquivos que começam com 'worker_'.
    Isso ativa todos os decoradores @register automaticamente.
    """
    global _WORKERS_LOADED
    if _WORKERS_LOADED:
        return

    import src.application.workers as workers_pkg

    # Percorre todos os arquivos dentro da pasta workers
    for _, module_name, is_pkg in pkgutil.iter_modules(workers_pkg.__path__):
        if not is_pkg and module_name.startswith("worker_"):
            full_module_name = f"src.application.workers.{module_name}"
            try:
                importlib.import_module(full_module_name)
            except Exception as e:
                logger.error("❌ [REGISTRY] Falha ao auto-importar %s: %s", full_module_name, e)

    _WORKERS_LOADED = True


def dispatch(worker_name: str, event: dict) -> str | None:
    """
    Despacha para o worker registrado.
    Retorna task_id ou None se worker não existir.
    """
    # 1. Garante que os workers foram descobertos (Roda apenas na 1ª vez)
    _autodiscover_workers()

    # 2. Busca o worker e despacha
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
    _autodiscover_workers()
    return list(_REGISTRY.keys())