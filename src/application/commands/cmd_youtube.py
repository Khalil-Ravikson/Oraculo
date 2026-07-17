from src.application.routing.command_builder import BaseCommand, register_command, CommandContext


@register_command(trigger="ytb", is_admin=False)
class CmdYouTube(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        return "🎬 Integração com YouTube em desenvolvimento. Em breve!"
