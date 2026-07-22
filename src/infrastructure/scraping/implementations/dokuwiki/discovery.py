"""
src/infrastructure/scraping/implementations/dokuwiki/discovery.py
------------------------------------------------------------------
Descoberta em massa de todas as páginas do wiki via `doku.php?do=index`
(sitemap nativo do DokuWiki — testado manualmente em ctic.uema.br/wiki,
retorna todos os page_ids do site em uma única página HTML).

Substitui a necessidade de um crawler recursivo só para "achar todas as
páginas": isso aqui roda 1x (ou periodicamente, via Celery beat) e alimenta
a fila de scraping com um ScrapeRequest por página.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin, parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from src.infrastructure.scraping.base_scraper import ScrapeRequest

logger = logging.getLogger(__name__)

_ACOES_ADMIN = frozenset({
    "do=edit", "do=revisions", "do=backlink", "do=recent",
    "do=index", "do=login", "do=register", "do=resendpwd", "do=admin", "do=diff",
})


def _extract_page_id(href: str) -> str | None:
    parsed = urlparse(href)
    if any(acao in parsed.query for acao in _ACOES_ADMIN):
        return None
    params = parse_qs(parsed.query)
    page_id = params.get("id", [None])[0]
    return page_id


def parse_index_page_ids(html: str) -> list[str]:
    """Extrai a lista de page_ids únicos a partir do HTML de `do=index`."""
    soup = BeautifulSoup(html, "lxml")
    page_ids: list[str] = []
    seen = set()

    for a_tag in soup.find_all("a", href=True):
        page_id = _extract_page_id(a_tag["href"])
        if page_id and page_id not in seen:
            seen.add(page_id)
            page_ids.append(page_id)

    return page_ids


async def descobrir_paginas(doku_php_url: str, timeout: float = 20.0) -> list[str]:
    """
    Busca `{doku_php_url}?do=index` e retorna todos os page_ids do wiki.
    `doku_php_url` deve apontar para o endpoint `doku.php` do site
    (ex.: https://ctic.uema.br/wiki/doku.php).
    """
    index_url = f"{doku_php_url}?do=index"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(index_url, follow_redirects=True)
        resp.raise_for_status()

    page_ids = parse_index_page_ids(resp.text)
    logger.info("📇 DokuWiki do=index: %d páginas descobertas em %s", len(page_ids), doku_php_url)
    return page_ids


def montar_requests(doku_php_url: str, page_ids: list[str], priority: int = 8) -> list[ScrapeRequest]:
    """Constrói um ScrapeRequest por page_id, pronto para `scrape_batch`/`scrape_and_queue`."""
    return [
        ScrapeRequest(
            url=f"{doku_php_url}?id={page_id}",
            doc_type="wiki_ctic",
            priority=priority,
            metadata={"page_id": page_id},
        )
        for page_id in page_ids
    ]
