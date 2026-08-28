"""Testes de MCPLabNode e RestLabNode — mocka tentar_rotear(), não chama gateway real."""

import pytest
from unittest.mock import AsyncMock, patch
from src.graph.nodes.mcp_lab_node import MCPLabNode
from src.graph.nodes.rest_lab_node import RestLabNode
from src.graph.execution_context import ExecutionContext


class TestMCPLabNode:
    """Testes de MCPLabNode."""

    def test_node_identity(self):
        node = MCPLabNode()
        assert node.node_id == "lab_mcp"
        assert node.node_type == "lab_router"

    def test_input_ports(self):
        node = MCPLabNode()
        names = {p.name for p in node.input_ports}
        assert {"mensagem", "chat_id"} <= names

    def test_output_ports(self):
        node = MCPLabNode()
        names = {p.name for p in node.output_ports}
        assert {"resultado", "intercepted"} <= names

    @pytest.mark.asyncio
    async def test_execute_missing_mensagem_raises(self):
        node = MCPLabNode()
        with pytest.raises(ValueError):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_intercepted(self):
        node = MCPLabNode()
        fake_tentar_rotear = AsyncMock(return_value={"mensagem": "achei 3 repos"})

        with patch("mcp_lab.router.tentar_rotear", fake_tentar_rotear):
            result = await node.execute(
                {"mensagem": "stack python asyncio", "chat_id": "123"},
                ExecutionContext()
            )

        assert result == {"resultado": {"mensagem": "achei 3 repos"}, "intercepted": True}
        fake_tentar_rotear.assert_awaited_once_with("stack python asyncio", "123")

    @pytest.mark.asyncio
    async def test_execute_not_intercepted(self):
        node = MCPLabNode()
        fake_tentar_rotear = AsyncMock(return_value=None)

        with patch("mcp_lab.router.tentar_rotear", fake_tentar_rotear):
            result = await node.execute(
                {"mensagem": "oi tudo bem?"},
                ExecutionContext()
            )

        assert result == {"resultado": None, "intercepted": False}

    @pytest.mark.asyncio
    async def test_execute_default_chat_id(self):
        node = MCPLabNode()
        fake_tentar_rotear = AsyncMock(return_value=None)

        with patch("mcp_lab.router.tentar_rotear", fake_tentar_rotear):
            await node.execute({"mensagem": "stack x"}, ExecutionContext())

        fake_tentar_rotear.assert_awaited_once_with("stack x", "")


class TestRestLabNode:
    """Testes de RestLabNode."""

    def test_node_identity(self):
        node = RestLabNode()
        assert node.node_id == "lab_rest"
        assert node.node_type == "lab_router"

    def test_input_ports(self):
        node = RestLabNode()
        names = {p.name for p in node.input_ports}
        assert "mensagem" in names

    def test_output_ports(self):
        node = RestLabNode()
        names = {p.name for p in node.output_ports}
        assert {"resultado", "intercepted"} <= names

    @pytest.mark.asyncio
    async def test_execute_missing_mensagem_raises(self):
        node = RestLabNode()
        with pytest.raises(ValueError):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_intercepted(self):
        node = RestLabNode()
        fake_tentar_rotear = AsyncMock(return_value={"mensagem": "usuario 1: Joao"})

        with patch("rest_lab.router.tentar_rotear", fake_tentar_rotear):
            result = await node.execute(
                {"mensagem": "rest usuario 1"},
                ExecutionContext()
            )

        assert result == {"resultado": {"mensagem": "usuario 1: Joao"}, "intercepted": True}
        fake_tentar_rotear.assert_awaited_once_with("rest usuario 1")

    @pytest.mark.asyncio
    async def test_execute_not_intercepted(self):
        node = RestLabNode()
        fake_tentar_rotear = AsyncMock(return_value=None)

        with patch("rest_lab.router.tentar_rotear", fake_tentar_rotear):
            result = await node.execute(
                {"mensagem": "oi tudo bem?"},
                ExecutionContext()
            )

        assert result == {"resultado": None, "intercepted": False}
