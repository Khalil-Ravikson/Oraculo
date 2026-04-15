# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 8: src/application/webhook/controller.py
# Responsabilidade: Receber webhook Evolution API, aplicar Porteiro e Lock.
# ─────────────────────────────────────────────────────────────────────────────

"""
application/webhook/controller.py — Webhook Controller v3
==========================================================
Implementa as Regras 2 e 4 de negócio antes de qualquer LLM.

FLUXO EXACTO (cada linha é uma operação, na ordem correcta):

  1. Parse do payload Evolution API
  2. Ignora mensagens que NÃO são de utilizador (status updates, acks)
  3. [PORTEIRO] Consulta PostgreSQL: valida número + status="Ativo"
     → 403 silencioso se não cadastrado ou inactivo
  4. [PORTEIRO] Resgata user_context rico (nome, matricula, curso, período)
  5. [LOCK] Verifica Redis Lock:
     → Se locked + mensagem inútil (ok, certo, 👍) → 200 silencioso (regra 4)
     → Se locked + mensagem substancial → responde "aguarde" e 200
     → Se not locked → adquire lock e prossegue
  6. [HITL] Verifica se sessão está em awaiting_hitl:
     → Se sim → resume o grafo com a mensagem como confirmação
     → Se não → inicia novo invoke()
  7. Executa o grafo LangGraph (async)
  8. Envia resposta via WhatsApp
  9. Liberta o lock
  10. Persiste o turno no SessionManager
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────

_LOCK_TTL_S        = 120      # lock expira em 2min (previne deadlock)
_LOCK_WAIT_MSG_TTL = 60       # TTL da mensagem "aguarde" no Redis (dedup)
_LOCK_PREFIX       = "lock:session:"
_HITL_PREFIX       = "hitl:session:"

# Padrões de mensagens inúteis durante processamento (Regra 4)
_INUTIL_PATTERNS = re.compile(
    r"^(ok|okay|certo|tá|ta|sim|não|nao|👍|👌|✅|"
    r"aguardando|aguarde|pode|manda|vamos|vai|oi|olá|opa|tudo)[\s!.?]*$",
    re.IGNORECASE,
)

# Webhook router (montado no main.py)
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
    antes de chamar o grafo LangGraph.

    Depende de:
      - user_repository:  IUserRepository (consulta PostgreSQL)
      - redis_client:     redis.Redis síncrono (locks de baixa latência)
      - graph:            CompiledGraph do LangGraph
      - whatsapp_client:  cliente Evolution API (envio de mensagens)
      - session_manager:  RedisVLSessionManager
      - metrics:          PrometheusMetrics
    """

    def __init__(
        self,
        user_repository,
        redis_client,
        graph,
        whatsapp_client,
        session_manager,
        metrics,
    ) -> None:
        self._repo     = user_repository
        self._redis    = redis_client
        self._graph    = graph
        self._wa       = whatsapp_client
        self._sessions = session_manager
        self._metrics  = metrics

    async def handle(self, payload: dict) -> Response:
        """
        Ponto de entrada do webhook — orquestra todo o fluxo.
        Retorna sempre HTTP 200 (Evolution API faz retry em non-200).
        """
        t0       = time.monotonic()
        trace_id = str(uuid.uuid4())[:8]

        # ── 1 + 2: Parse e filtragem de mensagens não-utilizador ──────────────
        msg = self._parse_payload(payload)
        if msg is None:
            return Response(status_code=200)   # silencioso

        phone   = _normalizar_phone(msg.phone)
        texto   = msg.text.strip()
        session = phone   # session_id = número normalizado

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
                "🚫 [PORTEIRO] Número não cadastrado ou inativo | "
                "phone=%s | %dms", phone[-6:], db_ms,
            )
            self._metrics.increment_blocked_requests()
            # Retorna sem resposta — não gasta tokens, não envia msg
            return Response(status_code=200)

        logger.debug(
            "✅ [PORTEIRO] Usuário autorizado | trace=%s | "
            "status=%s | curso=%s | %dms",
            trace_id,
            user_data.get("status"),
            user_data.get("curso", "?"),
            db_ms,
        )

        # Admin hardcoded via .env
        is_admin = (phone == _normalizar_phone(settings.ADMIN_PHONE))

        # ── 5: LOCK — Controlo de concorrência ────────────────────────────────
        lock_key   = f"{_LOCK_PREFIX}{session}"
        lock_token = str(uuid.uuid4())
        locked     = self._redis.exists(lock_key)

        if locked:
            return await self._handle_locked_session(
                phone, session, texto, trace_id,
            )

        # Adquire lock antes de processar (NX=set-if-not-exists, EX=TTL)
        adquirido = self._redis.set(
            lock_key, lock_token, nx=True, ex=_LOCK_TTL_S,
        )
        if not adquirido:
            # Corrida: outra coroutine adquiriu entre o exists() e o set()
            return await self._handle_locked_session(
                phone, session, texto, trace_id,
            )

        logger.debug(
            "🔒 [LOCK] Adquirido | trace=%s | key=%s | ttl=%ds",
            trace_id, lock_key, _LOCK_TTL_S,
        )

        try:
            # ── 6: HITL — Verifica se sessão aguarda confirmação ───────────────
            awaiting = await self._verificar_hitl(session)

            if awaiting:
                resultado = await self._resumir_hitl(
                    session, phone, texto, user_data, is_admin, trace_id,
                )
            else:
                resultado = await self._invocar_grafo(
                    session, phone, texto, user_data, is_admin, trace_id,
                )

            # ── 7: Envia resposta ──────────────────────────────────────────────
            resposta = resultado.get("resposta_final", "")
            if resposta:
                await asyncio.to_thread(
                    self._wa.enviar_mensagem, phone, resposta,
                )
                logger.info(
                    "📤 [WEBHOOK] Resposta enviada | trace=%s | chars=%d",
                    trace_id, len(resposta),
                )

            # ── 8: Persiste histórico ──────────────────────────────────────────
            asyncio.create_task(self._sessions.adicionar_turno(session, "user", texto))
            if resposta:
                asyncio.create_task(self._sessions.adicionar_turno(session, "assistant", resposta))

            # ── 9: Métricas ────────────────────────────────────────────────────
            total_ms = int((time.monotonic() - t0) * 1000)
            self._metrics.observe_request_latency(total_ms)
            self._metrics.increment_requests_processed()
            logger.info(
                "✅ [WEBHOOK] Concluído | trace=%s | total=%dms",
                trace_id, total_ms,
            )

        except Exception as exc:
            logger.exception(
                "❌ [WEBHOOK] ERRO CRÍTICO | trace=%s | phone=%s | erro: %s",
                trace_id, phone[-6:], exc,
            )
            self._metrics.increment_errors()
            # Envia mensagem de erro amigável sem expor internals
            await asyncio.to_thread(
                self._wa.enviar_mensagem,
                phone,
                "Desculpe, ocorreu um erro interno. Nossa equipa foi notificada. 🙏",
            )

        finally:
            # ── 10: Liberta o lock SEMPRE (mesmo em caso de erro) ─────────────
            # Usa script Lua para release atómico (verifica token antes de deletar)
            _release_lock(self._redis, lock_key, lock_token)
            logger.debug(
                "🔓 [LOCK] Liberado | trace=%s | key=%s",
                trace_id, lock_key,
            )

        return Response(status_code=200)

    # ── Métodos privados ───────────────────────────────────────────────────────

    async def _validar_usuario(self, phone: str) -> dict | None:
        """
        PORTEIRO: valida número no PostgreSQL e retorna contexto rico.
        Esta é a PRIMEIRA operação real — bloqueia não-cadastrados antes do LLM.

        Returns:
            dict com user_context se válido, None se inválido/inativo.
        """
        try:
            user = await self._repo.find_by_phone(phone)
            if user is None:
                return None
            if user.get("status", "").lower() != "ativo":
                return None
            return {
                "nome":        user.get("nome", ""),
                "matricula":   user.get("matricula", ""),
                "periodo":     user.get("periodo", ""),
                "curso":       user.get("curso", ""),
                "instituicao": user.get("instituicao", "UEMA"),
                "status":      user.get("status", ""),
            }
        except Exception as exc:
            logger.exception(
                "❌ [PORTEIRO] Falha no PostgreSQL | phone=%s | "
                "causa=%s | erro: %s",
                phone[-6:], type(exc).__name__, exc,
            )
            # Fail-closed: em caso de falha de DB, bloqueia para segurança
            return None

    async def _handle_locked_session(
        self,
        phone: str,
        session: str,
        texto: str,
        trace_id: str,
    ) -> Response:
        """
        Trata mensagens recebidas durante processamento activo (Regra 4).
        Mensagens inúteis → silêncio.
        Mensagens substanciais → "aguarde" (uma vez, com dedup Redis).
        """
        if _INUTIL_PATTERNS.match(texto):
            logger.debug(
                "🔕 [LOCK] Mensagem inútil ignorada | trace=%s | texto='%s'",
                trace_id, texto[:30],
            )
            return Response(status_code=200)

        # Dedup: só envia "aguarde" uma vez por sessão bloqueada
        dedup_key = f"dedup:aguarde:{session}"
        ja_enviado = self._redis.exists(dedup_key)

        if not ja_enviado:
            self._redis.setex(dedup_key, _LOCK_WAIT_MSG_TTL, "1")
            await asyncio.to_thread(
                self._wa.enviar_mensagem,
                phone,
                "⏳ Aguarde, ainda estou processando sua solicitação anterior...",
            )
            logger.info(
                "⏳ [LOCK] Mensagem 'aguarde' enviada | trace=%s | phone=%s",
                trace_id, phone[-6:],
            )
        else:
            logger.debug(
                "🔕 [LOCK] 'aguarde' já enviado, silenciando | trace=%s",
                trace_id,
            )

        return Response(status_code=200)

    async def _verificar_hitl(self, session: str) -> bool:
        """
        Verifica se a sessão tem um HITL pendente no Redis.
        O LangGraph com MemorySaver guarda o estado em memória;
        guardamos o flag HITL no Redis para o Controller verificar.
        """
        try:
            return bool(self._redis.exists(f"{_HITL_PREFIX}{session}"))
        except Exception:
            return False

    async def _invocar_grafo(
        self,
        session: str,
        phone: str,
        texto: str,
        user_data: dict,
        is_admin: bool,
        trace_id: str,
    ) -> dict:
        """
        Invoca o grafo LangGraph do início.
        """
        t0 = time.monotonic()

        # Recupera histórico compactado
        historico = await self._sessions.get_historico(session)

        state_input: dict = {
            "session_id":    session,
            "user_phone":    phone,
            "is_admin":      is_admin,
            "user_context":  user_data,
            "user_message":  texto,
            "historico":     historico.texto_formatado,
            "trace_id":      trace_id,
            "awaiting_hitl": False,
            "node_timings":  [],
        }

        config = {"configurable": {"thread_id": session}}

        try:
            # LangGraph invoke é síncrono na versão 0.0.39
            resultado = await asyncio.to_thread(
                self._graph.invoke, state_input, config,
            )

            ms = int((time.monotonic() - t0) * 1000)
            logger.debug(
                "🚀 [GRAPH] invoke concluído | trace=%s | %dms",
                trace_id, ms,
            )

            # Se o grafo pausou no HITL, regista o flag no Redis
            if resultado.get("awaiting_hitl"):
                self._redis.setex(
                    f"{_HITL_PREFIX}{session}", _LOCK_TTL_S * 10, "1",
                )
                logger.info(
                    "⏸️  [HITL] Sessão pausada aguardando confirmação | "
                    "trace=%s | session=%s",
                    trace_id, session[-6:],
                )

            return resultado

        except Exception as exc:
            logger.exception(
                "❌ [GRAPH] invoke falhou | trace=%s | causa=%s | erro: %s",
                trace_id, type(exc).__name__, exc,
            )
            raise

    async def _resumir_hitl(
        self,
        session: str,
        phone: str,
        texto: str,
        user_data: dict,
        is_admin: bool,
        trace_id: str,
    ) -> dict:
        """
        Retoma o grafo pausado com a resposta de confirmação do utilizador.
        O LangGraph resume a partir do interrupt_before=["confirm_node"].
        """
        t0 = time.monotonic()
        logger.info(
            "▶️  [HITL] Retomando grafo | trace=%s | resposta='%s'",
            trace_id, texto[:30],
        )

        # Injecta a confirmação no estado e resume
        state_update = {
            "tool_confirmation": texto,
            "user_message":      texto,
            "trace_id":          trace_id,
        }
        config = {"configurable": {"thread_id": session}}

        try:
            # Na versão 0.0.39 do LangGraph, usa-se update_state + invoke
            self._graph.update_state(config, state_update)
            resultado = await asyncio.to_thread(
                self._graph.invoke, None, config,
            )

            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "▶️  [HITL] Grafo retomado | trace=%s | %dms",
                trace_id, ms,
            )

            # Remove flag HITL — sessão concluída
            self._redis.delete(f"{_HITL_PREFIX}{session}")

            return resultado

        except Exception as exc:
            logger.exception(
                "❌ [HITL] Falha ao retomar | trace=%s | causa=%s | erro: %s",
                trace_id, type(exc).__name__, exc,
            )
            # Remove flag para não bloquear a sessão indefinidamente
            self._redis.delete(f"{_HITL_PREFIX}{session}")
            raise

    @staticmethod
    def _parse_payload(payload: dict) -> EvolutionMessage | None:
        """
        Extrai a mensagem útil do payload da Evolution API v2.
        Filtra: fromMe, status updates, non-conversation types.
        """
        try:
            key  = payload.get("data", {}).get("key", {})
            msg  = payload.get("data", {}).get("message", {})

            if key.get("fromMe", True):
                return None
            if payload.get("event") != "messages.upsert":
                return None

            texto = (
                msg.get("conversation")
                or msg.get("extendedTextMessage", {}).get("text", "")
                or ""
            )
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
        except Exception as exc:
            logger.warning("⚠️  [WEBHOOK] Erro ao parsear payload: %s", exc)
            return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _normalizar_phone(phone: str) -> str:
    """Normaliza número: remove @s.whatsapp.net, espaços, hífens."""
    cleaned = re.sub(r"[^\d]", "", phone)
    # Garante formato E.164 sem o +
    if cleaned.startswith("55") and len(cleaned) >= 12:
        return cleaned
    if len(cleaned) in (10, 11):
        return f"55{cleaned}"
    return cleaned


def _release_lock(r, key: str, token: str) -> None:
    """
    Release atómico do lock via script Lua.
    Verifica que o token é o nosso antes de deletar
    (evita deletar lock adquirido por outro worker em restart).
    """
    lua_script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    try:
        r.eval(lua_script, 1, key, token)
    except Exception as exc:
        logger.warning("⚠️  [LOCK] Release falhou | key=%s | erro: %s", key, exc)


# ─── FastAPI endpoint ─────────────────────────────────────────────────────────

def register_webhook(
    app,
    controller: WebhookController,
) -> None:
    """Regista o endpoint do webhook no FastAPI."""

    @app.post("/webhook/evolution")
    async def webhook_evolution(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            return Response(status_code=200)
        return await controller.handle(payload)