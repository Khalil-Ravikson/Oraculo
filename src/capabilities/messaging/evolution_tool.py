"""
src/capabilities/messaging/evolution_tool.py
===============================================
Capability fina que embrulha `infrastructure/adapters/evolution_adapter.py`
(o adapter técnico de baixo nível permanece como está) para uso por agentes
— ex.: `agents/conversation/registration.py` (Fase 6 do
PLANO_REFATORACAO_SUPERVISOR.md, seção 2.6).
"""
from __future__ import annotations


async def enviar_botoes_confirmacao(
    number: str,
    title: str,
    description: str,
    buttons: list[dict],
) -> None:
    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter

    gateway = EvolutionAdapter()
    await gateway.enviar_botoes(
        number=number,
        title=title,
        description=description,
        buttons=buttons,
    )


async def enviar_midia_por_url(
    number: str,
    url: str,
    mediatype: str,
    mimetype: str,
    caption: str = "",
) -> bool:
    """Fase 4 do plano de integração LangGraph/REST/MCP (Decisão 03): capability
    adicionada pra tirar mcp_lab/tools.py::buscar_imagem() do acesso direto a
    `EvolutionAdapter` (achado da auditoria de 2026-08-24) — mesmo padrão de
    `enviar_botoes_confirmacao` acima, o único outro consumidor deste
    módulo até então."""
    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter

    gateway = EvolutionAdapter()
    return await gateway.enviar_midia_url(
        number, url, mediatype=mediatype, mimetype=mimetype, caption=caption,
    )
