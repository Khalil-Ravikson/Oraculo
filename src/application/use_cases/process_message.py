# src/application/use_cases/process_message.py
"""
Use Case principal: orquestra o fluxo completo de uma mensagem recebida.

FLUXO:
  DevGuard → Lock check → DB Porteiro → RBAC → Enfileira para Celery
"""
from __future__ import annotations
import logging
import re

from src.domain.ports.cache_lock import ICacheLock
from src.domain.ports.message_gateway import IMessageGateway
from src.domain.ports.user_repository import IUserRepository
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# Mensagens que chegam enquanto o bot está processando — ignoradas silenciosamente
_FILLER_PATTERNS = re.compile(
    r"^(ok|certo|tá|ta|blz|beleza|aguardando|no aguardo|"
    r"entendi|combinado|👍|🙏|✅)\s*[.!]?$",
    re.IGNORECASE,
)

class ProcessMessageUseCase:

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
        """
        Retorna o status do processamento:
          'enqueued'   → tarefa enviada para Celery
          'ignored'    → mensagem descartada silenciosamente
          'blocked'    → usuário sem permissão (mensagem enviada)
          'registered' → usuário novo, fluxo de cadastro iniciado
        """
        phone   = identity["sender_phone"]
        chat_id = identity["chat_id"]
        body    = identity.get("body", "")

        # ── PORTÃO 1: Lock (Regra 4) ──────────────────────────────────────
        if await self._lock.is_locked(phone):
            if _FILLER_PATTERNS.match(body.strip()):
                logger.debug("🔇 Filler ignorado durante processamento: %s", phone)
                return "ignored"
            # Mensagem real durante lock — pode enfileirar ou descartar
            logger.info("⏳ Usuário %s em processamento, mensagem descartada.", phone)
            return "ignored"

        # ── PORTÃO 2: PostgreSQL Porteiro (Regra 2) ───────────────────────
        aluno = await self._repo.get_by_phone(phone)

        if not aluno:
            # Inicia fluxo de cadastro — zero tokens
            from src.application.use_cases.handle_registration import (
                HandleRegistrationUseCase,
            )
            reg_uc = HandleRegistrationUseCase(self._gateway)
            await reg_uc.execute(chat_id=chat_id, phone=phone, body=body)
            return "registered"

        # ── PORTÃO 3: RBAC (Regra 1) ─────────────────────────────────────
        if aluno.status == "pendente":
            await self._gateway.enviar_mensagem(
                chat_id,
                "⏳ Seu cadastro está em análise. Aguarde a validação!"
            )
            return "blocked"

        if aluno.status == "inativo":
            await self._gateway.enviar_mensagem(
                chat_id,
                "❌ Seu vínculo está inativo. Contate a secretaria do curso."
            )
            return "blocked"

        # ── DESPACHO: Celery com identidade rica (Regra 2) ───────────────
        from src.application.tasks import processar_mensagem_task

        identity_rica = {
            **identity,
            "user_id":      str(aluno.id),
            "user_name":    aluno.nome,
            "user_role":    aluno.role,
            "user_status":  aluno.status,
            "user_context": {
                "matricula": aluno.matricula,
                "curso":     aluno.curso,
                "periodo":   aluno.semestre_ingresso,
                "centro":    aluno.centro,
            },
        }

        processar_mensagem_task.delay(identity_rica)
        logger.info("📥 Enfileirado: %s | %s", aluno.nome, phone)
        return "enqueued"