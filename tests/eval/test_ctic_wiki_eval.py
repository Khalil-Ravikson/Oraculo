"""
tests/eval/test_ctic_wiki_eval.py
------------------------------------
Eval do scraper/RAG do wiki CTIC (DokuWiki), no mesmo espírito de
`test_sigaa_eval.py`: fixtures congeladas (wikitext real, baixado 1x de
ctic.uema.br/wiki via `do=export_raw`) + asserts factuais duros contra um
"gabarito" — não apenas "não é vazio".

O que este eval prova, e por quê importa:
  1. wikitext.py converte markup DokuWiki real (headers/tabelas/PDFs/links)
     sem depender de HTML renderizado.
  2. hierarchy.py resolve corretamente `sistema`/`modulo` subindo a cadeia de
     pais — a base da decisão de "índice único + filtro por tag" (não um
     banco separado por fonte).
  3. A propagação da taxonomia até `salvar_chunk()` (fix aplicado em
     `scraping_service.py::_ingest_to_rag`) realmente acontece — antes dessa
     mudança, sistema/modulo se perdiam no meio do caminho.
  4. O chunker `markdown` preserva os fatos-chave de uma página tutorial
     (ex.: nomes exatos de botões) em algum chunk — o que valida a troca de
     `semantic` → `markdown` para doc_type="wiki_ctic".
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.scraping.implementations.dokuwiki import hierarchy, media, wikitext
from src.infrastructure.scraping.implementations.dokuwiki.discovery import parse_index_page_ids
from src.infrastructure.scraping.implementations.dokuwiki.scraper import DokuWikiScraper

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ctic_wiki"
BASE_URL = "https://ctic.uema.br/wiki/doku.php"


def _load_fixture(page_id: str) -> str:
    return (FIXTURES_DIR / f"{page_id}.txt").read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Conversão wikitext → Markdown (sem rede, sem Redis)
# ─────────────────────────────────────────────────────────────────────────────

def test_eval_1_transferir_estoque_vira_markdown_com_tabela_e_headers():
    """Eval 1: página tutorial (tabela + headers). Critério: fatos-chave sobrevivem à conversão."""
    raw = _load_fixture("transferir_estoque_do_material")
    result = wikitext.convert(raw)

    assert result.markdown.startswith("# Transferir Estoque do Material")
    assert "SIPAC" in result.markdown
    assert "Gestor de Almoxarifado" in result.markdown
    assert "Trocar Material" in result.markdown
    assert "Material Anterior" in result.markdown
    # Tabela DokuWiki (| Sistema | SIPAC |) virou tabela Markdown (linha separadora)
    assert "| --- | --- |" in result.markdown
    # Sem PDF anexado nesta página
    assert result.pdf_attachments == []


def test_eval_2_almoxarifado_detecta_anexo_pdf_e_links_internos():
    """Eval 2: página de módulo (PDF + muitos links). Critério: PDF detectado, link-alvo correto."""
    raw = _load_fixture("almoxarifado")
    result = wikitext.convert(raw)

    assert len(result.pdf_attachments) == 1
    assert result.pdf_attachments[0]["media"].endswith("versao_completa.pdf")

    # O link [[ Transferir Estoque do Material ]] deve normalizar para o
    # page_id real usado na URL do site (confirmado manualmente no site).
    assert "transferir_estoque_do_material" in result.internal_links


# ─────────────────────────────────────────────────────────────────────────────
# 2. Hierarquia (sistema/modulo) via grafo de links
# ─────────────────────────────────────────────────────────────────────────────

def test_eval_3_hierarquia_infere_sistema_modulo_por_cadeia_de_pais():
    """
    Eval 3: valida a decisão de arquitetura (índice único + tag `sistema`/`modulo`
    em vez de banco separado). Simula a ordem real de crawling: hub de módulo
    processado antes da página-tutorial, para que o grafo pai→filho já exista
    quando resolvemos a taxonomia da página-folha.
    """
    store = hierarchy.InMemoryGraphStore()
    scraper = DokuWikiScraper(graph_store=store)

    doc_almoxarifado = scraper.parse(_load_fixture("almoxarifado"), f"{BASE_URL}?id=almoxarifado")
    assert doc_almoxarifado.metadata["sistema"] == "SIPAC"
    assert doc_almoxarifado.metadata["modulo"] == "Almoxarifado"

    doc_tutorial = scraper.parse(
        _load_fixture("transferir_estoque_do_material"),
        f"{BASE_URL}?id=transferir_estoque_do_material",
    )
    # transferir_estoque_do_material não é um hub conhecido — só resolve
    # corretamente se herdou o pai "almoxarifado" via grafo de links.
    assert doc_tutorial.metadata["sistema"] == "SIPAC"
    assert doc_tutorial.metadata["modulo"] == "Almoxarifado"
    assert doc_tutorial.metadata["setor"] == "CTIC"


def test_eval_4_sem_hierarquia_conhecida_cai_no_default_geral():
    """Eval 4: página nunca vista antes (sem pai registrado) não deve inventar sistema/modulo."""
    store = hierarchy.InMemoryGraphStore()
    scraper = DokuWikiScraper(graph_store=store)

    doc = scraper.parse(_load_fixture("start"), f"{BASE_URL}?id=start")
    assert doc.metadata["sistema"] == hierarchy.DEFAULT_SISTEMA
    assert doc.metadata["modulo"] == hierarchy.DEFAULT_MODULO


# ─────────────────────────────────────────────────────────────────────────────
# 3. Chunking (markdown) preserva fatos-chave para retrieval
# ─────────────────────────────────────────────────────────────────────────────

def test_eval_5_chunker_markdown_preserva_fatos_da_pagina_tutorial():
    """
    Eval 5: valida a troca de chunker `semantic` → `markdown` para wiki_ctic
    (ChunkerFactory.for_doc_type). Um chunk específico deve conter os fatos
    que uma pergunta real do usuário ("como transfiro estoque no SIPAC?")
    precisa para ser respondida corretamente.
    """
    from src.rag.ingestion.chunker_factory import ChunkerFactory

    raw = _load_fixture("transferir_estoque_do_material")
    converted = wikitext.convert(raw)

    chunker = ChunkerFactory.for_doc_type("wiki_ctic")
    assert chunker.name == "markdown"

    chunks = chunker.chunk(converted.markdown, source="transferir_estoque_do_material", doc_type="wiki_ctic")
    assert chunks, "chunker não deve retornar lista vazia para uma página válida"

    joined_relevant = "\n".join(
        c.text for c in chunks if "Trocar Material" in c.text or "Material Anterior" in c.text
    )
    assert "Trocar Material" in joined_relevant
    assert "Material Anterior" in joined_relevant
    assert "Novo Material" in joined_relevant


# ─────────────────────────────────────────────────────────────────────────────
# 4. Propagação da taxonomia até salvar_chunk() (fix em _ingest_to_rag)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_eval_6_ingest_to_rag_propaga_sistema_modulo_para_salvar_chunk(fake_redis):
    """
    Eval 6: sem este comportamento, o filtro por `sistema="SIPAC"` (Parte 3 do
    plano) não teria efeito nenhum — os chunks cairiam sempre em sistema/modulo
    default "Geral", misturando fontes na busca.
    """
    from src.infrastructure.scraping.scraping_service import ScrapingService

    # Usa "almoxarifado" (hub conhecido) em vez da página-tutorial: aqui o
    # que se quer validar é a propagação metadata -> salvar_chunk, não a
    # resolução de hierarquia em si (já coberta pelos evals 3 e 4).
    store = hierarchy.InMemoryGraphStore()
    scraper = DokuWikiScraper(graph_store=store)
    document = scraper.parse(_load_fixture("almoxarifado"), f"{BASE_URL}?id=almoxarifado")

    service = ScrapingService(rag_ingestion=True)

    saved_docs = []

    def _fake_salvar_chunk(**kwargs):
        saved_docs.append(kwargs)

    fake_embeddings = AsyncMock()
    with patch("src.rag.embeddings.get_embeddings") as mock_get_emb, \
         patch("src.infrastructure.redis_client.salvar_chunk", side_effect=_fake_salvar_chunk):
        mock_model = mock_get_emb.return_value
        mock_model.embed_documents = lambda textos: [[0.0] * 8 for _ in textos]

        await service._ingest_to_rag(document)

    assert saved_docs, "nenhum chunk foi salvo — pipeline de ingestão quebrou"
    assert all(d["metadata"]["sistema"] == "SIPAC" for d in saved_docs)
    assert all(d["metadata"]["modulo"] == "Almoxarifado" for d in saved_docs)
    assert all(d["metadata"]["setor"] == "CTIC" for d in saved_docs)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Utilidades determinísticas (sem rede)
# ─────────────────────────────────────────────────────────────────────────────

def test_eval_7_discovery_extrai_page_ids_de_html_do_index():
    """Eval 7: parser de `do=index` extrai page_ids únicos, ignorando ações admin."""
    html_fake = """
    <html><body>
      <ul class="idx">
        <li><a class="wikilink1" href="/wiki/doku.php?id=almoxarifado">Almoxarifado</a></li>
        <li><a class="wikilink1" href="/wiki/doku.php?id=start">start</a></li>
        <li><a href="/wiki/doku.php?id=almoxarifado&do=edit">editar</a></li>
      </ul>
    </body></html>
    """
    page_ids = parse_index_page_ids(html_fake)
    assert page_ids == ["almoxarifado", "start"]


def test_eval_8_media_url_builder_monta_url_de_fetch_correta():
    """Eval 8: URL de download de PDF anexado segue o padrão `lib/exe/fetch.php?media=...`."""
    url = media.build_media_url(
        "https://ctic.uema.br/wiki/doku.php",
        "apresentacao_sipac_-_almoxarifado_-_versao_completa.pdf",
    )
    assert url == (
        "https://ctic.uema.br/wiki/lib/exe/fetch.php"
        "?media=apresentacao_sipac_-_almoxarifado_-_versao_completa.pdf"
    )


def test_eval_9_pdf_anexado_vira_link_clicavel_sem_baixar_o_arquivo():
    """
    Eval 9: decisão do projeto — PDFs do wiki CTIC (slides de apresentação)
    não são baixados/parseados, só viram link direto no texto do chunk.
    """
    store = hierarchy.InMemoryGraphStore()
    scraper = DokuWikiScraper(graph_store=store)
    document = scraper.parse(_load_fixture("almoxarifado"), f"{BASE_URL}?id=almoxarifado")

    assert (
        "[Anexo PDF: apresentacao_sipac_-_almoxarifado_-_versao_completa.pdf]"
        "(https://ctic.uema.br/wiki/lib/exe/fetch.php"
        "?media=apresentacao_sipac_-_almoxarifado_-_versao_completa.pdf)"
    ) in document.content
    assert document.metadata["pdf_attachments"][0]["url"].startswith(
        "https://ctic.uema.br/wiki/lib/exe/fetch.php?media="
    )
