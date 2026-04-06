"""
src/memory/container.py
-------------------------
Container de injeção de dependência para o sistema de memória.

USO NO GRAPH:
    from src.memory.container import create_memory_service
    memory = create_memory_service()  # singleton por processo

    # Nos nodes do LangGraph:
    ctx = memory.carregar_contexto(user_id, session_id, query)
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def create_memory_service(redis_client: Any = None, embedding_model: Any = None):
    """
    Fábrica singleton do MemoryService.
    Cria todos os adapters e os injeta no serviço.
    """
    from src.memory.adapters.redis_working_memory import RedisWorkingMemory
    from src.memory.adapters.redis_long_term_memory import RedisLongTermMemory
    from src.memory.adapters.redis_menu_state import RedisMenuStateRepository
    from src.memory.adapters.llm_fact_extractor import (
        CompositeFactExtractor,
        LLMFactExtractor,
        RegexFactExtractor,
    )
    from src.memory.services.memory_service import MemoryService

    if redis_client is None:
        from src.infrastructure.redis_client import get_redis_text
        redis_client = get_redis_text()

    if embedding_model is None:
        from src.rag.embeddings import get_embeddings
        embedding_model = get_embeddings()

    working = RedisWorkingMemory(redis_client)
    long_term = RedisLongTermMemory(redis_client, embedding_model)
    menu_state = RedisMenuStateRepository(redis_client)

    # Extrator composto: regex (0 tokens) + LLM (quando disponível)
    try:
        from src.providers.gemini_provider import get_gemini_client
        llm_extractor = LLMFactExtractor(
            llm_provider=_GeminiProviderAdapter(),
            redis_client=redis_client,
        )
        extractor = CompositeFactExtractor([RegexFactExtractor(), llm_extractor])
    except Exception:
        extractor = CompositeFactExtractor([RegexFactExtractor()])

    return MemoryService(
        working=working,
        long_term=long_term,
        menu_state=menu_state,
        fact_extractor=extractor,
    )


class _GeminiProviderAdapter:
    """Adapter mínimo para o LLMFactExtractor usar o Gemini existente."""
    async def gerar_resposta_estruturada_async(self, prompt, response_schema, **kwargs):
        from src.providers.gemini_provider import chamar_gemini_async
        resp = await chamar_gemini_async(prompt=prompt, response_schema=response_schema, **kwargs)
        if resp.sucesso and resp.conteudo:
            import json
            try:
                data = json.loads(resp.conteudo)
                return response_schema(**data)
            except Exception:
                return None
        return None