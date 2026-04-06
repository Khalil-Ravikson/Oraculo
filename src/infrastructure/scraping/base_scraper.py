"""
src/infrastructure/scraping/base_scraper.py
---------------------------------------------
Classe base abstrata para todos os scrapers do Oráculo.

DESIGN (Open/Closed Principle):
  BaseScraper define o contrato e o pipeline fixo:
    fetch() → parse() → clean() → to_chunks()

  Subclasses sobrescrevem APENAS fetch() e parse().
  clean() e to_chunks() são finais (comportamento padrão herdado).

  Adicionar novo site = criar nova subclasse.
  ZERO mudança no orchestrator, nas tools ou no graph.

RESULTADO PADRONIZADO:
  ScrapedDocument é imutável e já vem preparado para:
    - Chunking (texto estruturado com metadata)
    - Embedding (texto limpo sem HTML)
    - Vector DB (source, url, timestamp)
    - RAG (context_label para prefixo anti-alucinação)
"""
from __future__ import annotations

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScrapedDocument:
    """
    Documento scrapeado — imutável e pronto para ingestão no RAG.
    frozen=True garante que não é mutado acidentalmente no pipeline.
    """
    url: str
    title: str
    content: str                    # texto limpo, sem HTML
    source_name: str                # "wikipedia", "uema_wiki", etc.
    doc_type: str = "web"          # "web" | "wiki" | "edital" | "manual"
    metadata: dict = field(default_factory=dict)
    scraped_at: float = field(default_factory=time.time)
    language: str = "pt"

    @property
    def content_id(self) -> str:
        """Hash único do conteúdo — para dedup no Redis."""
        return hashlib.md5(f"{self.url}:{self.content[:200]}".encode()).hexdigest()[:16]

    @property
    def context_label(self) -> str:
        """Prefixo hierárquico anti-alucinação para chunks RAG."""
        return f"[{self.source_name.upper()} | {self.doc_type}]"

    @property
    def word_count(self) -> int:
        return len(self.content.split())

    def is_valid(self) -> bool:
        return bool(self.url and self.content and len(self.content.strip()) > 100)


@dataclass
class ScrapeRequest:
    """Pedido de scraping — enviado para a fila ou chamado diretamente."""
    url: str
    source_name: str = ""
    doc_type: str = "web"
    force_refresh: bool = False     # ignora cache Redis
    priority: int = 5               # 1 (alta) a 10 (baixa)
    metadata: dict = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: hashlib.md5(str(time.time()).encode()).hexdigest()[:8])


@dataclass
class ScrapeResult:
    """Resultado de uma operação de scraping."""
    ok: bool
    document: ScrapedDocument | None = None
    error: str = ""
    from_cache: bool = False
    elapsed_ms: int = 0
    request_id: str = ""

    @classmethod
    def success(cls, doc: ScrapedDocument, from_cache: bool = False, elapsed_ms: int = 0) -> "ScrapeResult":
        return cls(ok=True, document=doc, from_cache=from_cache, elapsed_ms=elapsed_ms)

    @classmethod
    def failure(cls, error: str, request_id: str = "") -> "ScrapeResult":
        return cls(ok=False, error=error, request_id=request_id)


class BaseScraper(ABC):
    """
    Classe base para todos os scrapers.

    TEMPLATE METHOD PATTERN:
      scrape() é o método público — chama fetch() e parse() em ordem.
      Subclasses implementam fetch() e parse().
      O pipeline (cache check → fetch → parse → clean → validate) é fixo.

    INJEÇÃO DE DEPENDÊNCIA:
      anti_block, retry_policy e cache são injetados no construtor.
      Isso permite testar scrapers sem Redis, sem rede, sem delays.
    """

    def __init__(
        self,
        anti_block: Any | None = None,      # AntiBlockManager
        retry_policy: Any | None = None,    # RetryPolicy
        cache: Any | None = None,           # ScraperCache
        timeout: float = 15.0,
    ):
        self._anti_block = anti_block
        self._retry = retry_policy
        self._cache = cache
        self._timeout = timeout

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Identificador único do scraper (ex: 'wikipedia', 'uema_wiki')."""

    @property
    @abstractmethod
    def supported_domains(self) -> list[str]:
        """Domínios que este scraper sabe processar."""

    @abstractmethod
    async def fetch(self, url: str, headers: dict) -> str:
        """
        Faz a requisição HTTP e retorna o conteúdo bruto (HTML/texto).
        Subclasses definem aqui como obter o conteúdo (httpx, playwright, etc.).
        """

    @abstractmethod
    def parse(self, raw_content: str, url: str) -> ScrapedDocument:
        """
        Extrai texto estruturado do conteúdo bruto.
        Recebe HTML/texto, retorna ScrapedDocument limpo.
        """

    # ─────────────────────────────────────────────────────────────────────────
    # Template Method — pipeline fixo
    # ─────────────────────────────────────────────────────────────────────────

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """
        Pipeline completo de scraping.
        Não sobrescrever — adiciona cache, retry e anti-block automaticamente.
        """
        t0 = time.monotonic()

        # 1. Cache check
        if self._cache and not request.force_refresh:
            cached = await self._cache.get(request.url)
            if cached:
                logger.debug("🗃️  Cache hit: %s", request.url)
                return ScrapeResult.success(
                    cached, from_cache=True,
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )

        # 2. Headers com anti-block
        headers = {}
        if self._anti_block:
            headers = self._anti_block.get_headers()
            await self._anti_block.apply_delay()

        # 3. Fetch com retry
        try:
            if self._retry:
                raw = await self._retry.execute(self.fetch, url=request.url, headers=headers)
            else:
                raw = await self.fetch(url=request.url, headers=headers)
        except Exception as e:
            logger.error("❌ Scraper [%s] fetch falhou: %s — %s", self.source_name, request.url, e)
            return ScrapeResult.failure(str(e), request.request_id)

        # 4. Parse
        try:
            document = self.parse(raw, request.url)
        except Exception as e:
            logger.error("❌ Scraper [%s] parse falhou: %s — %s", self.source_name, request.url, e)
            return ScrapeResult.failure(f"Parse error: {e}", request.request_id)

        # 5. Validação
        if not document.is_valid():
            return ScrapeResult.failure("Documento inválido (conteúdo muito curto)", request.request_id)

        # 6. Salva no cache
        if self._cache:
            await self._cache.set(request.url, document)

        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info("✅ Scraped [%s]: %s | %d words | %dms",
                    self.source_name, request.url, document.word_count, elapsed)

        return ScrapeResult.success(document, elapsed_ms=elapsed)

    def can_handle(self, url: str) -> bool:
        """Verifica se este scraper pode processar a URL."""
        return any(domain in url for domain in self.supported_domains)