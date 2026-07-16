"""
EvolutionAdapter v2.3 — baseado na collection oficial.

ENDPOINTS USADOS:
  POST /message/sendText/{instance}         → enviar texto
  POST /message/sendMedia/{instance}        → enviar mídia (url ou base64)
  POST /message/sendButtons/{instance}      → botões interativos
  POST /message/sendList/{instance}         → listas interativas
  POST /message/sendReaction/{instance}     → reagir a mensagem
  POST /chat/sendPresence/{instance}        → "digitando..."
  POST /chat/getBase64FromMediaMessage/{instance} → download de mídia
  POST /chat/markMessageAsRead/{instance}   → marcar como lida
"""
from __future__ import annotations
import logging
import httpx
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "apikey": settings.EVOLUTION_API_KEY,
    }


def _base(path: str) -> str:
    base = settings.EVOLUTION_BASE_URL.rstrip("/")
    instance = settings.EVOLUTION_INSTANCE_NAME
    return f"{base}/{path}/{instance}"


class EvolutionAdapter:
    """
    Adapter para Evolution API v2.3.
    Todos os métodos são async.
    number = só dígitos, sem @s.whatsapp.net (Evolution converte internamente).
    """

    async def enviar_mensagem(self, number: str, text: str) -> bool:
        """POST /message/sendText/{instance}"""
        return await self._post(
            _base("message/sendText"),
            {"number": _clean_number(number), "text": text},
        )

    async def enviar_digitando(self, number: str, duration_ms: int = 2000) -> bool:
        """
        POST /chat/sendPresence/{instance}
        presence: composing | recording | paused
        """
        return await self._post(
            _base("chat/sendPresence"),
            {
                "number":   _clean_number(number),
                "delay":    duration_ms,
                "presence": "composing",
            },
        )

    async def marcar_lida(self, remote_jid: str, msg_id: str, from_me: bool = False) -> bool:
        """POST /chat/markMessageAsRead/{instance}"""
        return await self._post(
            _base("chat/markMessageAsRead"),
            {"readMessages": [{"remoteJid": remote_jid, "fromMe": from_me, "id": msg_id}]},
        )

    async def enviar_reacao(self, remote_jid: str, msg_id: str, emoji: str = "👍") -> bool:
        """POST /message/sendReaction/{instance}"""
        return await self._post(
            _base("message/sendReaction"),
            {
                "key": {
                    "remoteJid": remote_jid,
                    "fromMe":    False,
                    "id":        msg_id,
                },
                "reaction": emoji,
            },
        )

    async def enviar_midia_url(
        self,
        number: str,
        url: str,
        mediatype: str,        # "image" | "video" | "document" | "audio"
        mimetype: str,
        caption: str = "",
        filename: str = "",
    ) -> bool:
        """POST /message/sendMedia/{instance}"""
        body: dict = {
            "number":    _clean_number(number),
            "mediatype": mediatype,
            "mimetype":  mimetype,
            "media":     url,
        }
        if caption:
            body["caption"] = caption
        if filename:
            body["fileName"] = filename
        return await self._post(_base("message/sendMedia"), body)

    async def enviar_botoes(
        self,
        number: str,
        title: str,
        description: str,
        buttons: list[dict],
        footer: str = "",
    ) -> bool:
        """
        POST /message/sendButtons/{instance}
        button exemplo: {"type": "reply", "displayText": "Sim", "id": "btn_sim"}
        """
        body = {
            "number":      _clean_number(number),
            "title":       title,
            "description": description,
            "buttons":     buttons,
        }
        if footer:
            body["footer"] = footer
        return await self._post(_base("message/sendButtons"), body)

    async def enviar_lista(
        self,
        number: str,
        title: str,
        description: str,
        button_text: str,
        sections: list[dict],
        footer: str = "",
    ) -> bool:
        """POST /message/sendList/{instance}"""
        body = {
            "number":      _clean_number(number),
            "title":       title,
            "description": description,
            "buttonText":  button_text,
            "sections":    sections,
        }
        if footer:
            body["footerText"] = footer
        return await self._post(_base("message/sendList"), body)

    async def baixar_midia_base64(self, msg_key_id: str) -> tuple[str, str, str]:
        """
        POST /chat/getBase64FromMediaMessage/{instance}
        Retorna (base64, mimetype, fileName).
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    _base("chat/getBase64FromMediaMessage"),
                    headers=_headers(),
                    json={"message": {"key": {"id": msg_key_id}}, "convertToMp4": False},
                )
                resp.raise_for_status()
                data = resp.json()
            return (
                data.get("base64", ""),
                data.get("mimetype", "application/octet-stream"),
                data.get("fileName", "arquivo"),
            )
        except Exception as e:
            logger.error("❌ baixar_midia_base64 [%s]: %s", msg_key_id[:20], e)
            return "", "", ""

    # ── Helper interno ────────────────────────────────────────────────────────

    async def _post(self, url: str, body: dict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, headers=_headers(), json=body)
                if resp.status_code >= 400:
                    logger.error(
                        "❌ Evolution %s → HTTP %d | Body: %s | Resp: %s",
                        url.split("/")[-2], resp.status_code, body, resp.text[:200],
                    )
                    return False
            return True
        except httpx.TimeoutException:
            logger.error("❌ Evolution timeout: %s", url)
            return False
        except Exception as e:
            logger.error("❌ Evolution _post [%s]: %s", url, e)
            return False


def _clean_number(jid: str) -> str:
    """
    Evolution v2.3 exige o remoteJid completo (com @s.whatsapp.net ou @g.us).
    """
    if not jid:
        return ""
    if "@g.us" in jid or "@s.whatsapp.net" in jid:
        return jid
        
    # Remove não-dígitos para garantir um formato limpo antes de adicionar o sufixo
    import re
    numeros = re.sub(r"\D", "", jid)
    
    # Adiciona 55 apenas para números curtos (brasileiros padrão sem DDI)
    if not numeros.startswith("55") and len(numeros) <= 11:
        numeros = f"55{numeros}"
        
    return f"{numeros}@s.whatsapp.net"