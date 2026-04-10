# src/domain/entities/identidade.py
from typing import Literal, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
import re

class Mensagem(BaseModel):
    """Representação imutável de uma mensagem WhatsApp recebida."""
    user_id:    str
    chat_id:    str
    body:       str
    has_media:  bool = False
    msg_type:   str  = "conversation"
    push_name:  str  = ""
    msg_key_id: str  = ""
    
    # Tornando a mensagem imutável (similar ao frozen=True do dataclass)
    model_config = {"frozen": True}

class IdentidadeRica(BaseModel):
    """O DTO Ouro: Viaja pelo Celery e LangGraph sem tocar no banco."""
    user_id:      str
    chat_id:      str
    body:         str = ""
    nome:         str
    role:         Literal["guest", "student", "admin", "professor", "servidor", "publico"]
    status:       Literal["ativo", "inativo", "pendente", "banido", "trancado"]
    is_admin:     bool = False
    curso:        Optional[str] = None
    periodo:      Optional[str] = None
    matricula:    Optional[str] = None
    centro:       Optional[str] = None
    has_media:    bool = False
    msg_type:     str  = "conversation"
    msg_key_id:   str  = ""

    # Trazendo de volta a sua validação ninja!
    @field_validator("matricula")
    @classmethod
    def validar_matricula(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v # Visitantes e público geral não têm matrícula
            
        # Padrão: Começa com 20 (anos 2000+), mais 2 dígitos pro ano, e 7 dígitos
        if not re.match(r"^20\d{2}\d{7}$", v):
            raise ValueError("A matrícula deve conter 11 dígitos e iniciar com o ano de ingresso.")
        return v

    @property
    def pode_usar_tools(self) -> bool:
        return self.status == "ativo" and self.role not in ["guest", "publico"]

    @property
    def contexto_llm(self) -> dict:
        return {k: v for k, v in {
            "nome":      self.nome,
            "curso":     self.curso,
            "periodo":   self.periodo,
            "matricula": self.matricula,
            "centro":    self.centro,
            "role":      self.role,
        }.items() if v is not None}

    def to_state_dict(self) -> dict:
        """Serializa nativamente para o OracleState e Celery."""
        return self.model_dump()