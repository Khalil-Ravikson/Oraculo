"""
src/application/runtime/audio_intake.py
================================================================================
Ingestão de áudio recebido (STT) + detecção do pedido "responda em áudio" —
extraído de `dispatcher.py` (Plano A / Fase 2, Parte C: pré-requisito da
aposentadoria do `dispatcher.py` legado).

Estas funções eram importadas de `dispatcher.py` por `dispatcher_langgraph.py`
(`_transcrever_audio_recebido`) e por `process_message_task.py`
(`_quer_resposta_em_audio` / `_remover_pedido_audio`) — dependências que
travavam a remoção do dispatcher legado. São puramente mecânicas (download +
despacho de worker + polling / regex), sem regra de negócio.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

logger = logging.getLogger(__name__)

_STT_TIMEOUT_S = 20.0   # Celery pickup (queue=media) + chamada Gemini + polling
_MAX_AUDIO_MB  = 16     # mesmo cap de _MAX_ENVIO_MB em worker_media_download.py
_POLL_INTERVAL_S = 0.2

# Gatilho opt-in pra resposta sair também em áudio (Fase 3 do roadmap
# multimodal) — verbo "mandar/em/por (forma de) áudio". Deliberadamente não é
# o padrão automático: TTS ainda é caro (~15s de cold-load na 1ª chamada por
# processo worker) e nem toda resposta faz sentido em voz. Limitação
# conhecida: só detecta o pedido no TEXTO digitado (legenda/mensagem) — se o
# pedido for falado DENTRO de uma nota de voz, não é capturado aqui (checagem
# roda sobre o texto bruto recebido, antes/independente da transcrição STT).
_RE_AUDIO_REPLY = re.compile(
    r'\b(em|por|de)\s+(forma\s+de\s+)?áudio\b|\bmand(a|ar|e|em)\s+(um\s+|uma\s+mensagem\s+de\s+)?áudio\b',
    re.I,
)


def _quer_resposta_em_audio(text: str) -> bool:
    """True se o usuário pediu explicitamente a resposta em áudio."""
    return bool(_RE_AUDIO_REPLY.search(text or ""))


def _remover_pedido_audio(text: str) -> str:
    """
    Remove a frase-gatilho ("em áudio", "manda um áudio"...) do texto antes
    de virar `message` pro RAG/orchestrator/synthesis.

    Bug real de produção encontrado testando ao vivo: sem isso, o LLM de
    síntese via a frase completa ("Me explique em áudio sobre o Office 365")
    e respondia SOBRE o pedido de áudio ("não consigo te explicar em áudio,
    sou um assistente de texto") em vez de responder a pergunta de verdade —
    a frase-gatilho é sinal só pro roteamento de ENTREGA (`_quer_resposta_em_audio`,
    checado à parte sobre o texto original), não faz parte da pergunta em si.
    """
    limpo = _RE_AUDIO_REPLY.sub("", text or "")
    limpo = re.sub(r"\s{2,}", " ", limpo).strip(" ,.")
    return limpo or text


async def _transcrever_audio_recebido(r, user_context: dict, session_id: str) -> str | None:
    """
    Baixa o áudio recebido via Evolution API e despacha `worker_audio_to_text`
    (queue=media) — mantém o worker `default` (CELERY_CONCURRENCY=1) livre
    enquanto a transcrição roda, em vez de chamar o STT inline aqui. Faz
    polling em `plan:results:{plan_id}:{step_id}`, o mesmo Redis que o worker
    já escreve. Retorna None em qualquer falha (download vazio, áudio grande
    demais, timeout, erro de STT) — quem chama decide a mensagem de erro.
    """
    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
    from src.application.workers.registry import dispatch as worker_dispatch
    from src.capabilities.persistence.redis_state import get_result_cache
    from src.infrastructure.observability.metrics import get_metrics
    from src.infrastructure.settings import settings

    msg_key_id = user_context.get("msg_key_id", "")
    t_stt      = time.monotonic()
    metrics    = get_metrics()

    def _falhar() -> None:
        metrics.observe_stt(settings.STT_PROVIDER, int((time.monotonic() - t_stt) * 1000), False)

    gateway = EvolutionAdapter()
    audio_b64, mimetype, _filename = await gateway.baixar_midia_base64(msg_key_id)
    if not audio_b64:
        logger.warning("⚠️  [STT] Download de áudio vazio | msg_key_id=%s", msg_key_id[:20])
        _falhar()
        return None

    tamanho_mb = len(audio_b64) * 3 / 4 / (1024 * 1024)
    if tamanho_mb > _MAX_AUDIO_MB:
        logger.warning("⚠️  [STT] Áudio grande demais (%.1fMB > %dMB) | msg_key_id=%s",
                       tamanho_mb, _MAX_AUDIO_MB, msg_key_id[:20])
        _falhar()
        return None

    plan_id = f"fast_stt_{session_id[-6:]}_{int(time.time() * 1000)}"
    step_id = "s_stt"
    task_id = worker_dispatch("audio_to_text", {
        "plan_id": plan_id, "session_id": session_id, "step_id": step_id,
        "audio_b64": audio_b64, "mime_type": mimetype or "audio/ogg",
    })
    if task_id is None:
        _falhar()
        return None

    deadline = time.monotonic() + _STT_TIMEOUT_S
    payload  = None
    while time.monotonic() < deadline:
        payload = await get_result_cache(r, plan_id, step_id)
        if payload is not None:
            break
        await asyncio.sleep(_POLL_INTERVAL_S)

    ms = int((time.monotonic() - t_stt) * 1000)
    ok = bool(payload and payload.get("status") == "ok" and payload.get("transcription"))
    metrics.observe_stt(settings.STT_PROVIDER, ms, ok)

    if not ok:
        logger.warning("⚠️  [STT] Falha ou timeout | plan=%s | payload=%s", plan_id, payload)
        return None
    return payload["transcription"]
