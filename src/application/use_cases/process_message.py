# src/application/use_cases/process_message.py
"""
Caso de Uso Principal: Orquestra o fluxo completo de uma mensagem recebida.

PIPELINE EXATA (Regras 2 + 4):
  1. [DevGuard]       → valida payload WhatsApp
  2. [Redis Lock]     → bloqueia spam durante processamento
  3. [PostgreSQL]     → porteiro: quem é esse número?
  4. [RBAC]           → tem permissão para continuar?
  5. [Admin Check]    → é o admin? → rota especial
  6. [Celery Enqueue] → processa em background com identidade rica

CLEAN ARCHITECTURE:
  Este use case recebe interfaces (ports), não implementações.
  Testável com mocks sem subir banco ou Redis.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.ports.cache_lock import ICacheLock
    from src.domain.ports.message_gateway import IMessageGateway
    from src.domain.ports.user_repository import IUserRepository

logger = logging.getLogger(__name__)

# Mensagens ignoradas silenciosamente durante processamento
_FILLER_RE = re.compile(
    r"^(ok|certo|tá|ta|blz|beleza|aguardando|no aguardo|"
    r"entendi|combinado|👍|🙏|✅|sim|não|nao)\s*[.!]?$",
    re.IGNORECASE,
)

# Resposta padrão quando o sistema está em manutenção
_MSG_MANUTENCAO = (
    "🔧 *O Oráculo está em manutenção para melhorias.*\n\n"
    "Voltarei em breve! 🎓"
)

# Resposta para usuários banidos (silêncio — não enviamos nada)
_MSG_BANIDO = None  # None = não responde

# Resposta para cadastro pendente
_MSG_PENDENTE = (
    "⏳ Seu cadastro está em análise pelo CTIC.\n"
    "Em breve você receberá a confirmação!"
)


class ProcessMessageUseCase:
    """
    Orquestra o webhook do WhatsApp.

    Injeção de dependências via construtor:
      - user_repo:  IUserRepository  (PostgreSQL)
      - gateway:    IMessageGateway  (Evolution API)
      - lock:       ICacheLock       (Redis)
    """

    def __init__(
        self,
        user_repo: "IUserRepository",
        gateway:   "IMessageGateway",
        lock:      "ICacheLock",
    ):
        self._repo    = user_repo
        self._gateway = gateway
        self._lock    = lock

    async def execute(self, identity: dict) -> str:
        """
        Processa uma mensagem recebida.

        Retorna status string para logging:
          "enqueued"    → enviado ao Celery
          "ignored"     → descartado silenciosamente
          "maintenance" → sistema em manutenção
          "banned"      → usuário banido (silêncio)
          "blocked"     → sem permissão (mensagem enviada)
          "registered"  → novo usuário, onboarding iniciado
          "admin_direct"→ admin respondido diretamente
        """
        phone   = identity["sender_phone"]
        chat_id = identity["chat_id"]
        body    = identity.get("body", "")

        # ── PORTÃO 0: Modo Manutenção ─────────────────────────────────────────
        if await self._esta_em_manutencao():
            if not self._eh_admin(phone):
                await self._gateway.enviar_mensagem(chat_id, _MSG_MANUTENCAO)
                return "maintenance"
            # Admin passa mesmo em manutenção

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
            # Admin vai direto para o Celery sem consultar banco
            self._enqueue(identity)
            return "admin_direct"

        # ── PORTÃO 3: PostgreSQL Porteiro ─────────────────────────────────────
        aluno = await self._repo.get_by_phone(phone)

        if not aluno:
            from src.application.use_cases.handle_registration import HandleRegistrationUseCase
            await HandleRegistrationUseCase(self._gateway).execute(
                chat_id=chat_id, phone=phone, body=body,
            )
            return "registered"

        # ── PORTÃO 4: RBAC (verificação de status) ────────────────────────────
        if getattr(aluno, "status", "") == "banido":
            logger.info("🚫 Usuário banido silenciado: %s", phone)
            return "banned"   # Silêncio total para banidos

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

        # ── DESPACHO: Celery com identidade rica ──────────────────────────────
        ctx = getattr(aluno, "llm_context", {}) or {}
        identity_rica = {
            **identity,
            "user_id":    str(getattr(aluno, "id", phone)),
            "nome":       getattr(aluno, "nome", "Aluno"),
            "role":       getattr(aluno, "role", "estudante"),
            "status":     getattr(aluno, "status", "ativo"),
            "is_admin":   False,
            "curso":      ctx.get("curso") or getattr(aluno, "curso", None),
            "periodo":    ctx.get("periodo") or getattr(aluno, "semestre_ingresso", None),
            "matricula":  getattr(aluno, "matricula", None),
            "centro":     str(aluno.centro.value) if getattr(aluno, "centro", None) else None,
        }

        self._enqueue(identity_rica)
        logger.info("📥 Enfileirado: %s | %s", identity_rica["nome"], phone)
        return "enqueued"

    def _enqueue(self, identity: dict) -> None:
        """Envia para fila Celery correta (admin tem prioridade)."""
        from src.application.tasks import processar_mensagem_task
        queue = "admin" if identity.get("is_admin") else "default"
        processar_mensagem_task.apply_async(
            args=[identity],
            queue=queue,
        )

    def _eh_admin(self, phone: str) -> bool:
        """Verifica se o número é do admin sem consultar o banco."""
        from src.infrastructure.settings import settings
        numeros = {
            re.sub(r"\D", "", n)
            for n in (settings.ADMIN_NUMBERS or "").split(",")
            if n.strip()
        }
        return re.sub(r"\D", "", phone) in numeros

    async def _esta_em_manutencao(self) -> bool:
        """Verifica flag de manutenção no Redis."""
        try:
            from src.infrastructure.redis_client import get_redis_text
            return get_redis_text().get("admin:maintenance_mode") == "1"
        except Exception:
            return False