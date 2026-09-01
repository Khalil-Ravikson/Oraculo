"""Testes de TriggerNode — nó-fonte que injeta a mensagem de teste."""

import pytest

from src.graph.execution_context import ExecutionContext
from src.graph.nodes.trigger_node import TriggerNode


def test_node_identity():
    node = TriggerNode()
    assert node.node_id == "trigger_mensagem"
    assert node.node_type == "trigger"


def test_e_uma_fonte_sem_portas_de_entrada():
    node = TriggerNode()
    assert node.input_ports == []
    assert {p.name for p in node.output_ports} == {"text", "rota"}


@pytest.mark.asyncio
async def test_execute_devolve_a_mensagem_de_teste():
    node = TriggerNode()
    out = await node.execute({"mensagem_teste": "teste de GUI"}, ExecutionContext())
    assert out == {"text": "teste de GUI", "rota": "SANDBOX"}


@pytest.mark.asyncio
async def test_execute_sem_mensagem_nao_quebra():
    node = TriggerNode()
    out = await node.execute({}, ExecutionContext())
    assert out["text"] == ""
    assert out["rota"] == "SANDBOX"


def test_registrado_no_registry_global():
    from src.graph.node_registry import get_registry, reset_registry

    reset_registry()
    try:
        assert get_registry().get("trigger_mensagem") is not None
    finally:
        reset_registry()
