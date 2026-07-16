import re
from src.application.routing.command_builder import BaseCommand, register_command, CommandContext


@register_command(trigger="C", is_admin=True)
class CmdRegisterAdmin(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        phone = re.sub(r"\D", "", ctx.text.split()[0]) if ctx.text else ""
        if len(phone) < 10:
            return "❌ Use: `$C 5598999999999`"
        try:
            from src.infrastructure.database.session import AsyncSessionLocal
            from sqlalchemy import text
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("UPDATE pessoas SET role='admin' WHERE telefone=:p"),
                    {"p": phone},
                )
                await db.commit()
            return f"✅ `{phone}` promovido a admin."
        except Exception as e:
            return f"❌ Erro: {e}"
