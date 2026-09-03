"""Testes de ChannelNode — mocka EvolutionAdapter, não chama API real."""

import pytest
from unittest.mock import AsyncMock, patch
from src.graph_studio.nodes.channel_node import ChannelNode
from src.graph_studio.execution_context import ExecutionContext


class TestChannelNode:
    """Testes de ChannelNode."""

    def test_node_identity(self):
        node = ChannelNode()
        assert node.node_id == "channel_whatsapp"
        assert node.node_type == "channel"

    def test_input_ports(self):
        node = ChannelNode()
        names = {p.name for p in node.input_ports}
        assert {"number", "action", "payload"} <= names

    def test_output_ports(self):
        node = ChannelNode()
        names = {p.name for p in node.output_ports}
        assert "ok" in names

    @pytest.mark.asyncio
    async def test_execute_missing_number_raises(self):
        node = ChannelNode()
        with pytest.raises(ValueError, match="number"):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_unknown_action_raises(self):
        node = ChannelNode()
        with pytest.raises(ValueError, match="Unknown action"):
            await node.execute({"number": "5599999999", "action": "explode"}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_text_action_default(self):
        node = ChannelNode()
        fake_adapter = AsyncMock()
        fake_adapter.enviar_mensagem.return_value = True

        with patch(
            "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter",
            return_value=fake_adapter
        ):
            result = await node.execute(
                {"number": "5599999999", "payload": {"text": "oi"}},
                ExecutionContext()
            )

        assert result == {"ok": True}
        fake_adapter.enviar_mensagem.assert_awaited_once_with("5599999999", "oi")

    @pytest.mark.asyncio
    async def test_execute_text_action_missing_text_raises(self):
        node = ChannelNode()
        fake_adapter = AsyncMock()

        with patch(
            "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter",
            return_value=fake_adapter
        ):
            with pytest.raises(ValueError, match="payload.text"):
                await node.execute({"number": "5599999999"}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_typing_action(self):
        node = ChannelNode()
        fake_adapter = AsyncMock()
        fake_adapter.enviar_digitando.return_value = True

        with patch(
            "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter",
            return_value=fake_adapter
        ):
            result = await node.execute(
                {"number": "5599999999", "action": "typing", "payload": {"duration_ms": 1500}},
                ExecutionContext()
            )

        assert result == {"ok": True}
        fake_adapter.enviar_digitando.assert_awaited_once_with("5599999999", 1500)

    @pytest.mark.asyncio
    async def test_execute_typing_action_default_duration(self):
        node = ChannelNode()
        fake_adapter = AsyncMock()
        fake_adapter.enviar_digitando.return_value = True

        with patch(
            "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter",
            return_value=fake_adapter
        ):
            await node.execute(
                {"number": "5599999999", "action": "typing"},
                ExecutionContext()
            )

        fake_adapter.enviar_digitando.assert_awaited_once_with("5599999999", 2000)

    @pytest.mark.asyncio
    async def test_execute_media_url_action(self):
        node = ChannelNode()
        fake_adapter = AsyncMock()
        fake_adapter.enviar_midia_url.return_value = True

        with patch(
            "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter",
            return_value=fake_adapter
        ):
            result = await node.execute(
                {
                    "number": "5599999999",
                    "action": "media_url",
                    "payload": {
                        "url": "https://x.com/a.png",
                        "mediatype": "image",
                        "mimetype": "image/png",
                        "caption": "legenda",
                    },
                },
                ExecutionContext()
            )

        assert result == {"ok": True}
        fake_adapter.enviar_midia_url.assert_awaited_once_with(
            "5599999999", "https://x.com/a.png", "image", "image/png",
            caption="legenda", filename="",
        )

    @pytest.mark.asyncio
    async def test_execute_media_url_missing_fields_raises(self):
        node = ChannelNode()
        fake_adapter = AsyncMock()

        with patch(
            "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter",
            return_value=fake_adapter
        ):
            with pytest.raises(ValueError, match="url,mediatype,mimetype"):
                await node.execute(
                    {"number": "5599999999", "action": "media_url", "payload": {}},
                    ExecutionContext()
                )
