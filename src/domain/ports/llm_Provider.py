from __future__ import annotations
from typing import Protocol, Type, TypeVar, Any
from dataclasses import dataclass
from pydantic import BaseModel

# T é um tipo genérico que representa "Qualquer classe que herde de BaseModel"
T = TypeVar('T', bound=BaseModel)

@dataclass
class LLMResponse:
    """Envelope padrão para qualquer resposta de texto livre de qualquer LLM."""
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
    O 'Oráculo' só conversa com esta interface. Ele não sabe se é Gemini ou Groq.
    """
    
    async def gerar_resposta_async(
        self,
        prompt: str,
        system_instruction: str = "",
        temperatura: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Gera texto livre assíncrono (ex: resposta final para o aluno)."""
        ...

    async def gerar_resposta_estruturada_async(
        self,
        prompt: str,
        response_schema: Type[T],
        system_instruction: str = "",
        temperatura: float = 0.0,
    ) -> T | None:
        """
        Gera um JSON validado e devolve instanciado como um objeto Pydantic (T).
        É aqui que entra o "Global". Qualquer provedor tem que saber devolver isso!
        """
        ...