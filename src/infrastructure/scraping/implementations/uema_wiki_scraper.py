import logging
import re
from urllib.parse import urlparse, parse_qs, urljoin

from bs4 import BeautifulSoup

from src.infrastructure.scraping.base_scraper import BaseScraper, ScrapeRequest, ScrapedDocument

logger = logging.getLogger(__name__)

class UEMAWikiScraper(BaseScraper):
    """
    Scraper especializado para o DokuWiki do CTIC/UEMA.
    Extrai o conteúdo principal, converte para Markdown e descobre links internos.
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
        async with self.get_client() as client:
            response = await client.get(request.url, timeout=15.0)
            response.raise_for_status()
            return response.text

    async def parse(self, content: str, request: ScrapeRequest) -> ScrapedDocument:
        soup = BeautifulSoup(content, "lxml")

        # 1. Extrair Links Internos ANTES de destruir o HTML (para o Crawler continuar)
        div_page = soup.find("div", class_="page") or soup.find("div", id="dokuwiki__content") or soup.find("body")
        links_internos = self._extrair_links(div_page, request.url) if div_page else []

        # 2. Remoção Agressiva de Ruído
        for sel in ["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript", "form", "button", "input", "meta", "link"]:
            for t in soup.find_all(sel): t.decompose()

        for cls in ["secedit", "toolbar", "breadcrumbs", "dokuwiki__footer", "dokuwiki__header", "sidebar", "toc", "catlist", "search_quickresult", "page__footer", "docInfo", "footnotes"]:
            for t in soup.find_all(class_=cls): t.decompose()

        main = soup.find("div", class_="page") or soup.find("div", id="dokuwiki__content") or soup.find("body")
        
        page_id = self._url_para_page_id(request.url)
        title_node = soup.find("h1") or soup.find("title")
        title = title_node.get_text(strip=True) if title_node else f"Wiki CTIC: {page_id}"

        if not main:
            return ScrapedDocument(url=request.url, title=title, content="", source_name=self.source_name, doc_type="wiki_ctic")

        # 3. Converter para Markdown
        lines = []
        for elem in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "pre"]):
            text = elem.get_text(separator=" ", strip=True)
            if not text or len(text) < 4: 
                continue
            prefix = {"h1":"# ", "h2":"## ", "h3":"### ", "h4":"#### "}.get(elem.name, "")
            lines.append(f"{prefix}{text}")

        markdown = "\n\n".join(lines)
        markdown = re.sub(r"\[edit\]|\[rev\]|\[top\]|\[backlink\]|\*\*\s*\*\*", "", markdown)
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()

        return ScrapedDocument(
            url=request.url,
            content=markdown,
            title=title,
            doc_type="wiki_ctic",
            context_label=f"WIKI CTIC | {page_id.upper()}",
            metadata={"links_descobertos": links_internos, "page_id": page_id}
        )

    # --- Utilitários Privados ---

    def _extrair_links(self, container, base_url: str) -> list[str]:
        links = []
        for a_tag in container.find_all("a", href=True):
            href = a_tag["href"]
            if "id=" not in href or any(acao in href for acao in self._ACOES_ADMIN):
                continue
            
            url_limpa = self._normalizar_url(href, base_url)
            if url_limpa and url_limpa not in links:
                links.append(url_limpa)
        return links

    def _normalizar_url(self, href: str, base_url: str) -> str:
        url_completa = urljoin(base_url, href)
        parsed = urlparse(url_completa)
        params = parse_qs(parsed.query)
        if "id" in params:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={params['id'][0]}"
        return ""

    def _url_para_page_id(self, url: str) -> str:
        params = parse_qs(urlparse(url).query)
        return params.get("id", ["unknown"])[0]