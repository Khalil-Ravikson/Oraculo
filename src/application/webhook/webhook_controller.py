from fastapi import APIRouter, Request, Response
from src.application.tasks.process_message_task import processar_mensagem_whatsapp
from src.infrastructure.webhook.dto import IncomingMessage
import logging, re

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


def _parse_evolution_payload(payload: dict) -> IncomingMessage | None:
    """Normaliza payload Evolution API v2.3 para DTO interno."""
    try:
        if payload.get("event") != "messages.upsert":
            return None

        data = payload.get("data", {})
        key  = data.get("key", {})

        if key.get("fromMe", True):
            return None

        msg       = data.get("message", {})
        text = (
            msg.get("conversation")
            or msg.get("extendedTextMessage", {}).get("text")
            or msg.get("imageMessage", {}).get("caption")
            or msg.get("videoMessage", {}).get("caption")
            or msg.get("documentMessage", {}).get("caption")
            or ""
        ).strip()

        remote_jid = key.get("participant", "")
        
        is_group   = remote_jid.endswith("@g.us")

        # Em grupos, o sender está em participant
        sender_jid = key.get("participant", remote_jid) if is_group else remote_jid

        # Detecta menção ao bot (@oraculo ou @bot)
        mentioned  = bool(re.search(r"@oraculo\b", text, re.I))

        has_media = data.get("messageType") not in (
                                                    "conversation", "extendedTextMessage", "reactionMessage", ""
                                                )
        media_type = data.get("messageType", "")

        return IncomingMessage(
            remote_jid    = remote_jid,
            sender_jid    = re.sub(r"[^\d]", "", sender_jid.split("@")[0]),
            text          = text,
            push_name     = data.get("pushName", ""),
            is_group      = is_group,
            mentioned_bot = mentioned,
            msg_key_id    = key.get("id", ""),
            has_media     = has_media,
            media_type    = media_type,
        )
    except Exception as e:
        logger.debug("parse_evolution_payload falhou: %s", e)
        return None


@router.post("/evolution")
async def webhook_evolution(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=200)

    msg = _parse_evolution_payload(payload)
    if msg is None or not msg.text and not msg.has_media:
        return Response(status_code=200)

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
    
# 👇 A CORREÇÃO ENTRA AQUI 👇
    try:
        from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
        gateway = EvolutionAdapter()
        await gateway.marcar_lida(remote_jid=msg.remote_jid, msg_id=msg.msg_key_id)
    except Exception as e:
        logger.warning("Falha ao marcar como lida: %s", e)
    
    return Response(status_code=200)
  