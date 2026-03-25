import re
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

class Student(BaseModel):
    id: Optional[str] = None
    phone: str = Field(..., description="Número de WhatsApp do aluno (ID natural)")
    nome: str
    matricula: str
    is_guest: bool = False
    status: str = Field(default="Ativo", description="Ativo, Trancado, Formado, etc.")
    
    # Dicionário flexível para injetar contexto para o LLM sem quebrar o schema
    llm_context: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("matricula")
    @classmethod
    def validar_matricula(cls, v: str) -> str:
        # Padrão: Começa com 20 (anos 2000+), mais 2 dígitos pro ano, e 7 dígitos sequenciais (11 total)
        if not re.match(r"^20\d{2}\d{7}$", v):
            raise ValueError("A matrícula deve conter 11 dígitos e iniciar com o ano de ingresso (ex: 20200036520)")
        return v

    def can_access_tools(self) -> bool:
        """Apenas cadastrados podem acionar ferramentas que modificam estado."""
        return not self.is_guest