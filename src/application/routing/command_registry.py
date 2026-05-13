"""
CommandRegistry — padrão Command para $ e !

COMO ADICIONAR:
  1. Crie uma classe herdando AdminCommand ou PublicCommand
  2. Defina `trigger` (str) e implemente `execute()`
  3. Registre no final do arquivo
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)


# ─── Contrato ─────────────────────────────────────────────────────────────────

class CommandContext:
    """Carrega tudo que um command pode precisar — sem acesso global."""
    def __init__(
        self,
        sender_jid:  str,
        chat_id:     str,
        text:        str,         # argumento após o comando
        redis_text,               # cliente Redis decode_responses=True
        db_session=None,
    ):
        self.sender_jid = sender_jid
        self.chat_id    = chat_id
        self.text       = text
        self.r          = redis_text
        self.db         = db_session


class BaseCommand(ABC):
    trigger: str  # ex: "M", "CR", "5"

    @abstractmethod
    async def execute(self, ctx: CommandContext) -> str:
        """Retorna texto da resposta ou '' para silêncio."""


# ─── Admin Commands ($) ───────────────────────────────────────────────────────

class CmdMaintenanceOn(BaseCommand):
    trigger = "M"

    async def execute(self, ctx: CommandContext) -> str:
        ctx.r.set("admin:gemini_blocked", "1")
        logger.warning("⚠️  MANUTENÇÃO ATIVADA por %s", ctx.sender_jid)
        return "🔧 Modo manutenção *ATIVADO*. Alunos verão mensagem de indisponibilidade."


class CmdMaintenanceOff(BaseCommand):
    trigger = "MO"   # $MO = maintenance off

    async def execute(self, ctx: CommandContext) -> str:
        ctx.r.delete("admin:gemini_blocked")
        return "✅ Modo manutenção *DESATIVADO*."


class CmdSetSystemPrompt(BaseCommand):
    trigger = "S"

    async def execute(self, ctx: CommandContext) -> str:
        if not ctx.text or len(ctx.text) < 20:
            return "❌ Prompt muito curto (mínimo 20 chars). Use: `$S <novo prompt>`"
        ctx.r.set("admin:system_prompt", ctx.text)
        return f"✅ System prompt atualizado ({len(ctx.text)} chars)."


class CmdClearCache(BaseCommand):
    trigger = "L"

    async def execute(self, ctx: CommandContext) -> str:
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = ctx.r.scan(cursor, match="semantic_cache:*", count=500)
            if keys:
                ctx.r.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        return f"🗑️ Cache limpo: {deleted} entradas removidas."


class CmdGetCRAGScore(BaseCommand):
    trigger = "CR"

    async def execute(self, ctx: CommandContext) -> str:
        import json
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


class CmdRegisterAdmin(BaseCommand):
    """$C <numero> — cadastra novo usuário como admin."""
    trigger = "C"

    async def execute(self, ctx: CommandContext) -> str:
        import re
        phone = re.sub(r"\D", "", ctx.text.split()[0]) if ctx.text else ""
        if len(phone) < 10:
            return "❌ Use: `$C 5598999999999`"
        # Exemplo de upsert — adapte ao seu UserUseCase
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


# ─── Public Commands (!) ──────────────────────────────────────────────────────

class CmdFeedback(BaseCommand):
    """!1 a !5 — avalia a última resposta da LLM."""
    trigger = "FEEDBACK"   # mapeado pelo registry para 1..5

    async def execute(self, ctx: CommandContext) -> str:
        rating = int(ctx.text)  # 1-5, injetado pelo registry
        if not 1 <= rating <= 5:
            return "❌ Avaliação inválida. Use !1 a !5."

        import json, time
        from src.infrastructure.redis_client import get_redis_text

        r = ctx.r
        # Busca última interação do usuário nos logs
        raw = r.lrange("monitor:logs", 0, 49)
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
        r.lpush("feedback:ratings", json.dumps(feedback))
        r.ltrim("feedback:ratings", 0, 999)

        stars = "⭐" * rating
        messages = {
            1: "Obrigado pelo feedback. Vamos melhorar! 🙏",
            2: "Entendido. Trabalhando para melhorar.",
            3: "Obrigado! Resposta razoável.",
            4: "Fico feliz que tenha ajudado! 😊",
            5: "Perfeito! Muito obrigado pelo 5 estrelas! 🌟",
        }
        return f"{stars} Avaliação registrada! {messages[rating]}"


class CmdYouTube(BaseCommand):
    """!ytb <url> — placeholder para integração futura."""
    trigger = "ytb"

    async def execute(self, ctx: CommandContext) -> str:
        return "🎬 Integração com YouTube em desenvolvimento. Em breve!"


class CmdSticker(BaseCommand):
    """!sticker — placeholder para integração futura."""
    trigger = "sticker"

    async def execute(self, ctx: CommandContext) -> str:
        return "🖼️ Criação de stickers em desenvolvimento. Em breve!"


# ─── Registry ─────────────────────────────────────────────────────────────────

_ADMIN_COMMANDS: dict[str, BaseCommand] = {}
_PUBLIC_COMMANDS: dict[str, BaseCommand] = {}


def _register_admin(*cmds: BaseCommand):
    for cmd in cmds:
        _ADMIN_COMMANDS[cmd.trigger] = cmd


def _register_public(*cmds: BaseCommand):
    for cmd in cmds:
        _PUBLIC_COMMANDS[cmd.trigger] = cmd


_register_admin(
    CmdMaintenanceOn(),
    CmdMaintenanceOff(),
    CmdSetSystemPrompt(),
    CmdClearCache(),
    CmdGetCRAGScore(),
    CmdRegisterAdmin(),
)

_feedback_cmd = CmdFeedback()
_register_public(
    _feedback_cmd,
    CmdYouTube(),
    CmdSticker(),
)
# Mapeia !1 a !5 para o mesmo handler de feedback
for _i in range(1, 6):
    _PUBLIC_COMMANDS[str(_i)] = _feedback_cmd


async def dispatch_admin(trigger: str, ctx: CommandContext) -> str:
    cmd = _ADMIN_COMMANDS.get(trigger.upper())
    if not cmd:
        return f"❓ Comando `${trigger}` não reconhecido. Disponíveis: {list(_ADMIN_COMMANDS.keys())}"
    # Injeta rating para feedback se necessário
    if trigger in ("1","2","3","4","5"):
        ctx.text = trigger
    return await cmd.execute(ctx)


async def dispatch_public(trigger: str, ctx: CommandContext) -> str:
    cmd = _PUBLIC_COMMANDS.get(trigger.lower())
    if not cmd:
        return f"❓ Comando `!{trigger}` não reconhecido."
    if trigger in ("1","2","3","4","5"):
        ctx.text = trigger
    return await cmd.execute(ctx)