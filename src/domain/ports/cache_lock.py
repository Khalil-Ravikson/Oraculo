# src/domain/ports/cache_lock.py
from typing import Protocol

class ICacheLock(Protocol):
    """Contrato para lock distribuído — agnóstico de Redis."""

    async def is_locked(self, key: str) -> bool:
        """Verifica se a chave está travada."""
        ...

    async def acquire(self, key: str, timeout: int = 90) -> bool:
        """Adquire o lock. Retorna False se já está em uso."""
        ...

    async def release(self, key: str) -> None:
        """Libera o lock."""
        ...