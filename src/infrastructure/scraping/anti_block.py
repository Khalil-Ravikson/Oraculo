"""
src/infrastructure/scraping/anti_block.py
-------------------------------------------
Sistema anti-bloqueio: rotação de User-Agent, proxies e delays.

DESIGN: injetado no BaseScraper — scrapers não conhecem a estratégia.
Substituir por NoOpAntiBlock em testes = zero delays nos testes.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]

_ACCEPT_LANGUAGES = [
    "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "pt-BR,pt;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,pt;q=0.8",
]


@dataclass
class AntiBlockConfig:
    min_delay_s: float = 0.5
    max_delay_s: float = 2.5
    proxies: list[str] = field(default_factory=list)
    rotate_user_agent: bool = True
    rotate_accept_language: bool = True
    extra_headers: dict = field(default_factory=dict)


class AntiBlockManager:
    """
    Gerencia headers rotativos, proxies e delays para evitar bloqueios.
    """

    def __init__(self, config: AntiBlockConfig | None = None):
        self._cfg = config or AntiBlockConfig()
        self._proxy_idx = 0

    def get_headers(self) -> dict:
        """Retorna headers rotativos para a próxima requisição."""
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        }

        if self._cfg.rotate_user_agent:
            headers["User-Agent"] = random.choice(_USER_AGENTS)

        if self._cfg.rotate_accept_language:
            headers["Accept-Language"] = random.choice(_ACCEPT_LANGUAGES)

        headers.update(self._cfg.extra_headers)
        return headers

    def get_proxy(self) -> str | None:
        """Rotaciona proxies em round-robin."""
        if not self._cfg.proxies:
            return None
        proxy = self._cfg.proxies[self._proxy_idx % len(self._cfg.proxies)]
        self._proxy_idx += 1
        return proxy

    async def apply_delay(self) -> None:
        """Aplica delay randômico para parecer comportamento humano."""
        if self._cfg.min_delay_s > 0:
            delay = random.uniform(self._cfg.min_delay_s, self._cfg.max_delay_s)
            await asyncio.sleep(delay)


class NoOpAntiBlock(AntiBlockManager):
    """Implementação de no-op para testes — sem delays, headers fixos."""

    def __init__(self):
        super().__init__(AntiBlockConfig(min_delay_s=0.0, max_delay_s=0.0))

    async def apply_delay(self) -> None:
        pass  # zero delay em testes

    def get_headers(self) -> dict:
        return {"User-Agent": "TestBot/1.0"}