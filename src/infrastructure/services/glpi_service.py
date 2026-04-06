"""
src/infrastructure/services/glpi_service.py
---------------------------------------------
Implementação do IGLPIService.

Modo MOCK (DEV_MODE=True) retorna dados simulados sem chamar API.
Modo REAL faz HTTP para a API do GLPI.

COMO ADICIONAR AUTENTICAÇÃO REAL:
  1. Setar GLPI_URL, GLPI_APP_TOKEN, GLPI_USER_TOKEN no .env
  2. A implementação real usa httpx com retry via tenacity
"""
from __future__ import annotations

import logging
import random
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.domain.ports.tool_ports import IGLPIService, ToolResult

logger = logging.getLogger(__name__)


class MockGLPIService(IGLPIService):
    """Implementação mock do GLPI para DEV_MODE."""

    async def abrir_chamado(self, titulo, descricao, local="Não informado", urgencia="media", user_email="") -> ToolResult:
        fake_id = random.randint(5000, 9999)
        logger.info("🛠️  [GLPI MOCK] Abrindo chamado: %s", titulo)
        return ToolResult.success(
            message=f"Chamado #{fake_id} aberto com sucesso.",
            data={
                "id": fake_id,
                "titulo": titulo,
                "status": "novo",
                "urgencia": urgencia,
                "link": f"https://glpi.uema.br/front/ticket.form.php?id={fake_id}",
            },
        )

    async def consultar_fila(self, user_email="") -> ToolResult:
        return ToolResult.success(
            message="Fila consultada.",
            data={
                "total": 2,
                "chamados": [
                    {"id": 101, "titulo": "Sem internet", "status": "Aberto"},
                    {"id": 102, "titulo": "Impressora quebrada", "status": "Em andamento"},
                ],
            },
        )

    async def atualizar_chamado(self, chamado_id, status) -> ToolResult:
        return ToolResult.success(message=f"Chamado #{chamado_id} atualizado para '{status}'.")


class RealGLPIService(IGLPIService):
    """Implementação real do GLPI via API REST."""

    def __init__(self, base_url: str, app_token: str, user_token: str):
        self._base = base_url.rstrip("/")
        self._app_token = app_token
        self._user_token = user_token
        self._session_token: str | None = None

    async def _get_session(self) -> str:
        if self._session_token:
            return self._session_token
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base}/apirest.php/initSession",
                headers={
                    "App-Token": self._app_token,
                    "Authorization": f"user_token {self._user_token}",
                },
            )
            r.raise_for_status()
            self._session_token = r.json()["session_token"]
        return self._session_token

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
    )
    async def abrir_chamado(self, titulo, descricao, local="Não informado", urgencia="media", user_email="") -> ToolResult:
        urgencia_map = {"baixa": 1, "media": 3, "alta": 5}
        try:
            session = await self._get_session()
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{self._base}/apirest.php/Ticket",
                    headers={"Session-Token": session, "App-Token": self._app_token},
                    json={
                        "input": {
                            "name": titulo,
                            "content": f"{descricao}\n\nLocal: {local}",
                            "urgency": urgencia_map.get(urgencia, 3),
                            "type": 1,  # 1=Incident
                        }
                    },
                )
                r.raise_for_status()
                ticket_id = r.json().get("id", 0)
                return ToolResult.success(
                    message=f"Chamado #{ticket_id} aberto.",
                    data={"id": ticket_id, "link": f"{self._base}/front/ticket.form.php?id={ticket_id}"},
                )
        except Exception as e:
            logger.error("❌ GLPI.abrir_chamado: %s", e)
            return ToolResult.failure(str(e))

    async def consultar_fila(self, user_email="") -> ToolResult:
        return ToolResult.success(message="Consulta não implementada para produção.", data={"chamados": []})

    async def atualizar_chamado(self, chamado_id, status) -> ToolResult:
        return ToolResult.failure("Atualização não implementada.")