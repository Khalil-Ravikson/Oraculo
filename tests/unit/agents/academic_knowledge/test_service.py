# tests/unit/agents/academic_knowledge/test_service.py
"""
Testes de RAGSearchService/QueryTransformService, migrados de
tests/unit/domain/test_retrieve_use_case.py na Fase 4 do
PLANO_REFATORACAO_SUPERVISOR.md. O teste antigo permanece no lugar (ainda
verde via shim de compatibilidade) até a remoção do shim na Fase 7.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.agents.academic_knowledge.query_transform import QueryTransformService, TransformedQuery
from src.agents.academic_knowledge.service import RAGSearchService, ToolResult

# ─────────────────────────────────────────────────────────────────────────────
# Testes do QueryTransformService
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_transform_proper_noun():
    service = QueryTransformService()
    with patch.object(service, "transformar_com_flash", AsyncMock(side_effect=lambda q, r, h: q)):
        res = await service.transformar("Quem é o Prof. João Silva na UEMA?")

        assert '"João Silva"' in res.variants
        assert "João Silva" in res.keywords
        assert res.strategy_used == "proper_noun"
        assert res.was_transformed is True

@pytest.mark.asyncio
async def test_query_transform_step_back():
    service = QueryTransformService()
    with patch.object(service, "transformar_com_flash", AsyncMock(side_effect=lambda q, r, h: q)):
        res = await service.transformar("quais vagas de br-ppi na UEMA em 03/02/2026?")

        assert "03/02/2026" not in res.step_back
        assert "br-ppi" not in res.step_back.lower()
        assert "cota" in res.step_back.lower()

@pytest.mark.asyncio
async def test_query_transform_local_enrichment():
    service = QueryTransformService()
    with patch.object(service, "transformar_com_flash", AsyncMock(side_effect=lambda q, r, h: q)):
        res = await service.transformar("quero fazer trancamento de materia")

        assert "cancelamento disciplina" in res.primary or "trancar materia" in res.primary
        assert res.strategy_used == "keyword_enrich"
        assert res.was_transformed is True


# ─────────────────────────────────────────────────────────────────────────────
# Testes do RAGSearchService
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rag_search_buscar_success():
    mock_emb = MagicMock()
    mock_emb.embed_query.return_value = [0.1, 0.2, 0.3]

    transformed_mock = TransformedQuery(
        original="quando é a matrícula?",
        primary="quando é a matrícula?",
        variants=[],
        step_back="quando é a matrícula",
        keywords=[],
        strategy_used="passthrough",
        was_transformed=False,
    )
    mock_qt = AsyncMock(spec=QueryTransformService)
    mock_qt.transformar.return_value = transformed_mock

    mock_chunks = [
        {
            "id": "chunk_1",
            "content": "A matrícula ocorrerá no dia 03/02/2026.",
            "source": "calendario-academico-2026.pdf",
            "doc_type": "calendario",
            "rrf_score": 0.9,
        }
    ]

    service = RAGSearchService(embedding_model=mock_emb, query_transform=mock_qt, use_rerank=False)

    with patch("src.infrastructure.redis_client.busca_hibrida", return_value=mock_chunks):
        res = await service.buscar("quando é a matrícula?")

        assert res.ok is True
        assert res.data["found"] is True
        assert "A matrícula ocorrerá no dia 03/02/2026." in res.message
        assert "Calendário Acadêmico UEMA 2026" in res.message

@pytest.mark.asyncio
async def test_rag_search_buscar_deduplicacao():
    mock_emb = MagicMock()
    mock_emb.embed_query.return_value = [0.1, 0.2, 0.3]

    transformed_mock = TransformedQuery(
        original="Quem é João?",
        primary="Quem é João?",
        variants=['"João"'],
        step_back="Quem é João",
        keywords=["João"],
        strategy_used="proper_noun",
        was_transformed=True,
    )
    mock_qt = AsyncMock(spec=QueryTransformService)
    mock_qt.transformar.return_value = transformed_mock

    mock_results_1 = [
        {
            "id": "c1",
            "content": "João é professor de computação na UEMA.",
            "source": "guia.pdf",
            "doc_type": "contatos",
            "rrf_score": 0.9,
        }
    ]
    mock_results_2 = [
        {
            "id": "c2",
            "content": "João é professor de computação na UEMA.",
            "source": "guia.pdf",
            "doc_type": "contatos",
            "rrf_score": 0.8,
        }
    ]

    service = RAGSearchService(embedding_model=mock_emb, query_transform=mock_qt, use_rerank=False)

    with patch("src.infrastructure.redis_client.busca_hibrida", side_effect=[mock_results_1, mock_results_2]):
        res = await service.buscar("Quem é João?")

        assert res.ok is True
        assert len(res.data["chunks"]) == 1
        assert res.data["chunks"][0]["id"] == "c1"

@pytest.mark.asyncio
async def test_rag_search_buscar_step_back_fallback():
    mock_emb = MagicMock()
    mock_emb.embed_query.return_value = [0.1, 0.2, 0.3]

    transformed_mock = TransformedQuery(
        original="quando é a matrícula no dia 03/02/2026?",
        primary="quando é a matrícula no dia 03/02/2026?",
        variants=[],
        step_back="quando é a matrícula",
        keywords=[],
        strategy_used="passthrough",
        was_transformed=False,
    )
    mock_qt = AsyncMock(spec=QueryTransformService)
    mock_qt.transformar.return_value = transformed_mock

    mock_results_1 = []
    mock_results_2 = [
        {
            "id": "chunk_sb",
            "content": "Cronograma de matrículas ocorrendo em fevereiro.",
            "source": "calendario-academico-2026.pdf",
            "doc_type": "calendario",
            "rrf_score": 0.7,
        }
    ]

    service = RAGSearchService(embedding_model=mock_emb, query_transform=mock_qt, use_rerank=False)

    with patch("src.infrastructure.redis_client.busca_hibrida", side_effect=[mock_results_1, mock_results_2]):
        res = await service.buscar("quando é a matrícula no dia 03/02/2026?")

        assert res.ok is True
        assert res.data["found"] is True
        assert res.data["metodo"] == "step_back_fallback"
        assert "Cronograma de matrículas ocorrendo em fevereiro." in res.message
