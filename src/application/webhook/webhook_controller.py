from fastapi import APIRouter, Request, Response
from src.infrastructure.webhook.dto import IncomingMessage
from src.application.tasks.process_message_task import processar_mensagem_whatsapp
from src.infrastructure.settings import settings
import logging
import re

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhook"])

ALLOWED_GROUP = settings.ALLOWED_GROUP_ID

def _parse_evolution_payload(payload: dict) -> IncomingMessage | None:
    """Normaliza o payload da Evolution API v2.3 e extrai o texto do grupo."""
    try:
        if payload.get("event") != "messages.upsert":
            return None

        data = payload.get("data", {})
        key  = data.get("key", {})

        # Trava anti-looping: Ignora se a mensagem veio do próprio número do bot
        if key.get("fromMe", False) is True:
            return None

        msg = data.get("message", {})
        
        # Desenbrulha mensagens efêmeras comuns em canais/grupos
        if "ephemeralMessage" in msg:
            msg = msg.get("ephemeralMessage", {}).get("message", {})

        text = (
            msg.get("conversation")
            or msg.get("extendedTextMessage", {}).get("text")
            or msg.get("imageMessage", {}).get("caption")
            or ""
        ).strip()

        remote_jid = key.get("remoteJid", "")
        is_group   = remote_jid.endswith("@g.us")
        sender_jid = key.get("participant", remote_jid) if is_group else remote_jid
        
        # O bot responderá a qualquer texto enviado dentro do grupo homologado
        mentioned = True 
        
        msg_type   = data.get("messageType", "")
        has_media  = msg_type not in ("conversation", "extendedTextMessage", "")

        return IncomingMessage(
            remote_jid    = remote_jid,
            sender_jid    = re.sub(r"[^\d]", "", sender_jid.split("@")[0].split(":")[0]),
            text          = text,
            push_name     = data.get("pushName", ""),
            is_group      = is_group,
            mentioned_bot = mentioned,
            msg_key_id    = key.get("id", ""),
            has_media     = has_media,
            media_type    = msg_type,
        )
    except Exception as e:
        logger.error("❌ Erro ao converter DTO do webhook: %s", e)
        return None

@router.post("/evolution")
async def webhook_evolution(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=200)

    msg = _parse_evolution_payload(payload)
    if msg is None or (not msg.text and not msg.has_media):
        return Response(status_code=200)

    # 🚫 FILTRO RÍGIDO: Se não for o grupo exclusivo do .env, descarta e ignora o resto
    if msg.remote_jid != ALLOWED_GROUP:
        return Response(status_code=200)

    # Log visível no terminal do Docker indicando que o seu grupo passou
    logger.info("🎯 [OraculoUEMA] Mensagem aceita no grupo homologado: %s", msg.text)

    # Executa a chamada em background no Celery
    processar_mensagem_whatsapp.delay(
        remote_jid    = msg.remote_jid,
        sender_jid    = msg.sender_jid,
        text          = msg.text,
        push_name     = msg.push_name,
        is_group      = msg.is_group,
        mentioned_bot = msg.mentioned_bot,
        msg_key_id    = msg.msg_key_id,
        has_media     = msg.has_media,
        media_type    = msg.media_type,
    )
    
    return Response(status_code=200)