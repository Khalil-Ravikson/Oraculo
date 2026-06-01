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
