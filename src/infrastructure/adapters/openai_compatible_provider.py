"""
infrastructure/adapters/openai_compatible_provider.py
========================================================
Implementa ILLMProvider para qualquer provedor com endpoint compatível
OpenAI (`POST {base_url}/chat/completions`) — cobre DeepSeek e Groq com UMA
classe genérica, em vez de um adapter bespoke por provedor (os dois expõem
o mesmo formato de API):

  - DeepSeek: base_url=https://api.deepseek.com/v1, modelo "deepseek-chat"
    (docs: https://api-docs.deepseek.com)
  - Groq:     base_url=https://api.groq.com/openai/v1,
    modelo ex. "llama-3.3-70b-versatile"
    (docs: https://console.groq.com/docs/openai)

Usa `httpx` (já é dependência do projeto via rest_lab) — sem adicionar SDK
novo só para isto.

Saída estruturada: nem todo provedor OpenAI-compatible garante JSON Schema
estrito (diferente do `response_schema` nativo do Gemini) — usamos
`response_format: {"type": "json_object"}` (JSON mode, suportado pelos
dois) + o schema Pydantic embutido em texto no system prompt, com 1 retry
se a validação Pydantic falhar na primeira resposta.
"""
from __future__ import annotations

import json
import logging
from typing import Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from src.domain.ports.llm_Provider import ILLMProvider, LLMResponse

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0


class OpenAICompatibleProvider:
    """Adapter genérico ILLMProvider para APIs compatíveis com OpenAI."""

    def __init__(
        self,
        provider_name: str,
        base_url:      str,
        api_key:       str,
        model:         str,
    ) -> None:
        self.provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key  = api_key
        self._model    = model
        # Ver GeminiProvider.ultimo_uso_tokens — mesmo side-channel de
        # telemetria pra chamadas estruturadas.
        self.ultimo_uso_tokens: tuple[int, int] = (0, 0)

    @property
    def model(self) -> str:
        return self._model

    async def _with_backoff(self, func, *args, **kwargs):
        from tenacity import retry, stop_after_attempt, wait_exponential

        @retry(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            reraise=True,
        )
        async def _execute():
            return await func(*args, **kwargs)

        return await _execute()

    async def _chamar(self, messages: list[dict], temperatura: float, max_tokens: int,
                       response_format: dict | None = None) -> dict:
        payload = {
            "model":       self._model,
            "messages":    messages,
            "temperature": temperatura,
            "max_tokens":  max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        async def _post():
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()

        return await self._with_backoff(_post)

    # ─── Geração de texto livre ───────────────────────────────────────────────

    async def gerar_resposta_async(
        self,
        prompt:             str,
        system_instruction: str   = "",
        temperatura:        float = 0.2,
        max_tokens:         int   = 1024,
    ) -> LLMResponse:
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        try:
            data  = await self._chamar(messages, temperatura, max_tokens)
            texto = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})

            return LLMResponse(
                conteudo      = texto,
                model         = self._model,
                input_tokens  = usage.get("prompt_tokens", 0),
                output_tokens = usage.get("completion_tokens", 0),
                sucesso       = bool(texto),
            )
        except Exception as exc:
            logger.exception("❌ %s.gerar_resposta_async | erro: %s", self.provider_name, exc)
            return LLMResponse(conteudo="", model=self._model, sucesso=False, erro=str(exc)[:300])

    # ─── Geração estruturada (Pydantic via JSON mode) ─────────────────────────

    async def gerar_resposta_estruturada_async(
        self,
        prompt:             str,
        response_schema:    Type[T],
        system_instruction: str   = "",
        temperatura:        float = 0.0,
    ) -> T | None:
        schema_json = json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
        instrucao_schema = (
            f"{system_instruction}\n\n"
            f"Responda SOMENTE com um JSON válido que siga exatamente este schema "
            f"(sem markdown, sem texto fora do JSON): {schema_json}"
        ).strip()

        messages = [
            {"role": "system", "content": instrucao_schema},
            {"role": "user", "content": prompt},
        ]

        for tentativa in range(2):  # 1 tentativa + 1 retry em falha de validação
            try:
                data  = await self._chamar(
                    messages, temperatura, max_tokens=1024,
                    response_format={"type": "json_object"},
                )
                texto = data["choices"][0]["message"]["content"] or "{}"
                usage = data.get("usage", {})
                self.ultimo_uso_tokens = (
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )
                return response_schema(**json.loads(texto))
            except (ValidationError, json.JSONDecodeError, KeyError, IndexError) as exc:
                logger.warning(
                    "⚠️ %s.gerar_resposta_estruturada_async | tentativa %d falhou | schema=%s | %s",
                    self.provider_name, tentativa + 1, response_schema.__name__, exc,
                )
                continue
            except Exception as exc:
                logger.exception(
                    "❌ %s.gerar_resposta_estruturada_async | schema=%s | erro: %s",
                    self.provider_name, response_schema.__name__, exc,
                )
                return None
        return None

    # ─── Versão síncrona (para Celery workers) ────────────────────────────────

    def gerar_resposta_sincrono(
        self,
        prompt:      str,
        temperatura: float = 0.2,
        max_tokens:  int   = 1024,
    ) -> LLMResponse:
        messages = [{"role": "user", "content": prompt}]
        try:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model, "messages": messages,
                    "temperature": temperatura, "max_tokens": max_tokens,
                },
                timeout=_TIMEOUT_S,
            )
            resp.raise_for_status()
            data  = resp.json()
            texto = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
            return LLMResponse(
                conteudo      = texto,
                model         = self._model,
                input_tokens  = usage.get("prompt_tokens", 0),
                output_tokens = usage.get("completion_tokens", 0),
                sucesso       = bool(texto),
            )
        except Exception as exc:
            logger.exception("❌ %s.gerar_resposta_sincrono | erro: %s", self.provider_name, exc)
            return LLMResponse(conteudo="", model=self._model, sucesso=False, erro=str(exc)[:300])
