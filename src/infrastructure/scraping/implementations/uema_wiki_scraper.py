import logging
import re
from urllib.parse import urlparse, parse_qs

from bs4 import BeautifulSoup
import httpx

from src.infrastructure.scraping.base_scraper import BaseScraper, ScrapeRequest, ScrapedDocument

logger = logging.getLogger(__name__)

class UEMAWikiScraper(BaseScraper):
    """
    Scraper especializado para o DokuWiki do CTIC/UEMA.
    Extrai apenas a 'div.page', converte para Markdown e descobre links internos.
    """
    
    _ACOES_ADMIN = frozenset({"do=edit", "do=revisions", "do=backlink",
                              "do=recent", "do=index", "do=login",
                              "do=register", "do=resendpwd", "do=admin"})

    @property
    def source_name(self) -> str:
        return "wiki_ctic"

    @property
    def supported_domains(self) -> list[str]:
        return ["ctic.uema.br", "uema.br"]

    async def fetch(self, request: ScrapeRequest) -> str:
        """1. Faz o download do HTML bruto da página."""
        # O get_client do BaseScraper já deve injetar proxies e user-agents
        async with self.get_client() as client:
            response = await client.get(request.url, timeout=15.0)
            response.raise_for_status()
            return response.text

    async def parse(self, content: str, request: ScrapeRequest) -> ScrapedDocument:
        """2. Transforma o HTML em Markdown limpo e extrai metadados."""
        soup = BeautifulSoup(content, "lxml")

        # Foco na div.page
        div_page = soup.find("div", class_="page") or soup.find("div", id="dokuwiki__content")
        if not div_page:
            div_page = soup.find("body") or soup

        # Limpar Ruído
        for ruido in div_page.find_all(["div", "section"], class_=[
            "toolbar", "secedit", "footnotes", "catlist",
            "plugin_tag", "docInfo", "breadcrumbs",
        ]):
            ruido.decompose()

        # Extrair Links Internos (Para o RabbitMQ indexar depois)
        links_internos = self._extrair_links(div_page, request.url)

        # Converter para Markdown
        markdown = self._html_to_markdown(div_page)
        page_id = self._url_para_page_id(request.url)

        return ScrapedDocument(
            url=request.url,
            content=markdown,
            title=f"Wiki CTIC: {page_id}",
            doc_type="wiki_ctic",
            context_label=f"WIKI CTIC | {page_id.upper()}",
            metadata={"links_descobertos": links_internos, "page_id": page_id}
        )

    # --- Utilitários Privados ---

    def _html_to_markdown(self, elemento) -> str:
        try:
            from markdownify import markdownify as md
            texto = md(str(elemento), heading_style="ATX", bullets="-", strip=["img", "figure"])
            texto = re.sub(r"\n{3,}", "\n\n", texto)
            texto = re.sub(r"\[edit\]|\[rev\]|\[top\]", "", texto)
            return texto.strip()
        except ImportError:
            return elemento.get_text(separator="\n", strip=True)

    def _extrair_links(self, div_page, base_url: str) -> list[str]:
        links = []
        for a_tag in div_page.find_all("a", href=True):
            href = a_tag["href"]
            if "id=" not in href or any(acao in href for acao in self._ACOES_ADMIN):
                continue
            
            url_limpa = self._normalizar_url(href, base_url)
            if url_limpa and url_limpa not in links:
                links.append(url_limpa)
        return links

    def _normalizar_url(self, href: str, base_url: str) -> str:
        from urllib.parse import urlparse, urljoin, parse_qs
        url_completa = urljoin(base_url, href)
        parsed = urlparse(url_completa)
        params = parse_qs(parsed.query)
        if "id" in params:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={params['id'][0]}"
        return ""

    def _url_para_page_id(self, url: str) -> str:
        params = parse_qs(urlparse(url).query)
        return params.get("id", ["unknown"])[0]