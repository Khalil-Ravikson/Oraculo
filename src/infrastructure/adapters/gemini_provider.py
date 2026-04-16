"""
infrastructure/adapters/gemini_provider.py — GeminiProvider (google-genai >= 0.5.0)
=====================================================================================
Implementa ILLMProvider para o modelo Gemini.
RESPONSABILIDADE ÚNICA: comunicação com a API Gemini.
A lógica de memória vive em src/memory/container.py — NÃO misturar.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Type, TypeVar

from pydantic import BaseModel

from src.domain.ports.llm_Provider import ILLMProvider, LLMResponse

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class GeminiProvider:
    """
    Provider Gemini via google-genai SDK.
    Implementa ILLMProvider — o domínio nunca importa google.genai diretamente.

    Thread-safe: o cliente Gemini é stateless e pode ser compartilhado.
    Singleton recomendado via _get_default_provider().
    """

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        import google.genai as genai
        from src.infrastructure.settings import settings

        self._model  = model   or settings.GEMINI_MODEL
        self._client = genai.Client(api_key=api_key or settings.GEMINI_API_KEY)

    # ─── Geração de texto livre ───────────────────────────────────────────────

    async def gerar_resposta_async(
        self,
        prompt:             str,
        system_instruction: str   = "",
        temperatura:        float = 0.2,
        max_tokens:         int   = 1024,
    ) -> LLMResponse:
        """Geração assíncrona de texto livre (await nativo google-genai)."""
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction = system_instruction or None,
            temperature        = temperatura,
            max_output_tokens  = max_tokens,
        )

        try:
            response = await self._client.aio.models.generate_content(
                model    = self._model,
                contents = prompt,
                config   = config,
            )
            text  = response.text or ""
            usage = response.usage_metadata

            return LLMResponse(
                conteudo      = text,
                model         = self._model,
                input_tokens  = getattr(usage, "prompt_token_count",     0),
                output_tokens = getattr(usage, "candidates_token_count", 0),
                sucesso       = bool(text),
            )
        except Exception as exc:
            logger.exception("❌ GeminiProvider.gerar_resposta_async | erro: %s", exc)
            return LLMResponse(
                conteudo = "",
                model    = self._model,
                sucesso  = False,
                erro     = str(exc)[:300],
            )

    # ─── Geração estruturada (Pydantic) ───────────────────────────────────────

    async def gerar_resposta_estruturada_async(
        self,
        prompt:             str,
        response_schema:    Type[T],
        system_instruction: str   = "",
        temperatura:        float = 0.0,
    ) -> T | None:
        """
        Geração com structured output — Gemini garante JSON válido contra o schema.
        Temperatura 0.0 por padrão: determinístico para extração de dados.
        """
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction = system_instruction or None,
            temperature        = temperatura,
            max_output_tokens  = 1024,
            response_mime_type = "application/json",
            response_schema    = response_schema,
        )

        try:
            response = await self._client.aio.models.generate_content(
                model    = self._model,
                contents = prompt,
                config   = config,
            )
            raw  = response.text or "{}"
            data = json.loads(raw)
            return response_schema(**data)

        except Exception as exc:
            logger.exception(
                "❌ GeminiProvider.gerar_resposta_estruturada_async "
                "| schema=%s | erro: %s",
                response_schema.__name__, exc,
            )
            return None

    # ─── Versão síncrona (para Celery workers) ────────────────────────────────

    def gerar_resposta_sincrono(
        self,
        prompt:      str,
        temperatura: float = 0.2,
        max_tokens:  int   = 1024,
    ) -> LLMResponse:
        """
        Versão síncrona para tasks Celery que não podem usar await.
        Usa o cliente síncrono do google-genai.
        """
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature       = temperatura,
            max_output_tokens = max_tokens,
        )

        try:
            response = self._client.models.generate_content(
                model    = self._model,
                contents = prompt,
                config   = config,
            )
            text  = response.text or ""
            usage = response.usage_metadata

            return LLMResponse(
                conteudo      = text,
                model         = self._model,
                input_tokens  = getattr(usage, "prompt_token_count",     0),
                output_tokens = getattr(usage, "candidates_token_count", 0),
                sucesso       = bool(text),
            )
        except Exception as exc:
            logger.exception("❌ GeminiProvider.gerar_resposta_sincrono | erro: %s", exc)
            return LLMResponse(conteudo="", model=self._model, sucesso=False, erro=str(exc)[:300])


# ─── Singleton ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_default_provider() -> GeminiProvider:
    return GeminiProvider()


def get_gemini_provider() -> GeminiProvider:
    """Retorna o singleton do GeminiProvider."""
    return _get_default_provider()