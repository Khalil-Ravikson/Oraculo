from __future__ import annotations
from typing import Protocol, Type, TypeVar, Any
from dataclasses import dataclass
from pydantic import BaseModel

# Criamos um tipo genérico para o schema estruturado
T = TypeVar('T', bound=BaseModel)

@dataclass
class LLMResponse:
    """Objeto de resposta padronizado, agnóstico de provedor."""
    conteudo:      str
    model:         str
    input_tokens:  int  = 0
    output_tokens: int  = 0
    sucesso:       bool = True
    erro:          str  = ""

    @property
    def tokens_total(self) -> int:
        return self.input_tokens + self.output_tokens

class ILLMProvider(Protocol):
    """
    Contrato que qualquer LLM (Gemini, Groq, OpenAI) deve respeitar.
    O AgentCore conversará apenas com esta interface.
    """
    
    def gerar_resposta(
        self,
        prompt: str,
        system_instruction: str = "",
        temperatura: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Gera texto livre (ex: resposta final para o aluno)."""
        ...

    def gerar_resposta_estruturada(
        self,
        prompt: str,
        response_schema: Type[T],
        system_instruction: str = "",
        temperatura: float = 0.0,
    ) -> T | None:
        """
        Gera JSON estruturado baseado num Pydantic Model.
        Útil para extração de fatos e transformação de queries.
        """
        ...