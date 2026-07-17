# tests/unit/application/test_semantic_router_app.py
import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from src.application.routing.semantic_router import (
    _regex_rapido,
    _heuristica_basica,
    _classificar_com_flash,
    rotear,
    RoutingDecision,
    RouterDecision,
)

def test_regex_rapido_greetings():
    assert _regex_rapido("oi") == "GREETING"
    assert _regex_rapido("olá!") == "GREETING"
    assert _regex_rapido("bom dia") == "GREETING"
    assert _regex_rapido("como funciona a matrícula?") is None

def test_regex_rapido_media_download():
    assert _regex_rapido("https://youtube.com/watch?v=abc123xyz") == "MEDIA_DOWNLOAD"
    assert _regex_rapido("https://instagram.com/reel/abc123xyz/") == "MEDIA_DOWNLOAD"

def test_heuristica_basica():
    assert _heuristica_basica("senha sigaa uema") == "WIKI"
    assert _heuristica_basica("qual o calendário?") == "CALENDARIO"
    assert _heuristica_basica("quem é o reitor?") is None

@pytest.mark.asyncio
async def test_classificar_com_flash_sucesso():
    # Mock do cliente google-genai
    mock_response = MagicMock()
    mock_response.text = '{"rota": "EDITAL", "confianca": 0.95, "motivo": "pergunta sobre vestibular paes"}'
    mock_response.usage_metadata = MagicMock(prompt_token_count=20, candidates_token_count=10)
    
    mock_generate = AsyncMock(return_value=mock_response)
    mock_aio = MagicMock(models=MagicMock(generate_content=mock_generate))
    
    with patch("google.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client.aio = mock_aio
        mock_client_class.return_value = mock_client
        
        with patch("src.infrastructure.settings.settings.GEMINI_API_KEY", "fake_key"):
            decision = await _classificar_com_flash("qual a cota para pcd no paes?", {})
            
            assert decision.rota == "EDITAL"
            assert decision.confianca == 0.95
            assert decision.motivo == "pergunta sobre vestibular paes"
            assert decision.cache_hit is False

@pytest.mark.asyncio
async def test_classificar_com_flash_json_invalido_fallback():
    # Mock do cliente retornando JSON inválido
    mock_response = MagicMock()
    mock_response.text = "esta rota é edital com certeza!"
    mock_response.usage_metadata = MagicMock(prompt_token_count=20, candidates_token_count=10)
    
    mock_generate = AsyncMock(return_value=mock_response)
    mock_aio = MagicMock(models=MagicMock(generate_content=mock_generate))
    
    with patch("google.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client.aio = mock_aio
        mock_client_class.return_value = mock_client
        
        with patch("src.infrastructure.settings.settings.GEMINI_API_KEY", "fake_key"):
            decision = await _classificar_com_flash("qual a cota para pcd no paes?", {})
            
            # Deve disparar fallback e mapear baseado no regex
            assert decision.rota == "EDITAL"
            assert decision.confianca == 0.4
            assert "regex_fallback" in decision.motivo


@pytest.mark.asyncio
async def test_rotear_l3_seeded_regex_sucesso():
    # Setup mocks
    mock_redis_text = MagicMock()
    # L3 Regex em cache do Redis
    mock_redis_text.hgetall.return_value = {"PROVA_VESTIBULAR": "prova.*vestibular"}
    mock_redis_text.hget.return_value = '{"doc_type": "edital", "k_vector": 5, "k_text": 7}'

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis_text):
        decision = await rotear("quero saber sobre a prova do vestibular paes", "session-1")
        
        assert decision.rota == "PROVA_VESTIBULAR"
        assert decision.confianca == 0.95
        assert decision.cache_hit is True
        assert decision.cache_layer == "regex"
        assert decision.dag_hint["doc_type"] == "edital"
        assert decision.dag_hint["k_vector"] == 5


@pytest.mark.asyncio
async def test_rotear_l4_seeded_knn_sucesso():
    # Setup mocks: L3 não casa, L4 KNN casa com similaridade >= 0.82
    mock_redis_text = MagicMock()
    mock_redis_text.hgetall.return_value = {}  # Sem regex matching
    mock_redis_text.hget.return_value = '{"doc_type": "calendario", "k_vector": 6, "k_text": 6}'

    mock_redis_bytes = MagicMock()
    mock_doc = MagicMock()
    mock_doc.name = "DATES_CALENDAR"
    mock_doc.score = 0.1  # Distância coseno: 0.1, similaridade = 0.90
    mock_results = MagicMock()
    mock_results.docs = [mock_doc]
    
    mock_ft = MagicMock()
    mock_ft.search.return_value = mock_results
    mock_redis_bytes.ft.return_value = mock_ft

    mock_emb = MagicMock()
    mock_emb.embed_query.return_value = [0.1] * 3072

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis_text), \
         patch("src.infrastructure.redis_client.get_redis", return_value=mock_redis_bytes), \
         patch("src.rag.embeddings.get_embeddings", return_value=mock_emb):
         
        decision = await rotear("quando começam as aulas?", "session-1")
        
        assert decision.rota == "DATES_CALENDAR"
        assert decision.confianca == 0.81  # 0.90 * 0.90 = 0.81
        assert decision.cache_hit is True
        assert decision.cache_layer == "semantic"
        assert decision.dag_hint["doc_type"] == "calendario"


@pytest.mark.asyncio
async def test_rotear_l5_fallback_flash():
    # Setup mocks: L3 não casa, L4 KNN score é baixo (< 0.82), cai no L5 Flash
    mock_redis_text = MagicMock()
    mock_redis_text.hgetall.return_value = {}
    
    mock_redis_bytes = MagicMock()
    mock_doc = MagicMock()
    mock_doc.name = "DATES_CALENDAR"
    mock_doc.score = 0.3  # Similaridade = 0.70 < 0.82 threshold
    mock_results = MagicMock()
    mock_results.docs = [mock_doc]
    
    mock_ft = MagicMock()
    mock_ft.search.return_value = mock_results
    mock_redis_bytes.ft.return_value = mock_ft

    mock_emb = MagicMock()
    mock_emb.embed_query.return_value = [0.1] * 3072

    # Mock do Gemini Flash
    mock_response = MagicMock()
    mock_response.text = '{"rota": "EDITAL", "confianca": 0.90, "motivo": "paes info"}'
    mock_response.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=5)
    mock_generate = AsyncMock(return_value=mock_response)
    mock_aio = MagicMock(models=MagicMock(generate_content=mock_generate))

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis_text), \
         patch("src.infrastructure.redis_client.get_redis", return_value=mock_redis_bytes), \
         patch("src.rag.embeddings.get_embeddings", return_value=mock_emb), \
         patch("google.genai.Client") as mock_client_class:
         
        mock_client = MagicMock()
        mock_client.aio = mock_aio
        mock_client_class.return_value = mock_client
        
        with patch("src.infrastructure.settings.settings.GEMINI_API_KEY", "fake_key"):
            decision = await rotear("qualquer query sem matches locais", "session-1")
            
            assert decision.rota == "EDITAL"
            assert decision.confianca == 0.90
            assert decision.cache_hit is False
            assert decision.cache_layer == "miss"

