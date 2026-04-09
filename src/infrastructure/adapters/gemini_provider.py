"""
infrastructure/adapters/gemini_provider.py — Sprint 1 (Langfuse @observe_llm)
==============================================================================

MUDANÇA vs versão anterior:
  Adicionado @observe_llm no método gerar_resposta_async() e 
  gerar_resposta_estruturada_async().

  O decorator é NO-OP se Langfuse não estiver configurado.
  Zero impacto em performance quando desativado.

COMO VER NO LANGFUSE:
  http://localhost:3000 → Traces → "gemini_gerar_resposta"
  Cada chamada LLM mostra: prompt, resposta, tokens, latência.
"""
import json
import logging
from typing import Any, Type, TypeVar
from pydantic import BaseModel
import google.genai as genai
from google.genai import types
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

# Sprint 1: import do decorator de observabilidade
from src.infrastructure.observability.langfuse_client import observe_llm

from src.domain.ports.llm_Provider import ILLMProvider, LLMResponse
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)
T = TypeVar('T', bound=BaseModel)


def _is_retryable(exc: BaseException) -> bool:
    err = str(exc).lower()
    return any(term in err for term in ["429", "quota", "rate limit", "503", "overloaded", "timeout"])


class GeminiProvider(ILLMProvider):
    MODELO_PRIMARIO = "gemini-2.0-flash-lite"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY não configurada")
        self.client = genai.Client(api_key=self.api_key)
        logger.info("✅ GeminiProvider inicializado.")

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _chamar_gemini_raw(
        self,
        prompt: str,
        system_instruction: str,
        temperatura: float,
        max_tokens: int,
        response_schema: Type[BaseModel] | None = None,
    ) -> LLMResponse:
        """Chamada crua ao Gemini — sem decorators, testável isoladamente."""
        config_kwargs: dict[str, Any] = {
            "temperature": temperatura,
            "max_output_tokens": max_tokens,
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"]    = response_schema

        config   = types.GenerateContentConfig(**config_kwargs)
        resposta = await self.client.aio.models.generate_content(
            model=self.MODELO_PRIMARIO,
            contents=prompt,
            config=config,
        )
        usage = getattr(resposta, "usage_metadata", None)
        return LLMResponse(
            conteudo=resposta.text or "",
            model=self.MODELO_PRIMARIO,
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
            sucesso=True,
        )

    # ── Sprint 1: @observe_llm injetado aqui ──────────────────────────────────
    @observe_llm(name="gemini_gerar_resposta", capture_input=True, capture_output=True)
    async def gerar_resposta_async(
        self,
        prompt: str,
        system_instruction: str = "",
        temperatura: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """
        Gera resposta de texto livre.
        Traceable no Langfuse: cada chamada cria um span "gemini_gerar_resposta".
        """
        try:
            return await self._chamar_gemini_raw(
                prompt, system_instruction, temperatura, max_tokens
            )
        except Exception as e:
            logger.error("❌ Falha no Gemini: %s", e)
            return LLMResponse(
                conteudo="",
                model=self.MODELO_PRIMARIO,
                erro=str(e),
                sucesso=False,
            )

    # ── Sprint 1: @observe_llm injetado aqui ──────────────────────────────────
    @observe_llm(name="gemini_estruturado", capture_input=True, capture_output=False)
    async def gerar_resposta_estruturada_async(
        self,
        prompt: str,
        response_schema: Type[T],
        system_instruction: str = "",
        temperatura: float = 0.0,
    ) -> T | None:
        """
        Gera resposta JSON validada pelo Pydantic.
        Traceable: span "gemini_estruturado" — útil para debug do roteamento.
        """
        try:
            resposta_llm = await self._chamar_gemini_raw(
                prompt, system_instruction, temperatura,
                max_tokens=1024,
                response_schema=response_schema,
            )
            if not resposta_llm.sucesso or not resposta_llm.conteudo:
                return None
            dict_dados = json.loads(resposta_llm.conteudo)
            return response_schema(**dict_dados)
        except Exception as e:
            logger.error("❌ Erro estruturado Gemini: %s", e)
            return None