import json
import time
from src.application.routing.command_builder import BaseCommand, register_command, CommandContext


@register_command(trigger="feedback", is_admin=False)
class CmdFeedback(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        try:
            rating = int(ctx.text)
        except ValueError:
            return "❌ Avaliação inválida. Use !1 a !5."

        if not 1 <= rating <= 5:
            return "❌ Avaliação inválida. Use !1 a !5."

        # Busca última interação do usuário nos logs do Redis
        raw = ctx.r.lrange("monitor:logs", 0, 49)
        last_interaction = None
        for entry in raw:
            data = json.loads(entry)
            if data.get("user_id", "").endswith(ctx.sender_jid[-8:]):
                last_interaction = data
                break

        if not last_interaction:
            return "ℹ️ Nenhuma interação recente encontrada para avaliar."

        feedback = {
            "ts":        time.time(),
            "sender":    ctx.sender_jid,
            "rating":    rating,
            "route":     last_interaction.get("route"),
            "crag":      last_interaction.get("crag_score"),
        }
        ctx.r.lpush("feedback:ratings", json.dumps(feedback))
        ctx.r.ltrim("feedback:ratings", 0, 999)

        stars = "⭐" * rating
        messages = {
            1: "Obrigado pelo feedback. Vamos melhorar! 🙏",
            2: "Entendido. Trabalhando para melhorar.",
            3: "Obrigado! Resposta razoável.",
            4: "Fico feliz que tenha ajudado! 😊",
            5: "Perfeito! Muito obrigado pelo 5 estrelas! 🌟",
        }
        return f"{stars} Avaliação registrada! {messages[rating]}"
