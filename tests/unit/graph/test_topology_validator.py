"""Testes de src/graph/topology_validator.py — validação de topologia
(nós existem, tipos de porta batem, grafo é acíclico)."""

from src.graph.base_node import BaseNode, Port, PortType
from src.graph.node_registry import NodeRegistry
from src.graph.topology_validator import validar_topologia


class _SourceNode(BaseNode):
    def __init__(self, node_id="source"):
        self._id = node_id

    @property
    def node_id(self):
        return self._id

    @property
    def node_type(self):
        return "mock"

    @property
    def input_ports(self):
        return []

    @property
    def output_ports(self):
        return [Port("out", PortType.TEXT, "saida")]

    async def execute(self, inputs, context):
        return {"out": "x"}


class _TargetNode(BaseNode):
    def __init__(self, node_id="target"):
        self._id = node_id

    @property
    def node_id(self):
        return self._id

    @property
    def node_type(self):
        return "mock"

    @property
    def input_ports(self):
        return [Port("in", PortType.TEXT, "entrada")]

    @property
    def output_ports(self):
        return [Port("out", PortType.TEXT, "saida")]

    async def execute(self, inputs, context):
        return {"out": "x"}


class _EmbeddingsMockNode(BaseNode):
    """Porta de tipo diferente, pra testar mismatch."""

    @property
    def node_id(self):
        return "emb"

    @property
    def node_type(self):
        return "mock"

    @property
    def input_ports(self):
        return [Port("in", PortType.EMBEDDINGS, "entrada")]

    @property
    def output_ports(self):
        return []

    async def execute(self, inputs, context):
        return {}


def _registry_basico() -> NodeRegistry:
    reg = NodeRegistry()
    reg.register(_SourceNode("a"))
    reg.register(_TargetNode("b"))
    reg.register(_TargetNode("c"))
    reg.register(_EmbeddingsMockNode())
    return reg


class TestValidarTopologia:
    """Testes de validar_topologia."""

    def test_topologia_vazia_e_invalida(self):
        erros = validar_topologia({"nodes": [], "edges": []}, _registry_basico())
        assert any("sem nenhum nó" in e for e in erros)

    def test_topologia_um_no_sem_edges_e_valida(self):
        topo = {"nodes": [{"node_id": "a", "x": 0, "y": 0}], "edges": []}
        erros = validar_topologia(topo, _registry_basico())
        assert erros == []

    def test_no_inexistente_no_registry_e_erro(self):
        topo = {"nodes": [{"node_id": "fantasma"}], "edges": []}
        erros = validar_topologia(topo, _registry_basico())
        assert any("fantasma" in e and "não existe" in e for e in erros)

    def test_edge_valida_entre_dois_nos_do_canvas(self):
        topo = {
            "nodes": [{"node_id": "a"}, {"node_id": "b"}],
            "edges": [{"source_node": "a", "source_port": "out",
                       "target_node": "b", "target_port": "in"}],
        }
        erros = validar_topologia(topo, _registry_basico())
        assert erros == []

    def test_edge_com_tipo_incompativel_e_erro(self):
        topo = {
            "nodes": [{"node_id": "a"}, {"node_id": "emb"}],
            "edges": [{"source_node": "a", "source_port": "out",
                       "target_node": "emb", "target_port": "in"}],
        }
        erros = validar_topologia(topo, _registry_basico())
        assert any("Type mismatch" in e for e in erros)

    def test_edge_referencia_no_fora_do_canvas_e_erro(self):
        topo = {
            "nodes": [{"node_id": "a"}],  # "b" não está no canvas
            "edges": [{"source_node": "a", "source_port": "out",
                       "target_node": "b", "target_port": "in"}],
        }
        erros = validar_topologia(topo, _registry_basico())
        assert any("não está no canvas" in e for e in erros)

    def test_edge_incompleta_e_erro(self):
        topo = {
            "nodes": [{"node_id": "a"}, {"node_id": "b"}],
            "edges": [{"source_node": "a", "source_port": "out"}],  # falta target
        }
        erros = validar_topologia(topo, _registry_basico())
        assert any("incompleta" in e for e in erros)

    def test_grafo_aciclico_e_valido(self):
        # a -> b -> c (linear, sem ciclo)
        topo = {
            "nodes": [{"node_id": "a"}, {"node_id": "b"}, {"node_id": "c"}],
            "edges": [
                {"source_node": "a", "source_port": "out", "target_node": "b", "target_port": "in"},
                {"source_node": "b", "source_port": "out", "target_node": "c", "target_port": "in"},
            ],
        }
        erros = validar_topologia(topo, _registry_basico())
        assert erros == []

    def test_ciclo_direto_e_detectado(self):
        # b -> c -> b (ciclo)
        reg = _registry_basico()
        topo = {
            "nodes": [{"node_id": "b"}, {"node_id": "c"}],
            "edges": [
                {"source_node": "b", "source_port": "out", "target_node": "c", "target_port": "in"},
                {"source_node": "c", "source_port": "out", "target_node": "b", "target_port": "in"},
            ],
        }
        erros = validar_topologia(topo, reg)
        assert any("ciclo" in e.lower() for e in erros)

    def test_auto_loop_e_detectado_como_ciclo(self):
        # b -> b (aresta para si mesmo)
        topo = {
            "nodes": [{"node_id": "b"}],
            "edges": [{"source_node": "b", "source_port": "out", "target_node": "b", "target_port": "in"}],
        }
        erros = validar_topologia(topo, _registry_basico())
        assert any("ciclo" in e.lower() for e in erros)

    def test_multiplos_erros_acumulados_nao_para_no_primeiro(self):
        topo = {
            "nodes": [{"node_id": "fantasma1"}, {"node_id": "fantasma2"}],
            "edges": [],
        }
        erros = validar_topologia(topo, _registry_basico())
        assert len(erros) == 2  # os dois nós fantasma reportados, não só o primeiro
