"""
src/domain_services/tickets/service.py
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


logger = logging.getLogger(__name__)


class TicketService:
    async def atualizar_email(self, matricula: str, novo_email: str) -> dict:
        from src.capabilities.persistence.ticket_repository import atualizar_email_por_matricula
        if not matricula or not novo_email:
            raise ValueError("matricula e novo_email obrigatórios")
        await atualizar_email_por_matricula(matricula, novo_email)
        return {"mensagem": f"✅ E-mail atualizado para {novo_email}"}

    async def atualizar_meu_email(self, telefone: str, novo_email: str) -> dict:
        """Versão exposta ao usuário via !atualizaremail — escopo pelo próprio telefone."""
        from src.capabilities.persistence.ticket_repository import atualizar_email_por_telefone
        if not telefone or not novo_email:
            raise ValueError("telefone e novo_email obrigatórios")
        ok = await atualizar_email_por_telefone(telefone, novo_email)
        if not ok:
            return {"mensagem": "❌ Não encontrei seu cadastro para atualizar o e-mail."}
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
