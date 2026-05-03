"""
application/webhook/controller.py — Webhook Controller (Celery Dispatcher)
===========================================================================
Implementa as Regras 2 e 4 de negócio de forma assíncrona.

FLUXO EXACTO:
  1. Parse do payload Evolution API
  2. Ignora mensagens que NÃO são de utilizador
  3. [PORTEIRO] Consulta PostgreSQL: valida número + status="Ativo"
  4. [PORTEIRO] Resgata user_context rico
  5. [LOCK] Verifica Redis Lock:
     → Se locked + mensagem inútil → 200 silencioso
     → Se locked + mensagem substancial → responde "aguarde" e 200
     → Se not locked → adquire lock e prossegue
  6. Envia a requisição para a fila do Celery (process_message_task)
  7. Retorna 200 OK imediato para a Evolution API
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from src.infrastructure.settings import settings
# IMPORT DA TASK DO CELERY QUE VOCÊ CRIOU
from src.application.tasks.process_message_task import process_message_task 

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────

_LOCK_TTL_S        = 120      # lock expira em 2min (previne deadlock de worker morto)
_LOCK_WAIT_MSG_TTL = 60       # TTL da mensagem "aguarde" no Redis (dedup)
_LOCK_PREFIX       = "lock:session:"

_INUTIL_PATTERNS = re.compile(
    r"^(ok|okay|certo|tá|ta|sim|não|nao|👍|👌|✅|"
    r"aguardando|aguarde|pode|manda|vamos|vai|oi|olá|opa|tudo)[\s!.?]*$",
    re.IGNORECASE,
)

webhook_router = APIRouter(prefix="/webhook", tags=["webhook"])


# ─── Modelos de payload ───────────────────────────────────────────────────────

class EvolutionMessage(BaseModel):
    """Payload normalizado da Evolution API v2."""
    instance:   str
    message_id: str = Field(alias="key.id", default="")
    phone:      str = Field(alias="key.remoteJid", default="")
    from_me:    bool = Field(alias="key.fromMe", default=False)
    text:       str = Field(alias="message.conversation", default="")
    msg_type:   str = Field(alias="messageType", default="conversation")
    timestamp:  int = 0

    class Config:
        populate_by_name = True


# ─── Controller ───────────────────────────────────────────────────────────────

class WebhookController:
    """
    Controller do webhook. Orquestra todas as verificações de negócio
    antes de jogar para a fila do Celery.
    """

    def __init__(
        self,
        user_repository,
        redis_client,
        whatsapp_client,
        metrics,
    ) -> None:
        self._repo     = user_repository
        self._redis    = redis_client
        self._wa       = whatsapp_client
        self._metrics  = metrics

    async def handle(self, payload: dict) -> Response:
        t0       = time.monotonic()
        trace_id = str(uuid.uuid4())[:8]

        # ── 1 + 2: Parse e filtragem ──────────────────────────────────────────
        msg = self._parse_payload(payload)
        if msg is None:
            return Response(status_code=200)

        phone   = _normalizar_phone(msg.phone)
        texto   = msg.text.strip()
        session = phone

        if not texto:
            return Response(status_code=200)

        logger.info(
            "📩 [WEBHOOK] Recebida | trace=%s | phone=%s | texto='%.60s'",
            trace_id, phone[-6:], texto,
        )

        # ── 3 + 4: PORTEIRO — Validação PostgreSQL ────────────────────────────
        t_db = time.monotonic()
        user_data = await self._validar_usuario(phone)
        db_ms     = int((time.monotonic() - t_db) * 1000)

        self._metrics.observe_db_latency(db_ms)

        if user_data is None:
            logger.info(
                "🚫 [PORTEIRO] Número não cadastrado/inativo | phone=%s | %dms", 
                phone[-6:], db_ms,
            )
            self._metrics.increment_blocked_requests()
            return Response(status_code=200)

        is_admin = (phone == _normalizar_phone(settings.ADMIN_PHONE))

        # ── 5: LOCK — Controlo de concorrência ────────────────────────────────
        lock_key   = f"{_LOCK_PREFIX}{session}"
        lock_token = str(uuid.uuid4())
        locked     = self._redis.exists(lock_key)

        if locked:
            return await self._handle_locked_session(phone, session, texto, trace_id)

        # Adquire lock
        adquirido = self._redis.set(lock_key, lock_token, nx=True, ex=_LOCK_TTL_S)
        if not adquirido:
            return await self._handle_locked_session(phone, session, texto, trace_id)

        logger.debug("🔒 [LOCK] Adquirido | trace=%s | key=%s", trace_id, lock_key)

        try:
            # ── 6: DESPACHO CELERY ─────────────────────────────────────────────
            # Passamos todos os dados, inclusive a chave do lock para o Worker liberar
            process_message_task.delay(
                session=session,
                phone=phone,
                texto=texto,
                user_data=user_data,
                is_admin=is_admin,
                trace_id=trace_id,
                lock_key=lock_key,
                lock_token=lock_token
            )
            
            logger.info("📤 [CELERY] Tarefa enfileirada | trace=%s", trace_id)
            self._metrics.increment_requests_processed()

        except Exception as exc:
            logger.exception(
                "❌ [WEBHOOK] ERRO AO ENFILEIRAR | trace=%s | erro: %s",
                trace_id, exc,
            )
            self._metrics.increment_errors()
            # Se der erro ao enfileirar, temos que liberar o lock nós mesmos
            self._release_lock(lock_key, lock_token)
            
            await asyncio.to_thread(
                self._wa.enviar_mensagem,
                phone,
                "Desculpe, ocorreu um erro ao processar sua mensagem. 🙏",
            )

        # Retorno imediato. O Worker Celery fará o resto.
        return Response(status_code=200)


    # ── Métodos privados ───────────────────────────────────────────────────────

    async def _validar_usuario(self, phone: str) -> dict | None:
        """PORTEIRO: valida número no PostgreSQL."""
        try:
            # Dependendo de como está o seu novo PessoaRepository, talvez
            # o método se chame get_by_telefone em vez de find_by_phone
            user = await self._repo.get_by_telefone(phone) 
            if user is None:
                return None
            
            # Ajuste de acordo com seus atributos da model Pessoa
            ativo = getattr(user, 'ativo', getattr(user, 'is_active', getattr(user, 'verificado', False)))
            if not ativo:
                return None
                
            return {
                "nome":        user.nome,
                "matricula":   user.matricula,
                "periodo":     getattr(user, 'semestre_ingresso', ""),
                "curso":       user.curso,
                "status":      "ativo",
            }
        except Exception as exc:
            logger.exception("❌ [PORTEIRO] Falha no PostgreSQL | erro: %s", exc)
            return None

    async def _handle_locked_session(self, phone: str, session: str, texto: str, trace_id: str) -> Response:
        """Trata mensagens recebidas enquanto o Celery ainda está processando."""
        if _INUTIL_PATTERNS.match(texto):
            return Response(status_code=200)

        dedup_key = f"dedup:aguarde:{session}"
        if not self._redis.exists(dedup_key):
            self._redis.setex(dedup_key, _LOCK_WAIT_MSG_TTL, "1")
            await asyncio.to_thread(
                self._wa.enviar_mensagem,
                phone,
                "⏳ Aguarde, ainda estou processando sua solicitação anterior...",
            )
            
        return Response(status_code=200)

    def _release_lock(self, key: str, token: str) -> None:
        """Libera o lock apenas em caso de erro no enfileiramento do Celery."""
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) else return 0 end
        """
        try:
            self._redis.eval(lua_script, 1, key, token)
        except Exception:
            pass

    @staticmethod
    def _parse_payload(payload: dict) -> EvolutionMessage | None:
        """Extrai a mensagem útil do payload da Evolution API."""
        try:
            key  = payload.get("data", {}).get("key", {})
            msg  = payload.get("data", {}).get("message", {})

            if key.get("fromMe", True) or payload.get("event") != "messages.upsert":
                return None

            texto = (msg.get("conversation") or msg.get("extendedTextMessage", {}).get("text", "") or "")
            if not texto.strip():
                return None

            phone = key.get("remoteJid", "").replace("@s.whatsapp.net", "")

            return EvolutionMessage(
                instance   = payload.get("instance", ""),
                phone      = phone,
                from_me    = key.get("fromMe", False),
                text       = texto,
                msg_type   = payload.get("data", {}).get("messageType", ""),
                timestamp  = payload.get("data", {}).get("messageTimestamp", 0),
            )
        except Exception:
            return None


def _normalizar_phone(phone: str) -> str:
    cleaned = re.sub(r"[^\d]", "", phone)
    if cleaned.startswith("55") and len(cleaned) >= 12: return cleaned
    if len(cleaned) in (10, 11): return f"55{cleaned}"
    return cleaned

def register_webhook(app, controller: WebhookController) -> None:
    @app.post("/webhook/evolution")
    async def webhook_evolution(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            return Response(status_code=200)
        return await controller.handle(payload)