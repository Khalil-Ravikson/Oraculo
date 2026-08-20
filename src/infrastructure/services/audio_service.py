"""
AudioService — orquestra STT/TTS via providers configuráveis.

Não fala com Gemini/gTTS diretamente — delega para o provider selecionado
por settings.STT_PROVIDER/TTS_PROVIDER (ver infrastructure/adapters/
{stt,tts}_factory.py). Trocar provider é mudança de config, não de código.

Ponto único de telemetria de custo multimodal (STT/TTS) — mesmo espírito de
`MonitoredLLMProvider` (llm_factory.py), mas para os providers de áudio:
grava em `metricas_llm` (Postgres, rota="stt"/"tts") + Prometheus, pra
aparecer somado ao custo de texto em `/hub/llm-custo` em vez de ficar isolado
nas métricas `oraculo_{stt,tts}_*` (ver analise_custo_real_llm.md/plano de
observabilidade — antes disso não havia custo em dinheiro nenhum pra STT/TTS).
"""
from __future__ import annotations
import time
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


async def _registrar_uso_multimodal(
    rota: str, provider: str, modelo: str,
    tokens_in: int, tokens_out: int, custo_usd: float, ms: int,
) -> None:
    """Grava telemetria de STT/TTS no mesmo pipeline do texto (Postgres +
    Prometheus). Nunca propaga exceção — telemetria não pode derrubar a
    transcrição/síntese real."""
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from src.infrastructure.repositories.observability_repository import ObservabilityRepository

        async with AsyncSessionLocal() as session:
            repo = ObservabilityRepository(session)
            await repo.salvar_metrica_llm(
                user_id="", rota=rota,
                tokens_entrada=tokens_in, tokens_saida=tokens_out,
                latencia_ms=ms, custo_usd=custo_usd,
                modelo=modelo, provider=provider,
            )
    except Exception as exc:
        logger.warning("⚠️ [AUDIO_SERVICE] falha ao gravar metricas_llm (%s): %s", rota, exc)
    try:
        from src.infrastructure.observability.metrics import PrometheusMetrics
        PrometheusMetrics().record_llm_usage(
            input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=custo_usd,
            latency_ms=ms, provider=provider, modelo=modelo, rota=rota,
        )
    except Exception as exc:
        logger.warning("⚠️ [AUDIO_SERVICE] falha ao registrar Prometheus (%s): %s", rota, exc)


@dataclass
class AudioResult:
    ok: bool
    text: str = ""
    audio_path: str = ""
    error: str = ""


class AudioService:

    # ── STT: áudio → texto ──────────────────────────────────────────────────

    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> AudioResult:
        """
        Transcreve áudio via o provider de STT configurado (settings.STT_PROVIDER).
        mime_type: audio/ogg | audio/mp4 | audio/wav | audio/webm
        """
        from src.infrastructure.adapters.stt_factory import get_stt_provider
        from src.infrastructure.settings import settings
        from src.infrastructure.observability import pricing
        from src.infrastructure.observability.tracing import get_tracer, llm_span

        provider_name = settings.STT_PROVIDER
        t0 = time.monotonic()
        provider = get_stt_provider()
        with llm_span(get_tracer(), "stt", provider_name, settings.GEMINI_MODEL if provider_name == "gemini" else provider_name, "stt") as span:
            result = await provider.transcribe(audio_bytes, mime_type)
            span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
        ms = int((time.monotonic() - t0) * 1000)

        # Só o Gemini cobra por token hoje — outros providers de STT (se
        # algum dia locais/whisper) ficam a custo 0 sem precisar de tabela nova.
        custo = 0.0
        if provider_name == "gemini":
            custo = pricing.calcular_custo_usd("gemini", settings.GEMINI_MODEL,
                                                result.input_tokens, result.output_tokens)
        await _registrar_uso_multimodal(
            rota="stt", provider=provider_name, modelo=settings.GEMINI_MODEL if provider_name == "gemini" else provider_name,
            tokens_in=result.input_tokens, tokens_out=result.output_tokens, custo_usd=custo, ms=ms,
        )
        return AudioResult(ok=result.ok, text=result.text, error=result.error)

    # ── TTS: texto → áudio ──────────────────────────────────────────────────

    async def synthesize(self, text: str, lang: str = "pt") -> AudioResult:
        """
        Converte texto em áudio via o provider de TTS configurado (settings.TTS_PROVIDER).
        Retorna caminho do arquivo temporário gerado.
        """
        from src.infrastructure.adapters.tts_factory import get_tts_provider
        from src.infrastructure.settings import settings
        from src.infrastructure.observability.tracing import get_tracer, llm_span

        provider_name = settings.TTS_PROVIDER
        t0 = time.monotonic()
        provider = get_tts_provider()
        with llm_span(get_tracer(), "tts", provider_name, provider_name, "tts") as span:
            result = await provider.synthesize(text, lang)
            span.set_attribute("gen_ai.usage.input_tokens", len(text) // 4)
        ms = int((time.monotonic() - t0) * 1000)

        # Kokoro/gTTS rodam localmente — custo real é sempre $0. tokens_in
        # aqui é só uma proxy de volume (chars/4, mesma heurística de
        # tokenização usada informalmente no resto do projeto), não cobrado.
        await _registrar_uso_multimodal(
            rota="tts", provider=provider_name, modelo=provider_name,
            tokens_in=len(text) // 4, tokens_out=0, custo_usd=0.0, ms=ms,
        )
        return AudioResult(ok=result.ok, audio_path=result.audio_path, error=result.error)


_audio_service: AudioService | None = None

def get_audio_service() -> AudioService:
    global _audio_service
    if _audio_service is None:
        _audio_service = AudioService()
    return _audio_service
