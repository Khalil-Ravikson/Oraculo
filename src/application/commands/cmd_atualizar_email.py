"""
"!atualizaremail seu@email.com — atualiza o e-mail do próprio cadastro."

Repõe o antigo agente "tickets" (dormente em produção — a rota CRUD nunca
chegava a despachar `dispatch("action", ...)`) por uma única ação real e
simples de mostrar agora. O resto do TicketService (chamado GLPI fake,
envio de e-mail via Gmail sem credenciais configuradas) fica fora de uso
por enquanto.
"""
import re
from src.application.routing.command_builder import BaseCommand, register_command, CommandContext

_RE_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")


@register_command(trigger="atualizaremail", is_admin=False)
class CmdAtualizarEmail(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        novo_email = ctx.text.strip()
        if not novo_email or not _RE_EMAIL.match(novo_email):
            return "❌ Use: !atualizaremail seu@email.com"

        from src.capabilities.persistence.agent_config import is_agent_enabled
        if not is_agent_enabled(ctx.r, "tickets"):
            return "🚧 Essa função está temporariamente desativada."

        from src.agents.tickets.service import TicketService
        resultado = await TicketService().atualizar_meu_email(ctx.sender_jid, novo_email)
        return resultado["mensagem"]
