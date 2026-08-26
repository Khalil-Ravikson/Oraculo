from src.application.routing.command_builder import BaseCommand, register_command, CommandContext


@register_command(trigger="L", is_admin=True)
class CmdClearCache(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        # Prefixo real gravado pelo SemanticCache é "semcache:", não
        # "semantic_cache:" (bug encontrado na Fase 3.5: este comando nunca
        # apagou nada, silenciosamente).
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = ctx.r.scan(cursor, match="semcache:*", count=500)
            if keys:
                ctx.r.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        return f"🗑️ Cache limpo: {deleted} entradas removidas."
