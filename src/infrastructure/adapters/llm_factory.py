"""
infrastructure/adapters/llm_factory.py — resolução dinâmica de provider LLM
================================================================================
Ponto único de decisão de "qual provider usar" (Gemini/DeepSeek/Groq) e
ponto único de instrumentação de custo/telemetria. Os call sites que hoje
chamam `genai.Client` direto (achado em `analise_custo_real_llm.md` §3)
migram para `get_llm_provider()` em vez de instanciar client próprio.

Precedência (maior pra menor):
  1. Override por agente (`admin:agent:{nome}:llm_provider`/`llm_model` no
     Redis — cache de leitura rápida do que está gravado em
     `agentes_catalogo.llm_provider`/`llm_model`, editável via `/hub/agents`).
  2. Troca em runtime, global (`admin:llm_provider` no Redis — mesmo padrão
     de `admin:agent:{nome}:enabled` em
     `capabilities/persistence/agent_config.py`), sem restart.
  3. `settings.LLM_PROVIDER` (.env, default "gemini").

Sem `lru_cache` no resultado — precisa reagir a troca em runtime a cada
chamada. O custo de 1-2 `GET`s Redis extra é desprezível perto do custo de
uma chamada LLM real.
"""
from __future__ import annotations

import logging
import time
from typing import Type, TypeVar

from pydantic import BaseModel

from src.domain.ports.llm_Provider import ILLMProvider, LLMResponse
from src.infrastructure.observability import pricing

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

_CHAVE_PROVIDER_GLOBAL = "admin:llm_provider"
_PROVIDERS_VALIDOS = ("gemini", "deepseek", "groq")


def _instanciar(provider_name: str, modelo: str | None = None) -> ILLMProvider:
    from src.infrastructure.settings import settings

    if provider_name == "deepseek":
        from src.infrastructure.adapters.openai_compatible_provider import OpenAICompatibleProvider
        return OpenAICompatibleProvider(
            provider_name="deepseek",
            base_url=settings.DEEPSEEK_BASE_URL,
            api_key=settings.DEEPSEEK_API_KEY,
            model=modelo or settings.DEEPSEEK_MODEL,
        )
    if provider_name == "groq":
        from src.infrastructure.adapters.openai_compatible_provider import OpenAICompatibleProvider
        return OpenAICompatibleProvider(
            provider_name="groq",
            base_url=settings.GROQ_BASE_URL,
            api_key=settings.GROQ_API_KEY,
            model=modelo or settings.GROQ_MODEL,
        )
    from src.infrastructure.adapters.gemini_provider import GeminiProvider
    return GeminiProvider(model=modelo)


def _provider_global_ativo() -> str:
    from src.infrastructure.settings import settings
    try:
        from src.infrastructure.redis_client import get_redis_text
        raw = get_redis_text().get(_CHAVE_PROVIDER_GLOBAL)
        if raw:
            valor = raw if isinstance(raw, str) else raw.decode()
            if valor in _PROVIDERS_VALIDOS:
                return valor
    except Exception as exc:
        logger.warning("⚠️ [LLM_FACTORY] Falha ao ler provider global no Redis: %s", exc)
    return settings.LLM_PROVIDER if settings.LLM_PROVIDER in _PROVIDERS_VALIDOS else "gemini"


