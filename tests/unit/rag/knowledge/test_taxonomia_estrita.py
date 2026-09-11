"""O filtro por `doc_type` respeita quem o escolheu (2026-09-10).

Contexto do achado: uma pergunta feita pelo menu ("SIGAA" → wiki da CTIC)
custou 1747 tokens para responder "não encontrei". A busca não achou nada em
`wiki_ctic`, o filtro se anulou em silêncio, o rerank e a síntese rodaram
sobre conteúdo de outro assunto, e o LLM produziu a recusa que está no prompt.

A regra que estes testes protegem:

* Taxonomia escolhida por **classificador** (palpite sobre o texto) → errar é
  comum, cair na busca ampla salva a resposta. Comportamento histórico.
* Taxonomia escolhida pelo **usuário** (tecla do menu) → é instrução, não
  palpite. Sem resultado naquele assunto, responde vazio **sem chamar o LLM**.

Mesmo molde de dublês de `test_service.py`: embeddings e transformação de
query são substituídos para o teste falar só sobre a etapa de filtragem.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.rag.knowledge.query_transform import QueryTransformService, TransformedQuery
from src.rag.knowledge.service import RAGSearchService


def _chunk(doc_type: str, texto: str = "conteudo") -> dict:
    return {
        "id": f"c_{doc_type}_{texto[:4]}",
        "content": texto,
        "source": "x.txt",
        "doc_type": doc_type,
        "rrf_score": 0.9,
    }


def _servico() -> RAGSearchService:
    emb = MagicMock()
    emb.embed_query.return_value = [0.1, 0.2, 0.3]

    qt = AsyncMock(spec=QueryTransformService)
    qt.transformar.return_value = TransformedQuery(
        original="pergunta", primary="pergunta", variants=[], step_back="pergunta",
        keywords=[], strategy_used="passthrough", was_transformed=False,
    )
    return RAGSearchService(embedding_model=emb, query_transform=qt, use_rerank=False)


@pytest.mark.asyncio
async def test_estrito_devolve_vazio_quando_o_assunto_nao_tem_nada():
    """O caso que gerou o achado: menu pediu a wiki, índice só tem 'geral'."""
    chunks = [_chunk("geral"), _chunk("geral", "outro")]

    with patch("src.infrastructure.redis_client.busca_hibrida", return_value=chunks):
        r = await _servico().buscar(
            "como emito declaração?", doc_type="wiki_ctic", taxonomia_estrita=True,
        )

    assert r.ok is True
    assert r.data["found"] is False
    assert r.data["chunks"] == []
    # Sem chunks, `responder_rag_direto` retorna antes de chamar a síntese —
    # é aí que o token deixa de ser gasto.


@pytest.mark.asyncio
async def test_nao_estrito_mantem_o_retorno_amplo():
    """Comportamento histórico, preservado para quem classifica por palpite."""
    chunks = [_chunk("geral"), _chunk("geral", "outro")]

    with patch("src.infrastructure.redis_client.busca_hibrida", return_value=chunks):
        r = await _servico().buscar(
            "como emito declaração?", doc_type="wiki_ctic", taxonomia_estrita=False,
        )

    assert r.data["found"] is True
    assert len(r.data["chunks"]) == 2


@pytest.mark.asyncio
async def test_estrito_nao_atrapalha_quando_o_assunto_tem_conteudo():
    """Estrito não é "buscar menos": havendo conteúdo do tipo pedido, ele
    entrega esse conteúdo e descarta o que é de outro assunto."""
    chunks = [_chunk("geral"), _chunk("wiki_ctic", "resposta certa")]

    with patch("src.infrastructure.redis_client.busca_hibrida", return_value=chunks):
        r = await _servico().buscar(
            "como emito declaração?", doc_type="wiki_ctic", taxonomia_estrita=True,
        )

    assert r.data["found"] is True
    assert [c["doc_type"] for c in r.data["chunks"]] == ["wiki_ctic"]


@pytest.mark.asyncio
async def test_geral_nao_filtra_nada():
    """`geral` significa "sem taxonomia" — não há o que filtrar, e o nó não
    pede estrito nesse caso."""
    chunks = [_chunk("wiki_ctic"), _chunk("contatos", "telefone")]

    with patch("src.infrastructure.redis_client.busca_hibrida", return_value=chunks):
        r = await _servico().buscar("qualquer coisa", doc_type="geral", taxonomia_estrita=True)

    assert r.data["found"] is True
    assert len(r.data["chunks"]) == 2
