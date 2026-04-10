import pytest
from unittest.mock import patch, MagicMock
from src.domain.services.semantic_router import _busca_tool_semantica, Rota
from src.domain.entities import EstadoMenu

@patch('src.domain.semantic_router.get_redis')
@patch('src.domain.semantic_router.get_embeddings')
def test_roteamento_deve_retornar_edital_com_alta_confianca(mock_get_embeddings, mock_get_redis):
    # 1. Mock do Redis simulando um match perfeito (score de distância 0.1 -> similaridade 0.9)
    mock_redis = MagicMock()
    mock_doc = MagicMock()
    mock_doc.name = "consultar_edital_paes_2026"
    mock_doc.score = "0.1" # Distância no Redis
    
    mock_resultados = MagicMock()
    mock_resultados.docs = [mock_doc]
    mock_redis.ft().search.return_value = mock_resultados
    mock_get_redis.return_value = mock_redis

    # 2. Execução da função do teu router
    resultado = _busca_tool_semantica("quantas vagas tem?")

    # 3. Validação da lógica (Threshold > 0.80 = ALTA)
    assert resultado is not None
    assert resultado.rota == Rota.EDITAL
    assert resultado.confianca == "alta"