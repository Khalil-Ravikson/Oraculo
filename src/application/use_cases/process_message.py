"""
application/use_cases/process_message.py — Sprint 1 (Stream Dispatch)
======================================================================

MUDANÇA vs versão anterior:
  _enqueue() agora chama dispatch_with_stream() em vez de apply_async direto.
  Isso injeta o Redis Stream no pipeline sem mudar a lógica de negócio.

  Todo o resto (Porteiro PostgreSQL, RBAC, ban check, etc.) permanece igual.
"""
from __future__ import annotations

import logging
import re

from src.domain.ports.cache_lock import ICacheLock
from src.domain.ports.message_gateway import IMessageGateway
from src.domain.ports.user_repository import IUserRepository

logger = logging.getLogger(__name__)

_FILLER_RE = re.compile(
    r"^(ok|certo|tá|ta|blz|beleza|aguardando|no aguardo|"
    r"entendi|combinado|👍|🙏|✅|sim|não|nao)\s*[.!]?$",
    re.IGNORECASE,
)

_MSG_MANUTENCAO = (
    "🔧 *O Oráculo está em manutenção para melhorias.*\n\n"
    "Voltarei em breve! 🎓"
)
_MSG_PENDENTE = (
    "⏳ Seu cadastro está em análise pelo CTIC.\n"
    "Em breve você receberá a confirmação!"
)


class ProcessMessageUseCase:
    """
    Orquestra o webhook: Porteiro PostgreSQL → RBAC → Stream + Celery dispatch.
    """

    def __init__(
        self,
        user_repo: IUserRepository,
        gateway:   IMessageGateway,
        lock:      ICacheLock,
    ):
        self._repo    = user_repo
        self._gateway = gateway
        self._lock    = lock

    async def execute(self, identity: dict) -> str:
        phone   = identity["sender_phone"]
        chat_id = identity["chat_id"]
        body    = identity.get("body", "")

        # ── PORTÃO 0: Modo Manutenção ─────────────────────────────────────────
        if await self._esta_em_manutencao():
            if not self._eh_admin(phone):
                await self._gateway.enviar_mensagem(chat_id, _MSG_MANUTENCAO)
                return "maintenance"

        # ── PORTÃO 1: Lock Redis (anti-spam) ──────────────────────────────────
        if await self._lock.is_locked(phone):
            if _FILLER_RE.match(body.strip()):
                logger.debug("🔇 Filler ignorado durante processamento: %s", phone)
                return "ignored"
            logger.debug("⏳ Usuário %s em processamento — descartado.", phone)
            return "ignored"

        # ── PORTÃO 2: Admin (verificação ANTES do banco) ──────────────────────
        if self._eh_admin(phone):
            identity["is_admin"] = True
            identity["role"]     = "admin"
            identity["status"]   = "ativo"
            identity["nome"]     = "Admin"
            self._enqueue(identity, queue="admin")
            return "admin_direct"

        # ── PORTÃO 3: PostgreSQL Porteiro ─────────────────────────────────────
        aluno = await self._repo.get_by_phone(phone)

        if not aluno:
            from src.application.use_cases.handle_registration import HandleRegistrationUseCase
            await HandleRegistrationUseCase(self._gateway).execute(
                chat_id=chat_id, phone=phone, body=body,
            )
            return "registered"

        # ── PORTÃO 4: RBAC ────────────────────────────────────────────────────
        if getattr(aluno, "status", "") == "banido":
            return "banned"

        if getattr(aluno, "status", "") == "pendente":
            await self._gateway.enviar_mensagem(chat_id, _MSG_PENDENTE)
            return "blocked"

        if getattr(aluno, "status", "") == "inativo":
            await self._gateway.enviar_mensagem(
                chat_id,
                "❌ Seu vínculo com a UEMA está inativo. "
                "Contate a secretaria do seu curso.",
            )
            return "blocked"

        # ── DESPACHO: Stream + Celery ─────────────────────────────────────────
        ctx = getattr(aluno, "llm_context", {}) or {}
        identity_rica = {
            **identity,
            "user_id":   str(getattr(aluno, "id", phone)),
            "nome":      getattr(aluno, "nome", "Aluno"),
            "role":      getattr(aluno, "role", "estudante"),
            "status":    getattr(aluno, "status", "ativo"),
            "is_admin":  False,
            "curso":     ctx.get("curso") or getattr(aluno, "curso", None),
            "periodo":   ctx.get("periodo") or getattr(aluno, "semestre_ingresso", None),
            "matricula": getattr(aluno, "matricula", None),
            "centro":    str(aluno.centro.value) if getattr(aluno, "centro", None) else None,
        }

        stream_id = self._enqueue(identity_rica)
        logger.info(
            "📥 Enfileirado: %s | stream_id=%s",
            identity_rica["nome"], stream_id or "n/a",
        )
        return "enqueued"

    def _enqueue(self, identity: dict, queue: str = "default") -> str:
        """
        Sprint 1: usa dispatch_with_stream() para durabilidade via Redis Streams.
        Retorna o stream_id para auditoria.
        """
        from src.api.webhook import dispatch_with_stream
        return dispatch_with_stream(identity, queue=queue)

    def _eh_admin(self, phone: str) -> bool:
        from src.infrastructure.settings import settings
        numeros = {
            re.sub(r"\D", "", n)
            for n in (settings.ADMIN_NUMBERS or "").split(",")
            if n.strip()
        }
        return re.sub(r"\D", "", phone) in numeros

    async def _esta_em_manutencao(self) -> bool:
        try:
            from src.infrastructure.redis_client import get_redis_text
            return get_redis_text().get("admin:maintenance_mode") == "1"
        except Exception:
            return False