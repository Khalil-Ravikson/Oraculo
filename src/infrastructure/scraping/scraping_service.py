"""
src/infrastructure/scraping/scraping_service.py
-------------------------------------------------
Orquestrador do sistema de scraping.

RESPONSABILIDADES:
  1. Registry de scrapers (plugável por domínio)
  2. Roteamento URL → scraper correto
  3. Execução paralela com asyncio.gather
  4. Ingestão automática no RAG após scraping
  5. Publicação na fila RabbitMQ (modo async)

DESIGN (Registry Pattern):
  Adicionar novo scraper = register(MinhaClasse())
  O service roteia automaticamente pela URL.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base_scraper import BaseScraper, ScrapeRequest, ScrapeResult, ScrapedDocument

logger = logging.getLogger(__name__)


class ScrapingService:
    """
    Orquestrador central de scraping.
    Recebe scrapers por registro, roteia por URL, executa em paralelo.
    """

    def __init__(
        self,
        queue: Any | None = None,           # ScrapeQueueProducer | InMemoryQueue
        rag_ingestion: Any | None = None,   # IngestionPipeline (opcional)
        max_concurrency: int = 5,
    ):
        self._scrapers: list[BaseScraper] = []
        self._fallback: BaseScraper | None = None
        self._queue = queue
        self._rag = rag_ingestion
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def register(self, scraper: BaseScraper, fallback: bool = False) -> "ScrapingService":
        """
        Registra um scraper. Retorna self para encadeamento fluente.
        scraper(fallback=True) é usado quando nenhum outro aceita a URL.
        """
        if fallback:
            self._fallback = scraper
        else:
            self._scrapers.append(scraper)
        logger.debug("✅ Scraper registrado: %s", type(scraper).__name__)
        return self

    def _resolve(self, url: str) -> BaseScraper | None:
        """Encontra o scraper adequado para a URL (primeiro que pode processar)."""
        for scraper in self._scrapers:
            if scraper.can_handle(url):
                return scraper
        return self._fallback

    # ─────────────────────────────────────────────────────────────────────────
    # API principal
    # ─────────────────────────────────────────────────────────────────────────

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Scrapa uma URL única de forma assíncrona."""
        scraper = self._resolve(request.url)
        if not scraper:
            return ScrapeResult.failure(
                f"Nenhum scraper disponível para: {request.url}",
                request.request_id,
            )

        async with self._semaphore:
            result = await scraper.scrape(request)

        # 1. Tudo precisa estar garantido que deu sucesso!
        if result.ok and result.document:
            
            # Injeta no RAG
            if self._rag:
                await self._ingest_to_rag(result.document)

            # 2. Captura os novos links descobertos
            novos_links = result.document.metadata.get("links_descobertos", [])
            
            if novos_links and self._queue:
                from .base_scraper import ScrapeRequest
                from src.infrastructure.redis_client import get_redis_text
                
                r = get_redis_text()
                
                for link in novos_links:
                    # 3. Controle Distribuído: Usamos o Redis para garantir que 
                    # nenhum worker coloque a mesma URL na fila duas vezes em 24h
                    lock_key = f"crawler:queued:{link}"
                    
                    if not r.get(lock_key):
                        # Marca como visitado/enfileirado (TTL de 24 horas)
                        r.setex(lock_key, 86400, "1")
                        
                        # 4. Enfileira de forma recursiva com o mesmo doc_type
                        await self.scrape_and_queue(
                            ScrapeRequest(
                                url=link, 
                                doc_type=request.doc_type, 
                                priority=8 # Prioridade mais baixa que ações do usuário
                            )
                        )

        return result

    async def scrape_batch(
        self,
        requests: list[ScrapeRequest],
        stop_on_error: bool = False,
    ) -> list[ScrapeResult]:
        """
        Scrapa múltiplas URLs em paralelo.
        stop_on_error=False: continua mesmo com falhas individuais.
        """
        tasks = [self.scrape(req) for req in requests]
        if stop_on_error:
            results = await asyncio.gather(*tasks)
        else:
            results = await asyncio.gather(*tasks, return_exceptions=False)
        return list(results)

    async def scrape_and_queue(self, request: ScrapeRequest) -> None:
        """Publica na fila para processamento assíncrono (não aguarda resultado)."""
        if not self._queue:
            raise RuntimeError("Fila não configurada. Use register_queue().")
        await self._queue.publish(request)
        logger.info("📤 URL enfileirada: %s", request.url)

    async def scrape_urls(
        self,
        urls: list[str],
        source_name: str = "",
        doc_type: str = "web",
        force_refresh: bool = False,
    ) -> list[ScrapeResult]:
        """Atalho para scraping de lista de URLs simples."""
        requests = [
            ScrapeRequest(
                url=url,
                source_name=source_name,
                doc_type=doc_type,
                force_refresh=force_refresh,
            )
            for url in urls
        ]
        return await self.scrape_batch(requests)

    async def _ingest_to_rag(self, document: ScrapedDocument) -> None:
        """
        Injeta documento scrapeado no pipeline RAG.
        Usa text chunking + embedding + Redis.
        """
        if not document.is_valid():
            return
        try:
            from src.rag.ingestion.chunker_factory import ChunkerFactory
            from src.rag.embeddings import get_embeddings
            from src.infrastructure.redis_client import salvar_chunk
            import hashlib

            chunker = ChunkerFactory.for_doc_type(document.doc_type)
            chunks = chunker.chunk(
                document.content,
                source=document.url,
                doc_type=document.doc_type,
            )

            if not chunks:
                return

            emb_model = get_embeddings()
            textos = [c.text for c in chunks]
            embeddings = await asyncio.to_thread(emb_model.embed_documents, textos)

            prefixo = document.context_label
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chunk_id = hashlib.md5(f"{document.url}:{i}".encode()).hexdigest()[:16]
                await asyncio.to_thread(
                    salvar_chunk,
                    chunk_id=chunk_id,
                    content=f"{prefixo}\n{chunk.text}",
                    source=document.url,
                    doc_type=document.doc_type,
                    embedding=emb,
                    chunk_index=i,
                    metadata={**chunk.metadata, "title": document.title, "scraped_at": document.scraped_at},
                )

            logger.info("📚 RAG: %d chunks ingeridos de %s", len(chunks), document.url)
        except Exception as e:
            logger.warning("⚠️  RAG ingestion falhou para %s: %s", document.url, e)

    def list_scrapers(self) -> list[str]:
        names = [type(s).__name__ for s in self._scrapers]
        if self._fallback:
            names.append(f"{type(self._fallback).__name__} (fallback)")
        return names


