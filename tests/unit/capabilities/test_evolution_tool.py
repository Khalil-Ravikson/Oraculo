# tests/unit/capabilities/test_evolution_tool.py
"""
Cobertura de src/capabilities/messaging/evolution_tool.py — ganhou
enviar_midia_por_url() na Fase 4 do plano de integração (Decisão 03), pra
tirar mcp_lab/tools.py::buscar_imagem() do acesso direto a EvolutionAdapter
(ver ADR 0006). Este arquivo nunca tinha tido teste dedicado antes
(enviar_botoes_confirmacao só era coberto indiretamente via
test_conversation_registration.py).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.capabilities.messaging.evolution_tool import enviar_midia_por_url


@pytest.mark.asyncio
async def test_enviar_midia_por_url_repassa_argumentos_pro_adapter():
    gateway = MagicMock()
    gateway.enviar_midia_url = AsyncMock(return_value=True)
    gateway_cls = MagicMock(return_value=gateway)

    with patch(
        "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter", gateway_cls,
    ):
        ok = await enviar_midia_por_url(
            "5598999999999", "https://x.com/img.jpg",
            mediatype="image", mimetype="image/jpeg", caption="legenda",
        )

    assert ok is True
    gateway.enviar_midia_url.assert_awaited_once_with(
        "5598999999999", "https://x.com/img.jpg",
        mediatype="image", mimetype="image/jpeg", caption="legenda",
    )


@pytest.mark.asyncio
async def test_enviar_midia_por_url_propaga_falha_do_adapter():
    gateway = MagicMock()
    gateway.enviar_midia_url = AsyncMock(return_value=False)
    gateway_cls = MagicMock(return_value=gateway)

    with patch(
        "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter", gateway_cls,
    ):
        ok = await enviar_midia_por_url(
            "5598999999999", "https://x.com/img.jpg", mediatype="image", mimetype="image/jpeg",
        )

    assert ok is False
