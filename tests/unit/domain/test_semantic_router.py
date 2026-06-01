# tests/unit/domain/test_semantic_router.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.domain.services.semantic_router import SemanticRouterService, SemanticRouteResult

@pytest.mark.asyncio
async def test_roteamento_fast_path_greetings():
    # Fast path de saudação deve responder sem fazer chamadas ao Redis ou LLM
    mock_redis = AsyncMock()
    mock_emb = MagicMock()
    
    service = SemanticRouterService(async_redis=mock_redis, embeddings_model=mock_emb)
    resultado = await service.rotear("Bom dia")
    
    assert resultado.node == "greeting_node"
    assert resultado.intent == "intent_greeting"
    assert resultado.confianca == "alta"
    assert resultado.score == 1.0
    mock_redis.ft.assert_not_called()
    mock_emb.embed_query.assert_not_called()

@pytest.mark.asyncio
async def test_roteamento_knn_sucesso():
    # KNN bem sucedido deve retornar rota correspondente
    mock_redis = MagicMock()
    mock_emb = MagicMock()
    mock_emb.embed_query.return_value = [0.1] * 3072  # 3072 dimension representation
    
    # Mock docs do Redis
    mock_doc = MagicMock()
    mock_doc.name = "consultar_edital_paes_2026"
    mock_doc.knn_score = "0.1"  # Distância coseno
    
    mock_search_results = MagicMock()
    mock_search_results.docs = [mock_doc]
    
    mock_ft = MagicMock()
    mock_ft.search = AsyncMock(return_value=mock_search_results)
    mock_redis.ft.return_value = mock_ft
    
    service = SemanticRouterService(async_redis=mock_redis, embeddings_model=mock_emb)
    resultado = await service.rotear("quais vagas tem no PAES?")
    
    assert resultado.node == "retrieve_node"
    assert resultado.intent == "consultar_edital_paes_2026"
    assert resultado.confianca == "alta"  # similarity = 1.0 - 0.1 = 0.9 >= 0.82
    assert resultado.score == 0.9

@pytest.mark.asyncio
async def test_roteamento_fallback_erro():
    # Quando o Redis ou Embeddings falhar, deve retornar fallback do regex sem quebrar
    mock_redis = MagicMock()
    mock_emb = MagicMock()
    mock_emb.embed_query.side_effect = RuntimeError("Erro de rede")
    
    service = SemanticRouterService(async_redis=mock_redis, embeddings_model=mock_emb)
    resultado = await service.rotear("quais as vagas do vestibular paes?")
    
    assert resultado.node == "retrieve_node"
    assert resultado.intent == "consultar_edital_paes_2026"
    assert resultado.confianca == "baixa"
    assert resultado.score == 0.0