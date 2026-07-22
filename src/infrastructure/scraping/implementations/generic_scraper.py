"""
src/infrastructure/scraping/implementations/generic_scraper.py
----------------------------------------------------------------
Scraper genérico para qualquer URL (fallback quando nenhum scraper
especializado reconhece o domínio).
"""
from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup

from src.infrastructure.scraping.base_scraper import BaseScraper, ScrapedDocument

logger = logging.getLogger(__name__)


class GenericHTTPScraper(BaseScraper):
    """
    Scraper genérico para qualquer site HTML.
    Extrai texto visível dos elementos principais.
    Útil como fallback quando não existe scraper especializado.
    """

    @property
    def source_name(self) -> str:
        return "generic_web"

    @property
    def supported_domains(self) -> list[str]:
        return ["*"]  # aceita qualquer domínio

    def can_handle(self, url: str) -> bool:
        return url.startswith("http")  # aceita qualquer HTTP

    async def fetch(self, url: str, headers: dict) -> str:
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            r = await client.get(url, follow_redirects=True)
            r.raise_for_status()
            return r.text

    def parse(self, raw_content: str, url: str) -> ScrapedDocument:
        soup = BeautifulSoup(raw_content, "html.parser")

        # Remove ruído
        for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                                   "aside", "iframe", "noscript", "form"]):
            tag.decompose()

        # Título
        title_tag = soup.find("title") or soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else url

        # Conteúdo principal (prioriza <main>, <article>, <div class=content>)
        main = (
            soup.find("main") or
            soup.find("article") or
            soup.find(id=re.compile(r"content|main|body", re.I)) or
            soup.find("body")
        )

        if not main:
            return ScrapedDocument(url=url, title=title, content="", source_name=self.source_name, doc_type="web")

        textos = []
        for elem in main.find_all(["p", "h1", "h2", "h3", "h4", "li", "td", "th"]):
            t = elem.get_text(separator=" ", strip=True)
            if t and len(t) > 15:
                if elem.name in ("h1", "h2", "h3", "h4"):
                    textos.append(f"\n## {t}\n")
                else:
                    textos.append(t)

        content = "\n\n".join(textos)
        content = re.sub(r"\s{3,}", "\n\n", content)

        from urllib.parse import urlparse
        domain = urlparse(url).netloc

        return ScrapedDocument(
            url=url,
            title=title,
            content=content.strip(),
            source_name=self.source_name,
            doc_type="web",
            metadata={"domain": domain},
        )