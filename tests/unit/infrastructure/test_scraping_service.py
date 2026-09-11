"""O scraping só serve para alguma coisa se o resultado chegar ao índice.

Contexto do achado (2026-09-11): `build_default_scraping_service` aceitava
`ingest_to_rag=True`, não repassava para o construtor, e `ScrapingService`
ficava com `self._rag = None`. O efeito era o pior possível — tudo parecia
funcionar: as páginas eram raspadas, o log dizia "✅ Scraped", a contagem de
sucesso subia. Só o índice não mudava.

Foi visto rodando a ingestão da wiki inteira: 320 páginas raspadas com
sucesso e o índice parado nos mesmos 30 trechos de antes.
"""
from __future__ import annotations

import pytest

from src.infrastructure.scraping.scraping_service import (
    ScrapingService,
    build_default_scraping_service,
)


def test_fabrica_repassa_o_pedido_de_ingestao():
    """`ingest_to_rag=True` precisa CHEGAR ao serviço. Era exatamente este
    repasse que faltava."""
    servico = build_default_scraping_service(redis_client=None, ingest_to_rag=True)
    assert servico._rag, "ingest_to_rag=True não ligou a ingestão no serviço"


def test_fabrica_respeita_ingestao_desligada():
    servico = build_default_scraping_service(redis_client=None, ingest_to_rag=False)
    assert not servico._rag


def test_construtor_sem_ingestao_e_o_default():
    """Quem constrói o serviço à mão continua sem ingerir, como antes — a
    correção não muda o default, só para de perder o parâmetro."""
    assert not ScrapingService()._rag


@pytest.mark.asyncio
async def test_scrape_chama_a_ingestao_quando_ligada(monkeypatch):
    """Prova o caminho inteiro: com a ingestão ligada, um scrape bem-sucedido
    chama `_ingest_to_rag` com o documento."""
    from src.infrastructure.scraping.base_scraper import (
        ScrapeRequest,
        ScrapeResult,
        ScrapedDocument,
    )

    doc = ScrapedDocument(
        url="https://ctic.uema.br/wiki/doku.php?id=x",
        title="X",
        content="conteúdo suficiente para valer",
        doc_type="wiki_ctic",
        source_name="wiki",
    )

    class _ScraperFake:
        def can_handle(self, url: str) -> bool:
            return True

        async def scrape(self, request):
            return ScrapeResult(ok=True, document=doc, request_id=request.request_id)

    servico = ScrapingService(rag_ingestion=True)
    servico.register(_ScraperFake(), fallback=True)

    ingeridos = []

    async def _fake_ingest(documento):
        ingeridos.append(documento)

    monkeypatch.setattr(servico, "_ingest_to_rag", _fake_ingest)

    resultado = await servico.scrape(ScrapeRequest(url=doc.url, doc_type="wiki_ctic"))

    assert resultado.ok
    assert ingeridos == [doc], "scrape bem-sucedido não ingeriu o documento"


@pytest.mark.asyncio
async def test_scrape_nao_ingere_quando_desligada(monkeypatch):
    from src.infrastructure.scraping.base_scraper import (
        ScrapeRequest,
        ScrapeResult,
        ScrapedDocument,
    )

    doc = ScrapedDocument(
        url="https://x/y", title="X", content="texto", doc_type="web", source_name="x",
    )

    class _ScraperFake:
        def can_handle(self, url: str) -> bool:
            return True

        async def scrape(self, request):
            return ScrapeResult(ok=True, document=doc, request_id=request.request_id)

    servico = ScrapingService()  # sem rag_ingestion
    servico.register(_ScraperFake(), fallback=True)

    chamou = []

    async def _fake_ingest(documento):
        chamou.append(documento)

    monkeypatch.setattr(servico, "_ingest_to_rag", _fake_ingest)
    await servico.scrape(ScrapeRequest(url=doc.url))

    assert chamou == []
