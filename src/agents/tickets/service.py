"""
src/agents/tickets/service.py
================================
Ex handlers de `application/workers/worker_action.py` (Fase 6 do
PLANO_REFATORACAO_SUPERVISOR.md, seção 2.6) — decisão de qual ação
administrativa tomar (atualizar e-mail, abrir chamado GLPI, enviar e-mail).
SQL cru migrou para `capabilities/persistence/ticket_repository.py`.

ACHADO desta fase: nada no caminho de produção hoje despacha
`dispatch("action", event)` — o Supervisor mapeia a rota "CRUD" para o
worker "crud_confirm" (`router/supervisor.py::_HINTS`), que também não tem
implementação (`@register("crud_confirm")` não existe em lugar nenhum). Ou
seja, o fluxo CRUD/action está registrado na infraestrutura (Celery conhece
a task, o WorkerRegistry tem a fila mapeada) mas não é alcançado por nenhuma
decisão de roteamento viva hoje. Isso é um problema de PRODUTO pré-existente
(fluxo CRUD incompleto), não introduzido por este refactor — fica fora do
escopo desta fase corrigir o roteamento; só a estrutura de arquivos foi
reorganizada, mantendo `worker_action` chamável exatamente como antes.
"""
from __future__ import annotations

import logging

from src.agents.base import AgentEnabledMixin

logger = logging.getLogger(__name__)


class TicketService:
    async def atualizar_email(self, matricula: str, novo_email: str) -> dict:
        from src.capabilities.persistence.ticket_repository import atualizar_email_por_matricula
        if not matricula or not novo_email:
            raise ValueError("matricula e novo_email obrigatórios")
        await atualizar_email_por_matricula(matricula, novo_email)
        return {"mensagem": f"✅ E-mail atualizado para {novo_email}"}

    async def abrir_chamado_glpi(self, titulo: str, user_id: str = "") -> dict:
        # Integre com GLPI real via HTTP quando disponível
        logger.info("📋 [GLPI] Chamado: '%s' | user=%s", titulo, user_id)
        return {"mensagem": f"✅ Chamado '{titulo}' registrado. Acompanhe pelo GLPI."}

    async def enviar_email(self, destinatario: str, assunto: str = "", corpo: str = "") -> dict:
        if not destinatario:
            raise ValueError("destinatario obrigatório")
        try:
            from src.infrastructure.services.domain_service.gmail_service import get_gmail_service
            svc = get_gmail_service()
            result = await svc.send(destinatario, assunto, corpo)
            return {"mensagem": result}
        except Exception as e:
            raise RuntimeError(f"Email falhou: {e}")


class TicketAgent(AgentEnabledMixin):
    """
    BaseAgent mínimo (ver agents/base.py e agents/registry.py, Fase 2).
    Registrado no Agent Registry; ainda não é caminho quente de produção
    pelo motivo descrito no docstring do módulo (rota CRUD não chega a
    despachar "action" hoje).
    """
    name = "tickets"
    description = "Ações administrativas: atualizar dados cadastrais, abrir chamado GLPI, enviar e-mail."
    permissions: list[str] = []

    def __init__(self) -> None:
        self._service = TicketService()

    async def execute(self, context):
        from src.agents.base import AgentResponse

        conversation = context.conversation or {}
        acao = conversation.get("acao", "")
        args = conversation.get("args", {})

        try:
            if acao == "update_student_email":
                resultado = await self._service.atualizar_email(args.get("matricula", ""), args.get("novo_email", ""))
            elif acao == "abrir_chamado_glpi":
                resultado = await self._service.abrir_chamado_glpi(args.get("titulo", ""), args.get("user_id", ""))
            elif acao == "enviar_email":
                resultado = await self._service.enviar_email(args.get("destinatario", ""), args.get("assunto", ""), args.get("corpo", ""))
            else:
                return AgentResponse(answer=f"Ação desconhecida: '{acao}'.", status="error")
        except Exception as e:
            return AgentResponse(answer=str(e), status="error")

        return AgentResponse(answer=resultado.get("mensagem", ""))
