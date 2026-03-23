from __future__ import annotations
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

# Apenas o contrato e as settings importam aqui
from src.domain.ports.llm_provider import ILLMProvider, LLMResponse
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)

def _is_retryable(exc: BaseException) -> bool:
    """Decide se o Tenacity deve fazer retry neste erro."""
    err = str(exc).lower()
    return any(term in err for term in ["429", "quota", "rate limit", "503", "overloaded", "timeout", "connection"])

class GeminiProvider(ILLMProvider):
    """
    Implementação concreta do ILLMProvider usando a API Gemini.
    """
    MODELO_PRIMARIO = "gemini-3.1-flash-lite-preview"
    MODELO_FALLBACK = "gemini-2.5-flash" # Atualizado para um nome de fallback válido

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY não configurada")
        
        self.model_name = model_name or self.MODELO_PRIMARIO
        self.client = genai.Client(api_key=self.api_key)
        logger.info("✅ Cliente Gemini inicializado | modelo=%s", self.model_name)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        stop=stop_after_attempt(4),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _chamada_interna_com_retry(
        self,
        prompt: str,
        system_instruction: str,
        temperatura: float,
        max_tokens: int,
        modelo_alvo: str,
        response_schema: Type[BaseModel] | None = None
    ) -> LLMResponse:
        """Método privado que faz a requisição real à API do Google."""
        
        config_kwargs: dict[str, Any] = {
            "temperature": temperatura,
            "max_output_tokens": max_tokens,
            # Configurações de segurança resumidas para brevidade
        }

        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema

        config = types.GenerateContentConfig(**config_kwargs)

        resposta = self.client.models.generate_content(
            model=modelo_alvo,
            contents=prompt,
            config=config,
        )

        usage = getattr(resposta, "usage_metadata", None)
        input_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
        output_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
        conteudo = resposta.text or ""

        return LLMResponse(
            conteudo=conteudo,
            model=modelo_alvo,
            input_tokens=input_tok,
            output_tokens=output_tok,
            sucesso=True
        )

    # ==========================================
    # IMPLEMENTAÇÃO DOS MÉTODOS DO CONTRATO
    # ==========================================

    def gerar_resposta(
        self,
        prompt: str,
        system_instruction: str = "",
        temperatura: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Implementa a assinatura obrigatória do ILLMProvider."""
        
        try:
            # Tenta o modelo primário
            return self._chamada_interna_com_retry(
                prompt, system_instruction, temperatura, max_tokens, self.model_name
            )
        except Exception as e:
            logger.warning("🔄 Tenacity esgotado para %s → tentando fallback %s", self.model_name, self.MODELO_FALLBACK)
            try:
                # Tenta o fallback
                return self._chamada_interna_com_retry(
                    prompt, system_instruction, temperatura, max_tokens, self.MODELO_FALLBACK
                )
            except Exception as e_fallback:
                logger.error("❌ Fallback também falhou: %s", e_fallback)
                return LLMResponse(conteudo="", model=self.model_name, erro=str(e_fallback)[:300], sucesso=False)


    def gerar_resposta_estruturada(
        self,
        prompt: str,
        response_schema: Type[T],
        system_instruction: str = "",
        temperatura: float = 0.0,
    ) -> T | None:
        """Implementa a assinatura obrigatória para JSON."""
        
        try:
            resposta_llm = self._chamada_interna_com_retry(
                prompt=prompt,
                system_instruction=system_instruction,
                temperatura=temperatura,
                max_tokens=1024,
                modelo_alvo=self.model_name,
                response_schema=response_schema
            )
            
            if not resposta_llm.sucesso or not resposta_llm.conteudo:
                return None
                
            # A API do Gemini já garante o JSON perfeito quando usamos response_schema
            dict_resposta = json.loads(resposta_llm.conteudo)
            
            # Converte o dict de volta para a classe Pydantic pedida pelo usuário
            return response_schema(**dict_resposta)
            
        except Exception as e:
            logger.error("❌ Erro na geração estruturada: %s", e)
            return None