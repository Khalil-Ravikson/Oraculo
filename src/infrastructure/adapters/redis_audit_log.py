# src/infrastructure/adapters/redis_audit_log.py
"""
Adapter: IAuditLog implementado com Redis.

ESTRUTURA NO REDIS:
  audit:log → Lista LPUSH (mais recente primeiro)
  TTL: 90 dias
  Máximo: 10.000 entradas (ltrim)

FORMATO DA ENTRADA:
  {
    "ts":      "2026-04-02T14:30:00",
    "admin":   "5598999990001",
    "action":  "ban_user",
    "target":  "5598888880002",
    "payload": {"motivo": "spam"},
    "result":  "ok"
  }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from src.domain.ports.audit_log import IAuditLog

logger = logging.getLogger(__name__)

_KEY        = "audit:log"
_MAX_ENTRIES = 10_000
_TTL_DIAS   = 90


class RedisAuditLog(IAuditLog):

    async def registar(
        self,
        admin_id:  str,
        action:    str,
        target:    str | None,
        payload:   dict[str, Any] | None,
        resultado: str,
    ) -> None:
        try:
            from src.infrastructure.redis_client import get_redis_text
            r     = get_redis_text()
            entry = json.dumps({
                "ts":      datetime.now().isoformat(),
                "admin":   admin_id,
                "action":  action,
                "target":  target,
                "payload": payload,
                "result":  resultado,
            }, ensure_ascii=False, default=str)
            r.lpush(_KEY, entry)
            r.ltrim(_KEY, 0, _MAX_ENTRIES - 1)
            r.expire(_KEY, 86400 * _TTL_DIAS)
        except Exception as e:
            logger.debug("⚠️  AuditLog falhou (não crítico): %s", e)

    async def listar(self, limit: int = 50, offset: int = 0) -> list[dict]:
        try:
            from src.infrastructure.redis_client import get_redis_text
            r   = get_redis_text()
            raw = r.lrange(_KEY, offset, offset + limit - 1)
            return [json.loads(e) for e in raw]
        except Exception as e:
            logger.debug("⚠️  AuditLog listar falhou: %s", e)
            return []