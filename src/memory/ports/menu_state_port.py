"""
src/memory/ports/menu_state_port.py
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class IMenuStateRepository(ABC):
    """
    Porta para persistência do estado de navegação do menu.
    Separada da WorkingMemory porque tem TTL e ciclo de vida diferentes.
    """

    @abstractmethod
    def get(self, user_id: str) -> str:
        """Retorna o estado atual do menu. Default: 'MAIN'."""

    @abstractmethod
    def set(self, user_id: str, state: str) -> None:
        """Persiste o novo estado."""

    @abstractmethod
    def clear(self, user_id: str) -> None:
        """Reseta para 'MAIN'."""