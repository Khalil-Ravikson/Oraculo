"""
$voltar <jid> — tira uma sessão do modo "atendimento humano" (ADR 0008 Fase 2).

Quando o `human_handoff_node` encaminha uma conversa a um atendente, ele
grava `handoff:session:{jid}` (TTL 24h) e o bot para de responder aquela
sessão. Este comando apaga a chave antes do TTL, devolvendo a conversa ao
assistente automático. Sem argumento, lista as sessões em atendimento.
"""
from src.application.routing.command_builder import BaseCommand, CommandContext, register_command


@register_command(trigger="VOLTAR", is_admin=True)
class CmdVoltar(BaseCommand):
    async def execute(self, ctx: CommandContext) -> str:
        alvo = (ctx.text or "").strip()

        if not alvo:
            sessoes = []
            cursor = 0
            while True:
                cursor, keys = ctx.r.scan(cursor, match="handoff:session:*", count=200)
                sessoes.extend(k.split("handoff:session:", 1)[-1] for k in keys)
                if cursor == 0:
                    break
            if not sessoes:
                return "Nenhuma sessão em atendimento humano no momento."
            linhas = "\n".join(f"• {s}" for s in sorted(sessoes))
            return f"Sessões em atendimento humano ({len(sessoes)}):\n{linhas}\n\nUse `$voltar <jid>` para devolver ao bot."

        removidos = ctx.r.delete(f"handoff:session:{alvo}")
        if removidos:
            return f"✅ Sessão `{alvo}` devolvida ao assistente automático."
        return f"Sessão `{alvo}` não estava em atendimento humano (ou já expirou)."
