"""
src/memory/container.py — v2 (adapter Gemini correto)
=====================================================

CORREÇÕES vs v1:
  - _GeminiProviderAdapter chamava `chamar_gemini_async` (não existe).
    Agora usa GeminiProvider.gerar_resposta_estruturada_async() directamente.
  - lru_cache com parâmetros hashable (redis_client e embedding_model
    não são hashable — o cache agora é manual via módulo singleton).
  - Tratamento de erro robusto no extractor composto.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Singleton do MemoryService — iniciado uma vez por processo
_memory_service_instance: Optional[Any] = None


def create_memory_service(
    redis_client: Any = None,
    embedding_model: Any = None,
) -> Any:
    """
    Fábrica singleton do MemoryService.

    Primeira chamada cria e guarda a instância.
    Chamadas subsequentes retornam a mesma instância.

    Args:
        redis_client:    Cliente Redis (decode_responses=True).
                         Se None, usa get_redis_text().
        embedding_model: Modelo de embeddings.
                         Se None, usa get_embeddings().
    """
    global _memory_service_instance
    if _memory_service_instance is not None:
        return _memory_service_instance

    from src.memory.adapters.redis_working_memory import RedisWorkingMemory
    from src.memory.adapters.redis_long_term_memory import RedisLongTermMemory
    from src.memory.adapters.redis_menu_state import RedisMenuStateRepository
    from src.memory.adapters.llm_fact_extractor import (
        CompositeFactExtractor,
        RegexFactExtractor,
        LLMFactExtractor,
    )
    from src.memory.services.memory_service import MemoryService

    # Resolve dependências se não injectadas
    if redis_client is None:
        from src.infrastructure.redis_client import get_redis_text
        redis_client = get_redis_text()

    if embedding_model is None:
        from src.rag.embeddings import get_embeddings
        embedding_model = get_embeddings()

    # Adapters de memória
    working   = RedisWorkingMemory(redis_client)
    long_term = RedisLongTermMemory(redis_client, embedding_model)
    menu      = RedisMenuStateRepository(redis_client)

    # Extractor de fatos: regex (0 tokens) + LLM (quando disponível)
    extractors = [RegexFactExtractor()]

    try:
        llm_extractor = LLMFactExtractor(
            llm_provider = _GeminiProviderAdapter(),
            redis_client = redis_client,
        )
        extractors.append(llm_extractor)
        logger.debug("✅ [MEMORY] LLMFactExtractor (Gemini) disponível.")
    except Exception as exc:
        logger.warning(
            "⚠️  [MEMORY] LLMFactExtractor indisponível, usando apenas regex: %s", exc
        )

    extractor = CompositeFactExtractor(extractors)

    _memory_service_instance = MemoryService(
        working       = working,
        long_term     = long_term,
        menu_state    = menu,
        fact_extractor= extractor,
    )

    logger.info("✅ [MEMORY] MemoryService criado.")
    return _memory_service_instance


def reset_memory_service() -> None:
    """Reseta o singleton (útil em testes)."""
    global _memory_service_instance
    _memory_service_instance = None


# ─────────────────────────────────────────────────────────────────────────────
# Adapter do Gemini para o LLMFactExtractor
# ─────────────────────────────────────────────────────────────────────────────

class _GeminiProviderAdapter:
    """
    Adapter mínimo que expõe a interface esperada pelo LLMFactExtractor.

    LLMFactExtractor chama:
        await provider.gerar_resposta_estruturada_async(
            prompt=...,
            response_schema=PydanticModel,
            temperatura=0.05,
        )

    GeminiProvider (src/infrastructure/adapters/gemini_provider.py) já tem
    este método — só precisamos de uma referência lazy para evitar import circular.
    """

    async def gerar_resposta_estruturada_async(
        self,
        prompt: str,
        response_schema: Any,
        temperatura: float = 0.05,
        **kwargs,
    ) -> Any | None:
        """
        Delega para GeminiProvider.gerar_resposta_estruturada_async().
        Import lazy evita circular dependency entre memory e infrastructure.
        """
        try:
            from src.infrastructure.adapters.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            return await provider.gerar_resposta_estruturada_async(
                prompt          = prompt,
                response_schema = response_schema,
                temperatura     = temperatura,
            )
        except Exception as exc:
            logger.warning(
                "⚠️  [MEMORY:GEMINI] gerar_resposta_estruturada_async falhou: %s", exc
            )
            return None