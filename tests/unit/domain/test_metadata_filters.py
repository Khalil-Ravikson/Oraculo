# tests/unit/domain/test_metadata_filters.py
import pytest
from unittest.mock import MagicMock, patch
from src.infrastructure.redis_client import busca_hibrida

def test_busca_hibrida_compila_filtros_corretamente():
    # Mock do Redis
    mock_redis = MagicMock()
    mock_search_results = MagicMock()
    mock_search_results.docs = []
    
    mock_ft = MagicMock()
    mock_ft.search.return_value = mock_search_results
    mock_redis.ft.return_value = mock_ft
    
    # Mock get_redis para retornar o mock_redis
    with patch("src.infrastructure.redis_client.get_redis", return_value=mock_redis):
        # Caso 1: Sem filtros
        busca_hibrida(
            query_text="teste query",
            query_embedding=[0.1, 0.2, 0.3],
            k_vector=3,
            k_text=3,
        )
        
        # O search deve ser chamado com Query contendo "*" na busca vetorial
        calls = mock_ft.search.call_args_list
        assert len(calls) >= 2
        
        # Primeiro argumento de ft().search é a Query
        vec_query = calls[0][0][0]
        txt_query = calls[1][0][0]
        
        assert vec_query.query_string() == "*=>[KNN 3 @embedding $vec AS vec_score]"
        # No texto, deve ser a query escapada.
        # "teste query" splitado e sem stop words: termos > 2 caracteres
        # "teste" e "query" são mantidos, logo "teste | query"
        assert txt_query.query_string() == "teste | query"

def test_busca_hibrida_com_source_filter():
    mock_redis = MagicMock()
    mock_search_results = MagicMock()
    mock_search_results.docs = []
    mock_ft = MagicMock()
    mock_ft.search.return_value = mock_search_results
    mock_redis.ft.return_value = mock_ft
    
    with patch("src.infrastructure.redis_client.get_redis", return_value=mock_redis):
        busca_hibrida(
            query_text="matrícula",
            query_embedding=[0.1, 0.2, 0.3],
            source_filter="edital-2026.pdf",
            k_vector=5,
            k_text=5,
        )
        
        calls = mock_ft.search.call_args_list
        vec_query = calls[0][0][0]
        txt_query = calls[1][0][0]
        
        # Filtro de source escapado (edital\-2026.pdf) -> edital\-2026\.pdf
        expected_filter = "(@source:{edital\\-2026\\.pdf})"
        assert vec_query.query_string() == f"{expected_filter}=>[KNN 5 @embedding $vec AS vec_score]"
        assert txt_query.query_string() == f"{expected_filter} (matrícula)"

def test_busca_hibrida_com_metadata_filters_single_and_list():
    mock_redis = MagicMock()
    mock_search_results = MagicMock()
    mock_search_results.docs = []
    mock_ft = MagicMock()
    mock_ft.search.return_value = mock_search_results
    mock_redis.ft.return_value = mock_ft
    
    with patch("src.infrastructure.redis_client.get_redis", return_value=mock_redis):
        metadata = {
            "campus": "sao-luis",
            "ano": ["2025", "2026"],
            "eixo": "ensino"
        }
        busca_hibrida(
            query_text="calendário",
            query_embedding=[0.1],
            metadata_filter=metadata,
            k_vector=4,
            k_text=4,
        )
        
        calls = mock_ft.search.call_args_list
        vec_query = calls[0][0][0]
        txt_query = calls[1][0][0]
        
        q_vec = vec_query.query_string()
        q_txt = txt_query.query_string()
        
        # A ordem das chaves do dicionário pode variar, então verificamos se todas as partes estão lá
        assert "@campus:{sao\\-luis}" in q_vec
        assert "@ano:{2025|2026}" in q_vec
        assert "@eixo:{ensino}" in q_vec
        assert "=>[KNN 4 @embedding $vec AS vec_score]" in q_vec
        
        assert "@campus:{sao\\-luis}" in q_txt
        assert "@ano:{2025|2026}" in q_txt
        assert "@eixo:{ensino}" in q_txt
        assert "(calendário)" in q_txt
