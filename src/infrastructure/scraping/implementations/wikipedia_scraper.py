"""
src/infrastructure/scraping/implementations/wikipedia_scraper.py
------------------------------------------------------------------
Scraper para Wikipedia — exemplo de implementação concreta.
"""
from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup

from src.infrastructure.scraping.base_scraper import BaseScraper, ScrapedDocument

logger = logging.getLogger(__name__)


class WikipediaScraper(BaseScraper):
    """
    Scraper para artigos da Wikipedia (pt.wikipedia.org e en.wikipedia.org).
    Usa a API REST da Wikipedia para obter JSON estruturado quando possível.
    """

    @property
    def source_name(self) -> str:
        return "wikipedia"

    @property
    def supported_domains(self) -> list[str]:
        return ["wikipedia.org", "pt.wikipedia.org", "en.wikipedia.org"]

    async def fetch(self, url: str, headers: dict) -> str:
        """
        Tenta a API REST primeiro (JSON limpo), fallback para HTML.
        API REST: https://pt.wikipedia.org/api/rest_v1/page/summary/{título}
        """
        api_url = self._to_api_url(url)
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            if api_url:
                try:
                    r = await client.get(api_url)
                    if r.status_code == 200:
                        return f"__JSON_API__:{r.text}"
                except Exception:
                    pass
            r = await client.get(url)
            r.raise_for_status()
            return r.text

    def parse(self, raw_content: str, url: str) -> ScrapedDocument:
        if raw_content.startswith("__JSON_API__:"):
            return self._parse_api(raw_content[len("__JSON_API__:"):], url)
        return self._parse_html(raw_content, url)

    def _parse_api(self, json_text: str, url: str) -> ScrapedDocument:
        import json
        data = json.loads(json_text)
        title = data.get("title", "")
        extract = data.get("extract", "")
        description = data.get("description", "")
        content = f"{title}\n\n{description}\n\n{extract}".strip() if extract else ""

        return ScrapedDocument(
            url=url,
            title=title,
            content=content,
            source_name=self.source_name,
            doc_type="wiki",
            metadata={
                "description": description,
                "type": data.get("type", ""),
                "page_id": data.get("pageid", 0),
                "lang": data.get("lang", "pt"),
            },
            language=data.get("lang", "pt"),
        )

    def _parse_html(self, html: str, url: str) -> ScrapedDocument:
        soup = BeautifulSoup(html, "html.parser")

        # Remove elementos de navegação e metadados
        for tag in soup.find_all(["nav", "header", "footer", "aside", ".mw-navigation",
                                   ".mw-editsection", ".reference", ".citation"]):
            tag.decompose()

        title_tag = soup.find("h1", {"id": "firstHeading"}) or soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""

        content_div = soup.find("div", {"id": "mw-content-text"}) or soup.find("div", class_="mw-parser-output")
        if not content_div:
            return ScrapedDocument(url=url, title=title, content="", source_name=self.source_name, doc_type="wiki")

        # Extrai parágrafos em ordem
        paragrafos = []
        for elem in content_div.find_all(["p", "h2", "h3", "li"], recursive=True):
            texto = elem.get_text(separator=" ", strip=True)
            if texto and len(texto) > 20:
                if elem.name in ("h2", "h3"):
                    paragrafos.append(f"\n## {texto}\n")
                else:
                    paragrafos.append(texto)

        content = "\n\n".join(paragrafos)
        content = re.sub(r"\[\d+\]", "", content)   # Remove [1], [2], etc.
        content = re.sub(r"\s{3,}", "\n\n", content)  # Normaliza espaços

        return ScrapedDocument(
            url=url,
            title=title,
            content=content.strip(),
            source_name=self.source_name,
            doc_type="wiki",
            metadata={"parsed_from": "html"},
        )

    def _to_api_url(self, url: str) -> str | None:
        """Converte URL de artigo para URL da API REST."""
        import re
        match = re.search(r"(pt|en)\.wikipedia\.org/wiki/(.+)", url)
        if match:
            lang = match.group(1)
            title = match.group(2)
            return f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
        return None