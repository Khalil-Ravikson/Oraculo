import logging
from src.application.routing.command_builder import BaseCommand, register_command, CommandContext

logger = logging.getLogger(__name__)


@register_command(trigger="M", is_admin=True)
class CmdMaintenanceOn(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        ctx.r.set("admin:gemini_blocked", "1")
        logger.warning("⚠️  MANUTENÇÃO ATIVADA por %s", ctx.sender_jid)
        return "🔧 Modo manutenção *ATIVADO*. Alunos verão mensagem de indisponibilidade."


@register_command(trigger="MO", is_admin=True)
class CmdMaintenanceOff(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        ctx.r.delete("admin:gemini_blocked")
        return "✅ Modo manutenção *DESATIVADO*."
