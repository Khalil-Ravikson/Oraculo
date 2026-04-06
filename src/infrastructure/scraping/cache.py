"""
src/infrastructure/scraping/cache.py
--------------------------------------
Cache Redis para scrapers — TTL configurável, dedup por conteúdo.

EVITA:
  - Scraping repetido da mesma URL no mesmo período
  - Sobrecarga no servidor alvo
  - Custo de rede desnecessário

ESTRATÉGIA DE INVALIDAÇÃO:
  Por TTL (padrão: 24h para web, 1h para conteúdo dinâmico)
  Por força (force_refresh=True no ScrapeRequest)
  Por prefixo (admin pode limpar todos os caches de um domínio)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .base_scraper import ScrapedDocument

logger = logging.getLogger(__name__)

_PREFIX = "scrape:cache:"
_TTL_WEB = 86400        # 24h para páginas web estáticas
_TTL_DYNAMIC = 3600     # 1h para conteúdo dinâmico


def _serialize(doc: ScrapedDocument) -> str:
    return json.dumps({
        "url": doc.url,
        "title": doc.title,
        "content": doc.content,
        "source_name": doc.source_name,
        "doc_type": doc.doc_type,
        "metadata": doc.metadata,
        "scraped_at": doc.scraped_at,
        "language": doc.language,
    }, ensure_ascii=False)


def _deserialize(raw: str) -> ScrapedDocument:
    d = json.loads(raw)
    return ScrapedDocument(**d)


class ScraperCache:
    """Cache Redis para resultados de scraping."""

    def __init__(self, redis_client: Any, default_ttl: int = _TTL_WEB):
        self._r = redis_client
        self._ttl = default_ttl

    def _key(self, url: str) -> str:
        import hashlib
        url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
        return f"{_PREFIX}{url_hash}"

    async def get(self, url: str) -> ScrapedDocument | None:
        try:
            import asyncio
            raw = await asyncio.to_thread(self._r.get, self._key(url))
            if raw:
                doc = _deserialize(raw if isinstance(raw, str) else raw.decode())
                logger.debug("🗃️  Cache hit: %s", url)
                return doc
        except Exception as e:
            logger.warning("⚠️  Cache.get [%s]: %s", url, e)
        return None

    async def set(self, url: str, doc: ScrapedDocument, ttl: int | None = None) -> None:
        try:
            import asyncio
            await asyncio.to_thread(
                self._r.setex,
                self._key(url),
                ttl or self._ttl,
                _serialize(doc),
            )
        except Exception as e:
            logger.warning("⚠️  Cache.set [%s]: %s", url, e)

    async def invalidate(self, url: str) -> None:
        try:
            import asyncio
            await asyncio.to_thread(self._r.delete, self._key(url))
        except Exception:
            pass

    async def clear_prefix(self, domain: str) -> int:
        """Remove todos os caches de um domínio."""
        try:
            import asyncio
            _, keys = await asyncio.to_thread(
                self._r.scan, 0, match=f"{_PREFIX}*", count=500
            )
            # Filtra por domínio verificando a URL original (não é eficiente mas é correto)
            count = 0
            for key in keys:
                try:
                    raw = await asyncio.to_thread(self._r.get, key)
                    if raw and domain in raw.decode():
                        await asyncio.to_thread(self._r.delete, key)
                        count += 1
                except Exception:
                    pass
            return count
        except Exception:
            return 0


class NoOpCache(ScraperCache):
    """Cache de no-op para testes — nunca armazena nem retorna."""

    def __init__(self):
        pass  # sem Redis

    async def get(self, url: str) -> ScrapedDocument | None:
        return None

    async def set(self, url: str, doc: ScrapedDocument, ttl: int | None = None) -> None:
        pass

    async def invalidate(self, url: str) -> None:
        pass