def _override_do_agente(agente: str) -> tuple[str | None, str | None]:
    """Lê o cache Redis do override por agente (escrito por
    `set_agent_llm_override`, mesmo request que grava em Postgres). Falha
    silenciosa cai em "sem override" — nunca bloqueia uma resposta ao
    usuário por causa de telemetria/config."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        raw_provider = r.get(f"admin:agent:{agente}:llm_provider")
        raw_model    = r.get(f"admin:agent:{agente}:llm_model")
        provider = (raw_provider if isinstance(raw_provider, str) else raw_provider.decode()) if raw_provider else None
        modelo   = (raw_model if isinstance(raw_model, str) else raw_model.decode()) if raw_model else None
        return provider, modelo
    except Exception:
        return None, None


def get_llm_provider(agente: str | None = None, rota: str = "") -> "MonitoredLLMProvider":
    """Resolve o provider ativo (global + override opcional por agente) e
    devolve envolvido em `MonitoredLLMProvider` — toda chamada feita
    através do retorno grava telemetria real automaticamente (Postgres +
    Prometheus), sem o call site precisar fazer nada a mais."""
    provider_name = _provider_global_ativo()
    modelo = None

    if agente:
        override_provider, override_modelo = _override_do_agente(agente)
        if override_provider and override_provider in _PROVIDERS_VALIDOS:
            provider_name = override_provider
        if override_modelo:
            modelo = override_modelo

    provider = _instanciar(provider_name, modelo)
    return MonitoredLLMProvider(provider, rota_hint=rota)


class MonitoredLLMProvider:
    """Envolve qualquer ILLMProvider real e registra telemetria (Postgres
    `metricas_llm` + Prometheus) a cada chamada — ponto único de
    instrumentação. Ver `analise_custo_real_llm.md` §4/§7: antes desta
    classe, telemetria existia em pedaços desconectados (nada persistente
    sobrevivia mais de 1h)."""

    def __init__(self, provider: ILLMProvider, rota_hint: str = "") -> None:
        self._provider = provider
        self._rota_hint = rota_hint

    @property
    def provider_name(self) -> str:
        return getattr(self._provider, "provider_name", "desconhecido")

    @property
    def model(self) -> str:
        return getattr(self._provider, "model", "")

    @property
    def ultimo_uso_tokens(self) -> tuple[int, int]:
        """Tokens (in, out) da última chamada estruturada — útil para call
        sites que precisam alimentar telemetria própria (ex: o cache
        `eval:tokens:*` de `registrar_tokens_redis`, consumido pelo
        simulador de avaliação do `/hub`)."""
        return getattr(self._provider, "ultimo_uso_tokens", (0, 0))

    async def gerar_resposta_async(
        self,
        prompt:             str,
        system_instruction: str   = "",
        temperatura:        float = 0.2,
        max_tokens:         int   = 1024,
        *,
        user_id: str = "", rota: str = "",
    ) -> LLMResponse:
        from src.infrastructure.observability.tracing import get_tracer, llm_span

        t0 = time.monotonic()
        with llm_span(get_tracer(), "chat", self.provider_name, self.model, rota or self._rota_hint) as span:
            resp = await self._provider.gerar_resposta_async(prompt, system_instruction, temperatura, max_tokens)
            span.set_attribute("gen_ai.usage.input_tokens", resp.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", resp.output_tokens)
        ms = int((time.monotonic() - t0) * 1000)
        await self._registrar_async(resp.input_tokens, resp.output_tokens, ms, user_id, rota or self._rota_hint)
        return resp

    async def gerar_resposta_estruturada_async(
        self,
        prompt:             str,
        response_schema:    Type[T],
        system_instruction: str   = "",
        temperatura:        float = 0.0,
        *,
        user_id: str = "", rota: str = "",
    ) -> T | None:
        from src.infrastructure.observability.tracing import get_tracer, llm_span

        t0 = time.monotonic()
        with llm_span(get_tracer(), "chat_structured", self.provider_name, self.model, rota or self._rota_hint) as span:
            resultado = await self._provider.gerar_resposta_estruturada_async(
                prompt, response_schema, system_instruction, temperatura,
            )
            tokens_in, tokens_out = getattr(self._provider, "ultimo_uso_tokens", (0, 0))
            span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
            span.set_attribute("gen_ai.usage.output_tokens", tokens_out)
        ms = int((time.monotonic() - t0) * 1000)
        await self._registrar_async(tokens_in, tokens_out, ms, user_id, rota or self._rota_hint)
        return resultado

    def gerar_resposta_sincrono(
        self,
        prompt:      str,
        temperatura: float = 0.2,
        max_tokens:  int   = 1024,
        *,
        user_id: str = "", rota: str = "",
    ) -> LLMResponse:
        from src.infrastructure.observability.tracing import get_tracer, llm_span

        t0 = time.monotonic()
        with llm_span(get_tracer(), "chat", self.provider_name, self.model, rota or self._rota_hint) as span:
            resp = self._provider.gerar_resposta_sincrono(prompt, temperatura, max_tokens)
            span.set_attribute("gen_ai.usage.input_tokens", resp.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", resp.output_tokens)
        ms = int((time.monotonic() - t0) * 1000)
        self._registrar_sync(resp.input_tokens, resp.output_tokens, ms, user_id, rota or self._rota_hint)
        return resp

    # ─── Telemetria (nunca deve derrubar a resposta ao usuário) ───────────────

    async def _registrar_async(self, tokens_in: int, tokens_out: int, ms: int, user_id: str, rota: str) -> None:
        custo = pricing.calcular_custo_usd(self.provider_name, self.model, tokens_in, tokens_out)
        try:
            from src.infrastructure.database.session import AsyncSessionLocal
            from src.infrastructure.repositories.observability_repository import ObservabilityRepository

            async with AsyncSessionLocal() as session:
                repo = ObservabilityRepository(session)
                await repo.salvar_metrica_llm(
                    user_id=user_id, rota=rota,
                    tokens_entrada=tokens_in, tokens_saida=tokens_out,
                    latencia_ms=ms, custo_usd=custo,
                    modelo=self.model, provider=self.provider_name,
                )
        except Exception as exc:
            logger.warning("⚠️ [MONITORED_LLM] falha ao gravar metricas_llm: %s", exc)
        self._prometheus(tokens_in, tokens_out, custo, ms, rota)

    def _registrar_sync(self, tokens_in: int, tokens_out: int, ms: int, user_id: str, rota: str) -> None:
        custo = pricing.calcular_custo_usd(self.provider_name, self.model, tokens_in, tokens_out)
        try:
            from src.infrastructure.repositories.observability_repository import salvar_metrica_sync
            salvar_metrica_sync(
                user_id=user_id, rota=rota,
                tokens_entrada=tokens_in, tokens_saida=tokens_out,
                latencia_ms=ms, custo_usd=custo,
                modelo=self.model, provider=self.provider_name,
            )
        except Exception as exc:
            logger.warning("⚠️ [MONITORED_LLM] falha ao gravar metricas_llm (sync): %s", exc)
        self._prometheus(tokens_in, tokens_out, custo, ms, rota)

    def _prometheus(self, tokens_in: int, tokens_out: int, custo: float, ms: int, rota: str) -> None:
        try:
            from src.infrastructure.observability.metrics import PrometheusMetrics
            PrometheusMetrics().record_llm_usage(
                input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=custo,
                latency_ms=ms, provider=self.provider_name, modelo=self.model, rota=rota,
            )
        except Exception as exc:
            logger.warning("⚠️ [MONITORED_LLM] falha ao registrar Prometheus: %s", exc)
