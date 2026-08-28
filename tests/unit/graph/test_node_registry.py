"""Testes de NodeRegistry."""

import pytest
from src.graph.base_node import BaseNode, Port, PortType
from src.graph.execution_context import ExecutionContext
from src.graph.node_registry import NodeRegistry, get_registry, reset_registry


class MockNode(BaseNode):
    """Nó mock para testes."""

    def __init__(self, node_id: str = "mock_1", node_type: str = "mock"):
        self._node_id = node_id
        self._node_type = node_type

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def node_type(self) -> str:
        return self._node_type

    @property
    def input_ports(self):
        return [Port("input", PortType.TEXT, "Input")]

    @property
    def output_ports(self):
        return [Port("output", PortType.TEXT, "Output")]

    async def execute(self, inputs, context):
        return {"output": inputs.get("input", "").upper()}


class TestNodeRegistry:
    """Testes de NodeRegistry."""

    def test_registry_creation(self):
        """Registry pode ser criada vazia."""
        reg = NodeRegistry()
        assert reg.count() == 0

    def test_register_node(self):
        """Node pode ser registrada."""
        reg = NodeRegistry()
        node = MockNode("test_1", "test")
        reg.register(node)

        assert reg.count() == 1
        assert reg.get("test_1") == node

    def test_register_multiple_nodes(self):
        """Múltiplos nodes podem ser registrados."""
        reg = NodeRegistry()
        node1 = MockNode("test_1", "test")
        node2 = MockNode("test_2", "test")

        reg.register(node1)
        reg.register(node2)

        assert reg.count() == 2
        assert reg.get("test_1") == node1
        assert reg.get("test_2") == node2

    def test_register_duplicate_raises(self):
        """Registrar node com ID duplicado lança erro."""
        reg = NodeRegistry()
        node1 = MockNode("test_1", "test")
        node2 = MockNode("test_1", "test")  # Mesmo ID

        reg.register(node1)
        with pytest.raises(ValueError):
            reg.register(node2)

    def test_get_nonexistent_returns_none(self):
        """Buscar node inexistente retorna None."""
        reg = NodeRegistry()
        assert reg.get("nonexistent") is None

    def test_list_nodes(self):
        """list_nodes retorna metadados de todos os nodes."""
        reg = NodeRegistry()
        node1 = MockNode("node_1", "type_a")
        node2 = MockNode("node_2", "type_b")

        reg.register(node1)
        reg.register(node2)

        nodes_list = reg.list_nodes()
        assert len(nodes_list) == 2

        # Verificar que está ordenado por ID
        assert nodes_list[0]["id"] == "node_1"
        assert nodes_list[1]["id"] == "node_2"

        # Verificar estrutura de metadados
        node_dict = nodes_list[0]
        assert "id" in node_dict
        assert "type" in node_dict
        assert "metadata" in node_dict
        assert "input_ports" in node_dict
        assert "output_ports" in node_dict
        assert "config_schema" in node_dict

    def test_validate_connection_valid(self):
        """Validar conexão válida (tipos casam)."""
        reg = NodeRegistry()

        class SourceNode(BaseNode):
            @property
            def node_id(self):
                return "source"

            @property
            def node_type(self):
                return "source"

            @property
            def input_ports(self):
                return []

            @property
            def output_ports(self):
                return [Port("out", PortType.TEXT, "Output")]

            async def execute(self, inputs, context):
                return {"out": "value"}

        class TargetNode(BaseNode):
            @property
            def node_id(self):
                return "target"

            @property
            def node_type(self):
                return "target"

            @property
            def input_ports(self):
                return [Port("in", PortType.TEXT, "Input")]

            @property
            def output_ports(self):
                return []

            async def execute(self, inputs, context):
                return {}

        reg.register(SourceNode())
        reg.register(TargetNode())

        is_valid, error = reg.validate_connection("source", "out", "target", "in")
        assert is_valid is True
        assert error is None

    def test_validate_connection_type_mismatch(self):
        """Validar conexão com tipos incompatíveis."""
        reg = NodeRegistry()

        class SourceNode(BaseNode):
            @property
            def node_id(self):
                return "source"

            @property
            def node_type(self):
                return "source"

            @property
            def input_ports(self):
                return []

            @property
            def output_ports(self):
                return [Port("out", PortType.TEXT, "Output")]

            async def execute(self, inputs, context):
                return {"out": "value"}

        class TargetNode(BaseNode):
            @property
            def node_id(self):
                return "target"

            @property
            def node_type(self):
                return "target"

            @property
            def input_ports(self):
                return [Port("in", PortType.EMBEDDINGS, "Input")]

            @property
            def output_ports(self):
                return []

            async def execute(self, inputs, context):
                return {}

        reg.register(SourceNode())
        reg.register(TargetNode())

        is_valid, error = reg.validate_connection("source", "out", "target", "in")
        assert is_valid is False
        assert "Type mismatch" in error

    def test_validate_connection_node_not_found(self):
        """Validar conexão com node inexistente."""
        reg = NodeRegistry()
        node = MockNode("test_1", "test")
        reg.register(node)

        is_valid, error = reg.validate_connection("test_1", "output", "nonexistent", "input")
        assert is_valid is False
        assert "not found" in error.lower()

    def test_validate_connection_port_not_found(self):
        """Validar conexão com porta inexistente."""
        reg = NodeRegistry()
        node = MockNode("test_1", "test")
        reg.register(node)

        is_valid, error = reg.validate_connection("test_1", "nonexistent_port", "test_1", "input")
        assert is_valid is False
        assert "Output port not found" in error

    def test_register_factory(self):
        """Factory pode ser registrada."""
        reg = NodeRegistry()

        def factory():
            return MockNode("factory_node", "mock")

        reg.register_factory("mock_type", factory)
        retrieved_factory = reg.get_factory("mock_type")
        assert retrieved_factory == factory

    def test_get_nonexistent_factory(self):
        """Buscar factory inexistente retorna None."""
        reg = NodeRegistry()
        assert reg.get_factory("nonexistent") is None


class TestGlobalRegistry:
    """Testes de registry global (singleton)."""

    def setup_method(self):
        """Setup antes de cada teste."""
        reset_registry()

    def teardown_method(self):
        """Cleanup após cada teste."""
        reset_registry()

    def test_get_registry_returns_singleton(self):
        """get_registry retorna o mesmo objeto."""
        reg1 = get_registry()
        reg2 = get_registry()
        assert reg1 is reg2

    def test_get_registry_creates_on_first_call(self):
        """Primeira chamada cria novo registry (já com os nós conhecidos auto-registrados)."""
        registry = get_registry()
        assert isinstance(registry, NodeRegistry)
        # stt/tts/embeddings/llm/parser/tool/channel_whatsapp/lab_mcp/lab_rest
        assert registry.count() >= 9

    def test_reset_registry(self):
        """reset_registry limpa o global registry (nova instância, auto-registro roda de novo)."""
        reg1 = get_registry()
        base_count = reg1.count()
        node = MockNode("test_extra_node", "test")
        reg1.register(node)
        assert reg1.count() == base_count + 1

        reset_registry()

        reg2 = get_registry()
        assert reg2 is not reg1
        assert reg2.count() == base_count  # nó extra não sobrevive ao reset
        assert reg2.get("test_extra_node") is None
