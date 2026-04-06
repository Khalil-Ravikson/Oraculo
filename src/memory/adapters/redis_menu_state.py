"""
src/memory/adapters/redis_menu_state.py
"""
from __future__ import annotations
import logging
from typing import Any
from ..ports.menu_state_port import IMenuStateRepository

logger = logging.getLogger(__name__)
_PREFIX = "menu_state:"
_TTL = 1800


class RedisMenuStateRepository(IMenuStateRepository):
    def __init__(self, redis_client: Any):
        self._r = redis_client

    def get(self, user_id: str) -> str:
        try:
            val = self._r.get(f"{_PREFIX}{user_id}")
            if val:
                return val if isinstance(val, str) else val.decode()
        except Exception:
            pass
        return "MAIN"

    def set(self, user_id: str, state: str) -> None:
        try:
            self._r.setex(f"{_PREFIX}{user_id}", _TTL, state)
        except Exception as e:
            logger.warning("⚠️  MenuState.set [%s]: %s", user_id, e)

    def clear(self, user_id: str) -> None:
        try:
            self._r.delete(f"{_PREFIX}{user_id}")
        except Exception:
            pass