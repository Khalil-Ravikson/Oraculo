"""Testes de EmbeddingsNode — mocka get_embeddings(), não chama provider real."""

import pytest
from unittest.mock import MagicMock, patch
from src.graph_studio.nodes.embeddings_node import EmbeddingsNode
from src.graph_studio.execution_context import ExecutionContext


class TestEmbeddingsNode:
    """Testes de EmbeddingsNode."""

    def test_node_identity(self):
        node = EmbeddingsNode()
        assert node.node_id == "embeddings_default"
        assert node.node_type == "embeddings_provider"

    def test_input_ports(self):
        node = EmbeddingsNode()
        names = {p.name for p in node.input_ports}
        assert "texts" in names
        assert "query" in names
        # Ambas opcionais — validação de "pelo menos uma" é em runtime, não na porta
        for p in node.input_ports:
            assert p.required is False

    def test_output_ports(self):
        node = EmbeddingsNode()
        names = {p.name for p in node.output_ports}
        assert "embeddings" in names
        assert "embedding" in names

    @pytest.mark.asyncio
    async def test_execute_missing_both_raises(self):
        node = EmbeddingsNode()
        with pytest.raises(ValueError):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_texts_mode(self):
        node = EmbeddingsNode()
        fake_model = MagicMock()
        fake_model.embed_documents.return_value = [[0.1, 0.2], [0.3, 0.4]]

        with patch(
            "src.rag.embeddings.get_embeddings",
            return_value=fake_model
        ):
            result = await node.execute(
                {"texts": ["doc1", "doc2"]},
                ExecutionContext()
            )

        assert result == {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}
        fake_model.embed_documents.assert_called_once_with(["doc1", "doc2"])

    @pytest.mark.asyncio
    async def test_execute_query_mode(self):
        node = EmbeddingsNode()
        fake_model = MagicMock()
        fake_model.embed_query.return_value = [0.5, 0.6, 0.7]

        with patch(
            "src.rag.embeddings.get_embeddings",
            return_value=fake_model
        ):
            result = await node.execute(
                {"query": "pergunta do usuario"},
                ExecutionContext()
            )

        assert result == {"embedding": [0.5, 0.6, 0.7]}
        fake_model.embed_query.assert_called_once_with("pergunta do usuario")

    @pytest.mark.asyncio
    async def test_execute_query_takes_precedence_over_texts(self):
        """Se ambos vierem, query é modo mais específico e vence."""
        node = EmbeddingsNode()
        fake_model = MagicMock()
        fake_model.embed_query.return_value = [0.1]

        with patch(
            "src.rag.embeddings.get_embeddings",
            return_value=fake_model
        ):
            result = await node.execute(
                {"query": "q", "texts": ["ignored"]},
                ExecutionContext()
            )

        assert "embedding" in result
        assert "embeddings" not in result
        fake_model.embed_documents.assert_not_called()
