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

from src.domain.ports.llm_Provider import ILLMProvider, LLMResponse
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)

def _is_retryable(exc: BaseException) -> bool:
    err = str(exc).lower()
    return any(term in err for term in ["429", "quota", "rate limit", "503", "overloaded", "timeout"])

class GeminiProvider(ILLMProvider):
    MODELO_PRIMARIO = "gemini-3.1-flash-lite-preview"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY não configurada")
        
        self.client = genai.Client(api_key=self.api_key)
        logger.info("✅ GeminiProvider Assíncrono inicializado.")

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _chamada_interna_async(
        self, prompt: str, system_instruction: str, temperatura: float, 
        max_tokens: int, response_schema: Type[BaseModel] | None = None
    ) -> LLMResponse:
        
        config_kwargs: dict[str, Any] = {
            "temperature": temperatura,
            "max_output_tokens": max_tokens,
        }

        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema

        config = types.GenerateContentConfig(**config_kwargs)

        # Usando aio (async) da nova SDK do Google
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
            sucesso=True
        )

    async def gerar_resposta_async(
        self, prompt: str, system_instruction: str = "", temperatura: float = 0.2, max_tokens: int = 1024,
    ) -> LLMResponse:
        try:
            return await self._chamada_interna_async(prompt, system_instruction, temperatura, max_tokens)
        except Exception as e:
            logger.error("❌ Falha no Gemini: %s", e)
            return LLMResponse(conteudo="", model=self.MODELO_PRIMARIO, erro=str(e), sucesso=False)

    async def gerar_resposta_estruturada_async(
        self, prompt: str, response_schema: Type[T], system_instruction: str = "", temperatura: float = 0.0,
    ) -> T | None:
        try:
            resposta_llm = await self._chamada_interna_async(
                prompt, system_instruction, temperatura, max_tokens=1024, response_schema=response_schema
            )
            
            if not resposta_llm.sucesso or not resposta_llm.conteudo:
                return None
                
            # O Gemini nos garante um JSON aqui. O Pydantic (response_schema) faz a validação final!
            dict_dados = json.loads(resposta_llm.conteudo)
            return response_schema(**dict_dados)
        except Exception as e:
            logger.error("❌ Erro estruturado: %s", e)
            return None