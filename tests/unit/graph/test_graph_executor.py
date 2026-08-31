"""Testes de src/graph/graph_executor.py — execução de topologia (Hub v2 Sprint 8).

Cobre: dry-run não chama execute(); execução real segue a ordem topológica e
passa a saída de um nó para o próximo; nó desabilitado é pulado; topologia
inválida não executa; falha de um nó interrompe e é reportada.
"""
import pytest

from src.graph.base_node import BaseNode, Port, PortType
from src.graph.execution_context import ExecutionContext
from src.graph.graph_executor import GraphExecutor
from src.graph.node_registry import NodeRegistry


class _Fonte(BaseNode):
    def __init__(self, node_id="fonte", valor="olá"):
        self._id, self._v = node_id, valor
        self.chamado = False

    @property
    def node_id(self): return self._id
    @property
    def node_type(self): return "mock"
    @property
    def input_ports(self): return []
    @property
    def output_ports(self): return [Port("texto", PortType.TEXT, "saída")]

    async def execute(self, inputs, context):
        self.chamado = True
        return {"texto": self._v}


class _Meio(BaseNode):
    def __init__(self, node_id="meio"):
        self._id = node_id
        self.recebeu = None

    @property
    def node_id(self): return self._id
    @property
    def node_type(self): return "mock"
    @property
    def input_ports(self): return [Port("entrada", PortType.TEXT, "entrada")]
    @property
    def output_ports(self): return [Port("texto", PortType.TEXT, "saída")]

    async def execute(self, inputs, context):
        self.recebeu = inputs.get("entrada")
        return {"texto": f"[{inputs.get('entrada')}]"}


class _Quebra(BaseNode):
    @property
    def node_id(self): return "quebra"
    @property
    def node_type(self): return "mock"
    @property
    def input_ports(self): return [Port("entrada", PortType.TEXT, "entrada")]
    @property
    def output_ports(self): return [Port("texto", PortType.TEXT, "saída")]

    async def execute(self, inputs, context):
        raise RuntimeError("falhou de propósito")


def _topo(*node_ids, edges=None):
    return {
        "nodes": [{"node_id": n, "x": 0, "y": 0} for n in node_ids],
        "edges": edges or [],
    }


def _reg(*nodes):
    r = NodeRegistry()
    for n in nodes:
        r.register(n)
    return r


_EDGE = [{"source_node": "fonte", "source_port": "texto", "target_node": "meio", "target_port": "entrada"}]


@pytest.mark.asyncio
async def test_dry_run_nao_executa_os_nos():
    fonte, meio = _Fonte(), _Meio()
    ex = GraphExecutor(registry=_reg(fonte, meio))
    res = await ex.executar(_topo("fonte", "meio", edges=_EDGE), dry_run=True)
    assert res.ok and res.dry_run
    assert res.ordem == ["fonte", "meio"]
    assert fonte.chamado is False
    assert any(e["tipo"] == "simulado" for e in res.eventos)


@pytest.mark.asyncio
async def test_execucao_real_encadeia_saida_para_entrada():
    fonte, meio = _Fonte(valor="mundo"), _Meio()
    ex = GraphExecutor(registry=_reg(fonte, meio))
    res = await ex.executar(_topo("fonte", "meio", edges=_EDGE), dry_run=False)
    assert res.ok
    assert fonte.chamado is True
    assert meio.recebeu == "mundo"
    assert res.saidas["meio"] == {"texto": "[mundo]"}


@pytest.mark.asyncio
async def test_no_desabilitado_e_pulado():
    fonte, meio = _Fonte(), _Meio()
    ex = GraphExecutor(registry=_reg(fonte, meio), desabilitados={"meio"})
    res = await ex.executar(_topo("fonte", "meio", edges=_EDGE), dry_run=False)
    assert res.ok
    assert any(e["tipo"] == "pulado" and e["node"] == "meio" for e in res.eventos)
    assert meio.recebeu is None


@pytest.mark.asyncio
async def test_topologia_invalida_nao_executa():
    ex = GraphExecutor(registry=_reg(_Fonte()))
    res = await ex.executar(_topo("fonte", "inexistente"), dry_run=False)
    assert res.ok is False
    assert any("inexistente" in e for e in res.erros)


@pytest.mark.asyncio
async def test_falha_de_no_interrompe_e_reporta():
    fonte, quebra = _Fonte(), _Quebra()
    edge = [{"source_node": "fonte", "source_port": "texto", "target_node": "quebra", "target_port": "entrada"}]
    ex = GraphExecutor(registry=_reg(fonte, quebra))
    res = await ex.executar(_topo("fonte", "quebra", edges=edge), dry_run=False)
    assert res.ok is False
    assert any("falhou de propósito" in e for e in res.erros)
    assert any(e["tipo"] == "erro" and e["node"] == "quebra" for e in res.eventos)
