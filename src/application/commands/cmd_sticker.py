from src.application.routing.command_builder import BaseCommand, register_command, CommandContext


@register_command(trigger="sticker", is_admin=False)
class CmdSticker(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        return "🖼️ Criação de stickers em desenvolvimento. Em breve!"
