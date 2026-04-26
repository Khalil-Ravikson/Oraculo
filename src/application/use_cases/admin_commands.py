# src/application/use_cases/admin_commands.py
"""
Caso de Uso: Comandos Admin via WhatsApp — "A Constituição do Oráculo"

COMANDOS DISPONÍVEIS:
  !status              → saúde do sistema
  !ban <phone>         → banir utilizador
  !unban <phone>       → desbanir utilizador
  !prompt <texto>      → alterar system prompt global (afeta TODOS imediatamente)
  !prompt reset        → restaurar prompt padrão
  !manutencao on|off   → ativar/desativar modo manutenção
  !cache clear         → limpar cache semântico
  !ingerir <ficheiro>  → reingerir documento
  !audit [N]           → ver últimas N entradas do audit log
  !bloquear-api        → desativar Gemini API (emergência de gastos)
  !desbloquear-api     → reativar Gemini API
  !users               → listar utilizadores ativos
  !help                → lista de comandos

PRINCÍPIO CLEAN:
  Este use case recebe strings simples e retorna strings simples.
  Sem dependência de FastAPI, WhatsApp SDK ou SQLAlchemy direto.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# Mapa de comandos → handler
_HANDLERS: dict = {}


def _cmd(pattern: str):
    """Decorador que registra um handler para um padrão regex de comando."""
    def decorator(fn):
        _HANDLERS[re.compile(pattern, re.IGNORECASE)] = fn
        return fn
    return decorator


class AdminCommandsUseCase:
    """
    Processa comandos admin recebidos via WhatsApp.

    Uso:
        use_case = AdminCommandsUseCase()
        resposta = await use_case.executar("!ban 5598999999999", "admin_id")
    """

    async def executar(self, command: str, admin_id: str) -> str:
        """
        Despacha o comando para o handler correto.
        Registra toda ação no audit log.
        """
        cmd = command.strip()

        for pattern, handler in _HANDLERS.items():
            m = pattern.match(cmd)
            if m:
                try:
                    result = await handler(self, m, admin_id)
                    await self._audit(admin_id, cmd[:80], result[:50])
                    return result
                except Exception as e:
                    logger.exception("❌ Comando '%s' falhou: %s", cmd, e)
                    return f"❌ Erro ao executar `{cmd[:30]}`: `{str(e)[:80]}`"

        return (
            "❓ Comando não reconhecido.\n\n"
            "Use `!help` para ver os comandos disponíveis."
        )

    # ── Handlers ──────────────────────────────────────────────────────────────

    @_cmd(r'^[!/](status|saude|health)$')
    async def _status(self, m, admin_id: str) -> str:
        from src.infrastructure.redis_client import redis_ok, get_redis
        r_ok = redis_ok()
        try:
            r   = get_redis()
            mem = r.info("memory")
            ram = round(mem.get("used_memory", 0) / 1024 / 1024, 1)
        except Exception:
            ram = "?"

        manutencao = self._get_redis_flag("admin:maintenance_mode") == "1"
        api_bloq   = self._get_redis_flag("admin:gemini_blocked") == "1"

        return (
            f"⚙️  *Status do Oráculo*\n\n"
            f"{'🟢' if r_ok else '🔴'} Redis: {'OK' if r_ok else 'OFFLINE'} ({ram}MB RAM)\n"
            f"🔧 Manutenção: {'ATIVO' if manutencao else 'desligado'}\n"
            f"🤖 Gemini API: {'BLOQUEADA ⛔' if api_bloq else 'ativa'}\n"
            f"🕐 Hora: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )

    @_cmd(r'^[!/]ban\s+(\S+)$')
    async def _ban(self, m, admin_id: str) -> str:
        phone = re.sub(r"\D", "", m.group(1))
        await self._update_user_status(phone, "banido")
        return f"🚫 Utilizador `{phone}` banido com sucesso."

    @_cmd(r'^[!/]unban\s+(\S+)$')
    async def _unban(self, m, admin_id: str) -> str:
        phone = re.sub(r"\D", "", m.group(1))
        await self._update_user_status(phone, "ativo")
        return f"✅ Utilizador `{phone}` reativado."

    @_cmd(r'^[!/]prompt\s+reset$')
    async def _prompt_reset(self, m, admin_id: str) -> str:
        self._del_redis("admin:system_prompt")
        return "✅ System prompt restaurado para o padrão."

    @_cmd(r'^[!/]prompt\s+(.+)$')
    async def _prompt_set(self, m, admin_id: str) -> str:
        novo_prompt = m.group(1).strip()
        if len(novo_prompt) < 20:
            return "❌ Prompt muito curto (mínimo 20 chars)."
        self._set_redis("admin:system_prompt", novo_prompt)
        n_chars = len(novo_prompt)
        return (
            f"✅ *System prompt atualizado!*\n"
            f"📝 {n_chars} chars\n"
            f"⚡ Afeta TODOS os alunos imediatamente.\n\n"
            f"Preview: `{novo_prompt[:60]}...`"
        )

    @_cmd(r'^[!/]manutencao\s+(on|off|ligar|desligar)$')
    async def _manutencao(self, m, admin_id: str) -> str:
        ativar = m.group(1).lower() in ("on", "ligar")
        if ativar:
            self._set_redis("admin:maintenance_mode", "1")
            return (
                "🔧 *Modo manutenção ATIVADO*\n\n"
                "Todos os alunos receberão:\n"
                "_'O Oráculo está em manutenção para melhorias. Volto em breve!'_"
            )
        else:
            self._del_redis("admin:maintenance_mode")
            return "✅ *Modo manutenção DESATIVADO* — sistema voltou ao normal."

    @_cmd(r'^[!/](cache\s+clear|flush.?cache|limpar.?cache)$')
    async def _cache_clear(self, m, admin_id: str) -> str:
        from src.infrastructure.semantic_cache import invalidar_cache_rota
        from src.domain.entities import Rota
        total = sum(invalidar_cache_rota(r.value) for r in Rota)
        return f"🗑️  Cache semântico limpo: {total} entradas removidas."

    @_cmd(r'^[!/]bloquear.?api$')
    async def _bloquear_api(self, m, admin_id: str) -> str:
        self._set_redis("admin:gemini_blocked", "1")
        return (
            "⛔ *Gemini API BLOQUEADA*\n\n"
            "Nenhuma chamada ao LLM será feita até você usar `!desbloquear-api`.\n"
            "Os alunos receberão mensagem de sistema em manutenção."
        )

    @_cmd(r'^[!/]desbloquear.?api$')
    async def _desbloquear_api(self, m, admin_id: str) -> str:
        self._del_redis("admin:gemini_blocked")
        return "✅ *Gemini API DESBLOQUEADA* — sistema voltou ao normal."

    @_cmd(r'^[!/]audit(?:\s+(\d+))?$')
    async def _audit(self, m, admin_id: str, n: int = 10) -> str:
        if isinstance(m, re.Match):
            n = int(m.group(1) or 10)

        try:
            from src.infrastructure.redis_client import get_redis_text
            r    = get_redis_text()
            raw  = r.lrange("audit:log", 0, n - 1)
            logs = [json.loads(l) for l in raw]
        except Exception as e:
            return f"❌ Erro ao ler audit log: {e}"

        if not logs:
            return "📋 Nenhuma entrada no audit log."

        linhas = [f"📋 *Últimas {len(logs)} ações admin:*\n"]
        for l in logs:
            ts     = l.get("ts", "")[:16].replace("T", " ")
            action = l.get("action", "?")[:30]
            result = l.get("result", "?")[:15]
            linhas.append(f"`{ts}` — {action} → {result}")

        return "\n".join(linhas)

    @_cmd(r'^[!/]users$')
    async def _users(self, m, admin_id: str) -> str:
        try:
            from src.infrastructure.database.session import AsyncSessionLocal
            from sqlalchemy import text
            async with AsyncSessionLocal() as s:
                r = await s.execute(text(
                    "SELECT nome, telefone, role, status FROM \"pessoas\" "
                    "WHERE status = 'ativo' ORDER BY criado_em DESC LIMIT 10"
                ))
                rows = r.fetchall()
        except Exception as e:
            return f"❌ Erro ao consultar DB: {e}"

        if not rows:
            return "ℹ️  Nenhum utilizador ativo encontrado."

        linhas = [f"👥 *Últimos {len(rows)} utilizadores ativos:*\n"]
        for nome, tel, role, status in rows:
            tel_short = (tel or "")[-8:]
            linhas.append(f"• `...{tel_short}` — {nome[:20]} ({role})")
        return "\n".join(linhas)

    @_cmd(r'^[!/]help$')
    async def _help(self, m, admin_id: str) -> str:
        return (
            "📖 *Comandos Admin do Oráculo:*\n\n"
            "`!status`           → saúde do sistema\n"
            "`!ban <phone>`      → banir utilizador\n"
            "`!unban <phone>`    → desbanir utilizador\n"
            "`!prompt <texto>`   → alterar system prompt\n"
            "`!prompt reset`     → restaurar prompt padrão\n"
            "`!manutencao on`    → ativar modo manutenção\n"
            "`!manutencao off`   → desativar manutenção\n"
            "`!cache clear`      → limpar cache semântico\n"
            "`!bloquear-api`     → bloquear Gemini (emergência)\n"
            "`!desbloquear-api`  → reativar Gemini\n"
            "`!audit [N]`        → ver N entradas do log\n"
            "`!users`            → listar utilizadores ativos\n\n"
            "_Todos os comandos críticos exigem token de confirmação._"
        )

    # ── Utilitários internos ──────────────────────────────────────────────────

    def _get_redis_flag(self, key: str) -> str:
        try:
            from src.infrastructure.redis_client import get_redis_text
            return get_redis_text().get(key) or ""
        except Exception:
            return ""

    def _set_redis(self, key: str, value: str, ttl: int = 0) -> None:
        try:
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()
            if ttl:
                r.setex(key, ttl, value)
            else:
                r.set(key, value)
        except Exception as e:
            logger.error("Redis set '%s' falhou: %s", key, e)

    def _del_redis(self, key: str) -> None:
        try:
            from src.infrastructure.redis_client import get_redis_text
            get_redis_text().delete(key)
        except Exception as e:
            logger.error("Redis del '%s' falhou: %s", key, e)

    async def _update_user_status(self, phone: str, status: str) -> None:
        """Atualiza status do utilizador no PostgreSQL."""
        from src.infrastructure.database.session import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as s:
            await s.execute(
                text('UPDATE "pessoas" SET status = :s WHERE telefone = :p'),
                {"s": status, "p": phone},
            )
            await s.commit()

    async def _audit(self, admin_id: str, action: str, result: str) -> None:
        """Registra ação no audit log (não-bloqueante)."""
        try:
            from src.infrastructure.redis_client import get_redis_text
            r     = get_redis_text()
            entry = json.dumps({
                "ts":     datetime.now().isoformat(),
                "admin":  admin_id,
                "action": action,
                "result": result,
            }, ensure_ascii=False)
            r.lpush("audit:log", entry)
            r.ltrim("audit:log", 0, 999)
            r.expire("audit:log", 86400 * 90)
        except Exception as e:
            logger.debug("Audit log falhou: %s", e)