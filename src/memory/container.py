"""
src/memory/container.py — Container de DI para o sistema de memória
====================================================================
CORREÇÃO: remove importação circular de gemini_provider.py.
O adaptador Gemini agora é importado de infrastructure/adapters/gemini_provider.py
onde vive a classe GeminiProvider REAL.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_memory_service_instance: Any | None = None


def create_memory_service(
    redis_client:    Any = None,
    embedding_model: Any = None,
) -> Any:
    """
    Fábrica singleton do MemoryService.
    Primeira chamada cria e guarda a instância.
    """
    global _memory_service_instance
    if _memory_service_instance is not None:
        return _memory_service_instance

    from src.memory.adapters.redis_working_memory  import RedisWorkingMemory
    from src.memory.adapters.redis_long_term_memory import RedisLongTermMemory
    from src.memory.adapters.redis_menu_state       import RedisMenuStateRepository
    from src.memory.adapters.llm_fact_extractor     import (
        CompositeFactExtractor,
        RegexFactExtractor,
        LLMFactExtractor,
    )
    from src.memory.services.memory_service import MemoryService

    if redis_client is None:
        from src.infrastructure.redis_client import get_redis_text
        redis_client = get_redis_text()

    if embedding_model is None:
        from src.rag.embeddings import get_embeddings
        embedding_model = get_embeddings()

    working   = RedisWorkingMemory(redis_client)
    long_term = RedisLongTermMemory(redis_client, embedding_model)
    menu      = RedisMenuStateRepository(redis_client)

    extractors = [RegexFactExtractor()]

    try:
        # ── CORREÇÃO: importa GeminiProvider do lugar correto ────────────────
        # ANTES (circular): from src.infrastructure.adapters.gemini_provider import GeminiProvider
        # DEPOIS (correto): importa a classe real que agora existe nesse arquivo
        from src.infrastructure.adapters.gemini_provider import GeminiProvider

        llm_extractor = LLMFactExtractor(
            llm_provider = _GeminiAdapterForExtractor(GeminiProvider()),
            redis_client = redis_client,
        )
        extractors.append(llm_extractor)
        logger.debug("✅ [MEMORY] LLMFactExtractor (Gemini) disponível.")
    except Exception as exc:
        logger.warning("⚠️  [MEMORY] LLMFactExtractor indisponível: %s", exc)

    extractor = CompositeFactExtractor(extractors)

    _memory_service_instance = MemoryService(
        working        = working,
        long_term      = long_term,
        menu_state     = menu,
        fact_extractor = extractor,
    )

    logger.info("✅ [MEMORY] MemoryService criado.")
    return _memory_service_instance


def reset_memory_service() -> None:
    """Reseta o singleton — útil em testes."""
    global _memory_service_instance
    _memory_service_instance = None


class _GeminiAdapterForExtractor:
    """
    Adapter mínimo que expõe a interface esperada pelo LLMFactExtractor.
    Delega para GeminiProvider sem criar nova instância a cada chamada.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def gerar_resposta_estruturada_async(
        self,
        prompt:          str,
        response_schema: Any,
        temperatura:     float = 0.05,
        **kwargs,
    ) -> Any | None:
        return await self._provider.gerar_resposta_estruturada_async(
            prompt          = prompt,
            response_schema = response_schema,
            temperatura     = temperatura,
        )