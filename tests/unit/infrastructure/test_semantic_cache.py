# tests/unit/infrastructure/test_semantic_cache.py
"""
Fase 3.5 (fusão de contexto no LangGraph + cache): cobre a resolução de
TTL/threshold por rota, a exclusão de rotas não-cacheáveis, e as funções de
gerenciamento (invalidar_cache_rota/cache_stats) que antes eram importadas
em admin_api.py/admin_commands.py sem existirem em lugar nenhum.
"""
import pytest
from unittest.mock import MagicMock, patch

from src.infrastructure.semantic_cache import (
    SemanticCache,
    TTL_POR_ROTA,
    THRESHOLD_POR_ROTA,
    invalidar_cache_rota,
    cache_stats,
)


def _mock_redis_emb(embed_return=None):
    mock_redis = MagicMock()
    mock_emb = MagicMock()
    mock_emb.embed_query.return_value = embed_return or [1.0, 0.0, 0.0]
    return mock_redis, mock_emb


def _entrada_score_090():
    # embedding unitário com cosine=0.9 exato contra [1.0, 0.0]
    return {
        b"embedding": b'[0.9, 0.4358898943540674]',
        b"response": b'{"answer": "resposta cacheada"}',
    }


@pytest.mark.asyncio
async def test_get_hit_quando_score_acima_do_threshold_da_rota():
    """WIKI tem threshold 0.88 — uma entrada com score 0.90 bate."""
    mock_redis, mock_emb = _mock_redis_emb(embed_return=[1.0, 0.0])
    mock_redis.scan.side_effect = [(0, ["semcache:WIKI:x"])]
    mock_redis.hgetall.return_value = _entrada_score_090()

    with patch("src.infrastructure.semantic_cache.get_redis_text", return_value=mock_redis), \
         patch("src.infrastructure.semantic_cache.get_embeddings", return_value=mock_emb):
        resultado = await SemanticCache().get(query="x", rota="WIKI")

    assert THRESHOLD_POR_ROTA["WIKI"] == 0.88
    assert resultado is not None
    assert resultado["answer"] == "resposta cacheada"


@pytest.mark.asyncio
async def test_get_miss_quando_score_abaixo_do_threshold_mais_rigido():
    """CONTATOS tem threshold 0.93 (mais rígido que WIKI) — a MESMA entrada
    com score 0.90 não bate."""
    mock_redis, mock_emb = _mock_redis_emb(embed_return=[1.0, 0.0])
    mock_redis.scan.side_effect = [(0, ["semcache:CONTATOS:x"])]
    mock_redis.hgetall.return_value = _entrada_score_090()

    with patch("src.infrastructure.semantic_cache.get_redis_text", return_value=mock_redis), \
         patch("src.infrastructure.semantic_cache.get_embeddings", return_value=mock_emb):
        resultado = await SemanticCache().get(query="x", rota="CONTATOS")

    assert THRESHOLD_POR_ROTA["CONTATOS"] == 0.93
    assert resultado is None


@pytest.mark.asyncio
async def test_get_nunca_consulta_rota_sem_cache():
    """SIGAA/MEDIA_DOWNLOAD/GREETING/CRUD/CHECK_STATUS nunca são consultadas
    — get() retorna None sem nem tentar gerar embedding (guarda de entrada)."""
    mock_redis, mock_emb = _mock_redis_emb()

    with patch("src.infrastructure.semantic_cache.get_redis_text", return_value=mock_redis), \
         patch("src.infrastructure.semantic_cache.get_embeddings", return_value=mock_emb):
        cache = SemanticCache()
        resultado = await cache.get(query="qual minha nota?", rota="SIGAA")

    assert resultado is None
    mock_emb.embed_query.assert_not_called()


@pytest.mark.asyncio
async def test_set_usa_ttl_por_rota_quando_nao_especificado():
    mock_redis, mock_emb = _mock_redis_emb()

    with patch("src.infrastructure.semantic_cache.get_redis_text", return_value=mock_redis), \
         patch("src.infrastructure.semantic_cache.get_embeddings", return_value=mock_emb):
        cache = SemanticCache()
        await cache.set(query="qual o calendário?", rota="CALENDARIO", response={"answer": "..."})

    mock_redis.expire.assert_called_once()
    args, _ = mock_redis.expire.call_args
    assert args[1] == TTL_POR_ROTA["CALENDARIO"] == 6 * 3600


@pytest.mark.asyncio
async def test_set_nao_grava_rota_sem_cache():
    mock_redis, mock_emb = _mock_redis_emb()

    with patch("src.infrastructure.semantic_cache.get_redis_text", return_value=mock_redis), \
         patch("src.infrastructure.semantic_cache.get_embeddings", return_value=mock_emb):
        cache = SemanticCache()
        await cache.set(query="quero mudar meu telefone", rota="CRUD", response={"answer": "..."})

    mock_redis.hset.assert_not_called()
    mock_emb.embed_query.assert_not_called()


def test_invalidar_cache_rota_deleta_por_padrao():
    mock_redis = MagicMock()
    mock_redis.scan.side_effect = [(0, ["semcache:EDITAL:a", "semcache:EDITAL:b"])]
    mock_redis.delete.return_value = 2

    with patch("src.infrastructure.semantic_cache.get_redis_text", return_value=mock_redis):
        total = invalidar_cache_rota("EDITAL")

    mock_redis.scan.assert_called_with(0, match="semcache:EDITAL:*", count=100)
    assert total == 2


def test_cache_stats_agrega_por_rota():
    mock_redis = MagicMock()
    mock_redis.scan.side_effect = [
        (0, ["semcache:EDITAL:a", "semcache:EDITAL:b", "semcache:WIKI:c"]),
    ]

    with patch("src.infrastructure.semantic_cache.get_redis_text", return_value=mock_redis):
        stats = cache_stats()

    assert stats["total_entradas"] == 3
    assert stats["por_rota"] == {"EDITAL": 2, "WIKI": 1}
