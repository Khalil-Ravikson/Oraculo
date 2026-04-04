# src/domain/ports/audit_log.py
"""
Port para Auditoria de Ações Admin — agnóstico de implementação.
"""
from __future__ import annotations
from typing import Protocol, Any


class IAuditLog(Protocol):
    """
    Contrato para o sistema de auditoria das ações admin.
    Toda ação administrativa deve ser registada aqui.
    """

    async def registar(
        self,
        admin_id:  str,
        action:    str,
        target:    str | None,
        payload:   dict[str, Any] | None,
        resultado: str,
    ) -> None:
        """
        Regista uma ação admin.

        Args:
            admin_id:  Número do admin (ou username do portal)
            action:    Nome da ação ("ban_user", "clear_cache", "change_prompt", ...)
            target:    Alvo da ação (user_id, cache_key, ...)
            payload:   Dados adicionais da ação
            resultado: "ok" | "erro" | "cancelado"
        """
        ...

    async def listar(
        self,
        limit:  int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Retorna as últimas N entradas do log de auditoria."""
        ...