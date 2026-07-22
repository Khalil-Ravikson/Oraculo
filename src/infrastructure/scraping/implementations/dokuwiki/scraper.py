"""
src/infrastructure/scraping/implementations/dokuwiki/scraper.py
------------------------------------------------------------------
Scraper para o DokuWiki do CTIC/UEMA (https://ctic.uema.br/wiki/).

DIFERENÇA-CHAVE em relação ao scraper genérico anterior: em vez de raspar o
HTML renderizado (nav/sidebar/rodapé + BeautifulSoup), usa o endpoint nativo
`do=export_raw` do DokuWiki, que devolve o wikitext-fonte da página, limpo
de qualquer elemento de navegação. Testado manualmente contra o site real
(ctic.uema.br/wiki) antes de implementar — ver `arquitetura_oraculo.md`/plano.

fetch()  → GET `{doku_php}?id={page_id}&do=export_raw`
parse()  → wikitext.convert() (headers/tabelas/links/mídia) + hierarquia
           (sistema/modulo inferidos via hierarchy.py)

PDFs anexados (`{{:arquivo.pdf}}`) NÃO são baixados nem parseados — decisão
do projeto: até agora são slides de apresentação (pouco texto extraível, e o
conteúdo procedural já está na própria página wiki). Em vez disso, o texto
do chunk fica só com um link direto pro arquivo (`media.build_media_url()`),
para o usuário abrir manualmente.
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

import httpx

from src.infrastructure.scraping.base_scraper import BaseScraper, ScrapedDocument
from . import hierarchy, media, wikitext

logger = logging.getLogger(__name__)


def _page_id_from_url(url: str) -> str:
    params = parse_qs(urlparse(url).query)
    return params.get("id", ["start"])[0]


def _export_raw_url(url: str) -> str:
    """Reescreve a URL da página para apontar ao endpoint `do=export_raw`."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    page_id = params.get("id", ["start"])[0]
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={page_id}&do=export_raw"


class DokuWikiScraper(BaseScraper):
    """Scraper especializado para o DokuWiki do CTIC/UEMA."""

    def __init__(self, *args, graph_store: hierarchy.GraphStore | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._graph_store = graph_store or hierarchy.InMemoryGraphStore()

    @property
    def source_name(self) -> str:
        return "wiki_ctic"

    @property
    def supported_domains(self) -> list[str]:
        return ["ctic.uema.br"]

    async def fetch(self, url: str, headers: dict) -> str:
        export_url = _export_raw_url(url)
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            r = await client.get(export_url, follow_redirects=True)
            r.raise_for_status()
            # `do=export_raw` não declara charset no header Content-Type —
            # sem isso, httpx tenta adivinhar e erra com acentos (mojibake).
            # DokuWiki serve UTF-8, então força explicitamente.
            r.encoding = "utf-8"
            return r.text

    def parse(self, raw_content: str, url: str) -> ScrapedDocument:
        page_id = _page_id_from_url(url)
        parsed_url = urlparse(url)
        wiki_base = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path.rsplit('/', 1)[0]}/"
        media_url_builder = lambda media_path: media.build_media_url(wiki_base, media_path)  # noqa: E731

        converted = wikitext.convert(raw_content, media_url_builder=media_url_builder)

        hierarchy.registrar_links(page_id, converted.internal_links, self._graph_store)
        taxonomia = hierarchy.resolver_taxonomia(page_id, self._graph_store)

        parsed = urlparse(url)
        doku_php_base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        links_descobertos = [f"{doku_php_base}?id={child_id}" for child_id in converted.internal_links]

        title = page_id.replace("_", " ").replace(":", " / ").title()
        first_heading = next((l for l in converted.markdown.splitlines() if l.startswith("#")), None)
        if first_heading:
            title = first_heading.lstrip("#").strip()

        return ScrapedDocument(
            url=url,
            title=title,
            content=converted.markdown,
            source_name=self.source_name,
            doc_type="wiki_ctic",
            metadata={
                "page_id": page_id,
                "links_descobertos": links_descobertos,
                "pdf_attachments": converted.pdf_attachments,
                "sistema": taxonomia["sistema"],
                "modulo": taxonomia["modulo"],
                "setor": "CTIC",
                "tipo_doc": "Manual",
            },
        )
