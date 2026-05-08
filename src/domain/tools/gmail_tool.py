"""
Gmail Tools — GmailSearchTool + GmailTriggerTool
Apenas para role=ADMIN. Usa Google API via service account ou OAuth.
"""
from __future__ import annotations
import logging
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)


class GmailSearchInput(BaseModel):
    query: str = Field(..., description="Query Gmail. Ex: 'edital PAES subject:inscrição'")
    max_results: int = Field(default=3, description="Máximo de e-mails a retornar")


class GmailTriggerInput(BaseModel):
    destinatario: str = Field(..., description="E-mail do destinatário")
    assunto: str = Field(..., description="Assunto do e-mail")
    corpo: str = Field(..., description="Corpo do e-mail em texto simples")


def build_gmail_search_tool(gmail_svc) -> StructuredTool:
    async def _run(query: str, max_results: int = 3) -> str:
        return await gmail_svc.search(query, max_results)

    return StructuredTool(
        name="gmail_search",
        description=(
            "Busca e-mails no Gmail institucional da UEMA. "
            "Use para encontrar comunicados, editais ou tickets específicos. "
            "Ex: 'edital PAES 2026', 'chamado CTIC suporte'"
        ),
        args_schema=GmailSearchInput,
        coroutine=_run,
    )


def build_gmail_trigger_tool(gmail_svc) -> StructuredTool:
    async def _run(destinatario: str, assunto: str, corpo: str) -> str:
        return await gmail_svc.send(destinatario, assunto, corpo)

    return StructuredTool(
        name="gmail_trigger",
        description=(
            "Envia e-mail via Gmail institucional. "
            "Use APENAS após confirmação explícita do admin. "
            "Para testes: destinatario='admin@uema.br'"
        ),
        args_schema=GmailTriggerInput,
        coroutine=_run,
    )