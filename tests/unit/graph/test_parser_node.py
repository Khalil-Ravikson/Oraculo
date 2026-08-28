"""Testes de ParserNode — mocka ParserFactory, não parseia arquivo real."""

import pytest
from unittest.mock import MagicMock, patch
from src.graph.nodes.parser_node import ParserNode
from src.graph.execution_context import ExecutionContext


class TestParserNode:
    """Testes de ParserNode."""

    def test_node_identity(self):
        node = ParserNode()
        assert node.node_id == "parser_default"
        assert node.node_type == "parser"

    def test_input_ports(self):
        node = ParserNode()
        names = {p.name for p in node.input_ports}
        assert "file_path" in names
        assert "instruction" in names

    def test_output_ports(self):
        node = ParserNode()
        names = {p.name for p in node.output_ports}
        assert "text" in names

    @pytest.mark.asyncio
    async def test_execute_missing_file_path_raises(self):
        node = ParserNode()
        with pytest.raises(ValueError):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_success(self):
        node = ParserNode()
        fake_parser = MagicMock()
        fake_parser.parse.return_value = "texto extraido do pdf"

        with patch(
            "src.rag.ingestion.parser_factory.ParserFactory.auto",
            return_value=fake_parser
        ):
            result = await node.execute(
                {"file_path": "/tmp/doc.pdf"},
                ExecutionContext()
            )

        assert result == {"text": "texto extraido do pdf"}
        fake_parser.parse.assert_called_once_with("/tmp/doc.pdf", "")

    @pytest.mark.asyncio
    async def test_execute_with_instruction(self):
        node = ParserNode()
        fake_parser = MagicMock()
        fake_parser.parse.return_value = "resumo"

        with patch(
            "src.rag.ingestion.parser_factory.ParserFactory.auto",
            return_value=fake_parser
        ):
            await node.execute(
                {"file_path": "/tmp/doc.pdf", "instruction": "resuma"},
                ExecutionContext()
            )

        fake_parser.parse.assert_called_once_with("/tmp/doc.pdf", "resuma")
