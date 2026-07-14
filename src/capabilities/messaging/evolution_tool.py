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