def build_default_scraping_service(
    redis_client: Any | None = None,
    amqp_url: str | None = None,
    ingest_to_rag: bool = True,
) -> ScrapingService:
    """
    Fábrica que monta o ScrapingService com todos os scrapers registrados.
    Configuração padrão para produção.
    """
    from .anti_block import AntiBlockConfig, AntiBlockManager
    from .cache import NoOpCache, ScraperCache
    from .retry import RetryConfig, RetryPolicy
    from .implementations.wikipedia_scraper import WikipediaScraper
    from .implementations.generic_scraper import GenericHTTPScraper, UEMAWikiScraper
    from .implementations.uema_wiki_scraper import UEMAWikiScraper
    # Componentes compartilhados
    anti_block = AntiBlockManager(AntiBlockConfig(min_delay_s=0.3, max_delay_s=1.5))
    retry = RetryPolicy(RetryConfig(max_attempts=3))
    cache = ScraperCache(redis_client) if redis_client else NoOpCache()
    
    def _mk(ScrapeClass: type) -> BaseScraper:
        return ScrapeClass(anti_block=anti_block, retry_policy=retry, cache=cache)

    # Fila
    queue = None
    if amqp_url:
        from .queue import ScrapeQueueProducer
        queue = ScrapeQueueProducer(amqp_url)

    service = ScrapingService(queue=queue, max_concurrency=5)
    service.register(_mk(WikipediaScraper))
    
    service.register(_mk(GenericHTTPScraper), fallback=True)
    service.register(UEMAWikiScraper(anti_block=anti_block, retry_policy=retry, cache=cache))
    return service