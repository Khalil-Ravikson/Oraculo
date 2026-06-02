import json
from src.application.routing.command_builder import BaseCommand, register_command, CommandContext


@register_command(trigger="CR", is_admin=True)
class CmdGetCRAGScore(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        raw = ctx.r.lrange("monitor:logs", 0, 0)
        if not raw:
            return "ℹ️ Nenhuma métrica disponível ainda."
        last = json.loads(raw[0])
        return (
            f"📊 *Última iteração RAG:*\n"
            f"• CRAG Score: `{last.get('crag_score', '?'):.3f}`\n"
            f"• Rota: `{last.get('route', '?')}`\n"
            f"• Tokens: `{last.get('tokens', '?')}`\n"
            f"• Latência: `{last.get('total_ms', '?')}ms`"
        )
