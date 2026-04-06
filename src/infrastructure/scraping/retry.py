"""
src/infrastructure/scraping/retry.py
--------------------------------------
Política de retry com backoff exponencial para scrapers.
Injetada no BaseScraper — não acoplada à implementação.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    backoff_factor: float = 2.0
    retryable_status_codes: tuple = (429, 500, 502, 503, 504)


class RetryPolicy:
    """
    Executa uma coroutine com retry e backoff exponencial.
    Compatível com qualquer função async — não depende de tenacity.
    """

    def __init__(self, config: RetryConfig | None = None):
        self._cfg = config or RetryConfig()

    async def execute(self, coro_fn, **kwargs):
        """
        Executa coro_fn(**kwargs) com retry automático.

        ALGORITMO BACKOFF:
          tentativa 1 → falha → espera 1s
          tentativa 2 → falha → espera 2s
          tentativa 3 → falha → espera 4s (max 30s)
        """
        last_error = None
        delay = self._cfg.base_delay_s

        for attempt in range(1, self._cfg.max_attempts + 1):
            try:
                return await coro_fn(**kwargs)
            except Exception as e:
                last_error = e
                is_retryable = self._is_retryable(e)

                if attempt == self._cfg.max_attempts or not is_retryable:
                    logger.error(
                        "❌ Retry esgotado após %d tentativas: %s",
                        attempt, e,
                    )
                    raise

                logger.warning(
                    "⏳ Retry %d/%d em %.1fs — %s",
                    attempt, self._cfg.max_attempts, delay, e,
                )
                await asyncio.sleep(delay)
                delay = min(delay * self._cfg.backoff_factor, self._cfg.max_delay_s)

        raise last_error  # pragma: no cover

    def _is_retryable(self, error: Exception) -> bool:
        """Determina se o erro justifica retry."""
        err_str = str(error).lower()
        # HTTP status retryáveis
        for code in self._cfg.retryable_status_codes:
            if str(code) in err_str:
                return True
        # Erros de rede
        retryable_keywords = ("timeout", "connection", "connect", "read", "network", "ssl")
        return any(kw in err_str for kw in retryable_keywords)


class NoOpRetry(RetryPolicy):
    """Sem retry — para testes ou quando se quer falha imediata."""

    async def execute(self, coro_fn, **kwargs):
        return await coro_fn(**kwargs)