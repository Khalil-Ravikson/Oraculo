"""Testes de ToolNode — mocka executar_tool(), não invoca tool real."""

import pytest
from unittest.mock import AsyncMock, patch
from src.graph_studio.nodes.tool_node import ToolNode
from src.graph_studio.execution_context import ExecutionContext


class TestToolNode:
    """Testes de ToolNode."""

    def test_node_identity(self):
        node = ToolNode()
        assert node.node_id == "tool_default"
        assert node.node_type == "tool"

    def test_input_ports(self):
        node = ToolNode()
        names = {p.name for p in node.input_ports}
        assert "tool_name" in names
        assert "args" in names

    def test_output_ports(self):
        node = ToolNode()
        names = {p.name for p in node.output_ports}
        assert "result" in names

    @pytest.mark.asyncio
    async def test_execute_missing_tool_name_raises(self):
        node = ToolNode()
        with pytest.raises(ValueError):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_success(self):
        node = ToolNode()
        fake_executar_tool = AsyncMock(return_value={"mensagem": "ok"})

        with patch(
            "src.capabilities.registry.executar_tool",
            fake_executar_tool
        ):
            result = await node.execute(
                {"tool_name": "get_student_info", "args": {"user_id": "123"}},
                ExecutionContext()
            )

        assert result == {"result": {"mensagem": "ok"}}
        fake_executar_tool.assert_awaited_once_with("get_student_info", {"user_id": "123"})

    @pytest.mark.asyncio
    async def test_execute_default_empty_args(self):
        node = ToolNode()
        fake_executar_tool = AsyncMock(return_value={"mensagem": "ok"})

        with patch(
            "src.capabilities.registry.executar_tool",
            fake_executar_tool
        ):
            await node.execute({"tool_name": "some_tool"}, ExecutionContext())

        fake_executar_tool.assert_awaited_once_with("some_tool", {})
