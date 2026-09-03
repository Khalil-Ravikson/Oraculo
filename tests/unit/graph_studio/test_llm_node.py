"""Testes de LLMNode — mocka llm_factory.get_llm_provider(), não chama Gemini real."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel
from src.graph_studio.nodes.llm_node import LLMNode
from src.graph_studio.execution_context import ExecutionContext


class _FakeLLMResponse:
    def __init__(self, sucesso=True, conteudo="", erro="", input_tokens=10, output_tokens=20):
        self.sucesso = sucesso
        self.conteudo = conteudo
        self.erro = erro
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeSchema(BaseModel):
    resposta: str


class TestLLMNode:
    """Testes de LLMNode."""

    def test_node_identity(self):
        node = LLMNode()
        assert node.node_id == "llm_default"
        assert node.node_type == "llm_provider"

    def test_input_ports(self):
        node = LLMNode()
        names = {p.name for p in node.input_ports}
        assert {"prompt", "system_instruction", "temperatura", "response_schema", "agente", "rota"} <= names

    def test_output_ports(self):
        node = LLMNode()
        names = {p.name for p in node.output_ports}
        assert {"response", "structured", "tokens_used"} <= names

    @pytest.mark.asyncio
    async def test_execute_missing_prompt_raises(self):
        node = LLMNode()
        with pytest.raises(ValueError):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_free_text_mode(self):
        node = LLMNode()
        fake_provider = MagicMock()
        fake_provider.gerar_resposta_async = AsyncMock(
            return_value=_FakeLLMResponse(sucesso=True, conteudo="ola!", input_tokens=5, output_tokens=8)
        )

        with patch(
            "src.infrastructure.adapters.llm_factory.get_llm_provider",
            return_value=fake_provider
        ):
            result = await node.execute({"prompt": "oi"}, ExecutionContext())

        assert result["response"] == "ola!"
        assert result["tokens_used"] == (5, 8)
        fake_provider.gerar_resposta_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_free_text_failure_raises(self):
        node = LLMNode()
        fake_provider = MagicMock()
        fake_provider.gerar_resposta_async = AsyncMock(
            return_value=_FakeLLMResponse(sucesso=False, erro="rate limited")
        )

        with patch(
            "src.infrastructure.adapters.llm_factory.get_llm_provider",
            return_value=fake_provider
        ):
            with pytest.raises(RuntimeError, match="rate limited"):
                await node.execute({"prompt": "oi"}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_structured_mode(self):
        node = LLMNode()
        fake_provider = MagicMock()
        fake_instance = _FakeSchema(resposta="ok")
        fake_provider.gerar_resposta_estruturada_async = AsyncMock(return_value=fake_instance)
        fake_provider.ultimo_uso_tokens = (3, 4)

        with patch(
            "src.infrastructure.adapters.llm_factory.get_llm_provider",
            return_value=fake_provider
        ):
            result = await node.execute(
                {"prompt": "oi", "response_schema": _FakeSchema},
                ExecutionContext()
            )

        assert result["structured"] == fake_instance
        assert result["tokens_used"] == (3, 4)
        fake_provider.gerar_resposta_estruturada_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_passes_agente_and_rota(self):
        node = LLMNode()
        fake_provider = MagicMock()
        fake_provider.gerar_resposta_async = AsyncMock(
            return_value=_FakeLLMResponse(sucesso=True, conteudo="x")
        )

        with patch(
            "src.infrastructure.adapters.llm_factory.get_llm_provider",
            return_value=fake_provider
        ) as mock_get:
            await node.execute(
                {"prompt": "oi", "agente": "rag", "rota": "GERAL"},
                ExecutionContext()
            )

        mock_get.assert_called_once_with("rag", "GERAL")
