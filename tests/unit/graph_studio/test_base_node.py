"""Testes de BaseNode e classes relacionadas."""

import pytest
from datetime import datetime
from src.graph_studio.base_node import BaseNode, Port, PortType, NodeHealthStatus
from src.graph_studio.execution_context import ExecutionContext


class TestPortType:
    """Testes do enum PortType."""

    def test_port_type_values(self):
        """Valida que tipos de porta existem."""
        assert PortType.LLM_RESPONSE == "llm_response"
        assert PortType.EMBEDDINGS == "embeddings"
        assert PortType.TEXT == "text"
        assert PortType.STRUCTURED == "structured"

    def test_port_type_is_string_enum(self):
        """PortType é string enum."""
        assert isinstance(PortType.TEXT, str)


class TestPort:
    """Testes de Port."""

    def test_port_creation(self):
        """Port pode ser criada."""
        port = Port(
            name="prompt",
            type_=PortType.TEXT,
            description="Prompt pra enviar"
        )
        assert port.name == "prompt"
        assert port.type_ == PortType.TEXT
        assert port.description == "Prompt pra enviar"
        assert port.required is True

    def test_port_with_optional(self):
        """Port pode ser opcional."""
        port = Port(
            name="context",
            type_=PortType.STRUCTURED,
            description="Contexto",
            required=False
        )
        assert port.required is False

    def test_port_with_schema(self):
        """Port pode ter JSON schema."""
        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        port = Port(
            name="data",
            type_=PortType.STRUCTURED,
            description="Dados",
            schema=schema
        )
        assert port.schema == schema

    def test_port_invalid_type_raises(self):
        """Port com type_ inválido lança erro."""
        with pytest.raises(ValueError):
            Port(
                name="test",
                type_=12345,  # Inválido
                description="Test"
            )


class TestNodeHealthStatus:
    """Testes de NodeHealthStatus."""

    def test_health_status_healthy(self):
        """NodeHealthStatus healthy."""
        status = NodeHealthStatus(
            is_healthy=True,
            last_checked="2026-09-02T10:00:00"
        )
        assert status.is_healthy is True
        assert status.error_message is None

    def test_health_status_unhealthy(self):
        """NodeHealthStatus unhealthy com erro."""
        status = NodeHealthStatus(
            is_healthy=False,
            last_checked="2026-09-02T10:00:00",
            error_message="Connection timeout",
            details={"retry_count": 3}
        )
        assert status.is_healthy is False
        assert status.error_message == "Connection timeout"
        assert status.details == {"retry_count": 3}


class TestExecutionContext:
    """Testes de ExecutionContext."""

    def test_context_creation(self):
        """ExecutionContext pode ser criada."""
        ctx = ExecutionContext()
        assert ctx.execution_id is not None
        assert ctx.tenant_id is None
        assert ctx.parent_execution_id is None
        assert isinstance(ctx.started_at, str)

    def test_context_with_tenant(self):
        """ExecutionContext com tenant_id."""
        ctx = ExecutionContext(tenant_id="UEMA")
        assert ctx.tenant_id == "UEMA"

    def test_context_with_metadata(self):
        """ExecutionContext pode armazenar metadados."""
        ctx = ExecutionContext()
        ctx.set_metadata("user_id", "user123")
        ctx.set_metadata("session_id", "sess456")

        assert ctx.get_metadata("user_id") == "user123"
        assert ctx.get_metadata("session_id") == "sess456"
        assert ctx.get_metadata("nonexistent", "default") == "default"

    def test_context_with_child(self):
        """ExecutionContext pode criar child context."""
        parent = ExecutionContext(tenant_id="UEMA")
        parent.set_metadata("user_id", "user123")

        child = parent.with_child()
        assert child.parent_execution_id == parent.execution_id
        assert child.tenant_id == "UEMA"
        assert child.execution_id != parent.execution_id
        assert child.get_metadata("user_id") == "user123"

    def test_context_child_different_tenant(self):
        """Child context pode ter tenant diferente."""
        parent = ExecutionContext(tenant_id="UEMA")
        child = parent.with_child(child_tenant_id="PARTNER")
        assert child.tenant_id == "PARTNER"
        assert parent.tenant_id == "UEMA"


class TestBaseNode:
    """Testes de BaseNode."""

    def test_base_node_is_abstract(self):
        """BaseNode não pode ser instanciada direto."""
        with pytest.raises(TypeError):
            BaseNode()

    def test_base_node_subclass_must_implement_abstract(self):
        """Subclass deve implementar todos abstract methods."""

        class IncompleteNode(BaseNode):
            @property
            def node_id(self):
                return "incomplete"

            # Faltam: node_type, input_ports, output_ports, execute

        with pytest.raises(TypeError):
            IncompleteNode()

    def test_base_node_concrete_subclass(self):
        """Subclass concreto pode ser instanciado."""

        class MockNode(BaseNode):
            @property
            def node_id(self):
                return "mock_1"

            @property
            def node_type(self):
                return "mock"

            @property
            def input_ports(self):
                return [Port("input", PortType.TEXT, "Test input")]

            @property
            def output_ports(self):
                return [Port("output", PortType.TEXT, "Test output")]

            async def execute(self, inputs, context):
                return {"output": inputs.get("input", "").upper()}

        node = MockNode()
        assert node.node_id == "mock_1"
        assert node.node_type == "mock"
        assert len(node.input_ports) == 1
        assert len(node.output_ports) == 1

    def test_base_node_health_check_optional(self):
        """health_check é opcional (default: None)."""

        class MockNode(BaseNode):
            @property
            def node_id(self):
                return "mock_2"

            @property
            def node_type(self):
                return "mock"

            @property
            def input_ports(self):
                return []

            @property
            def output_ports(self):
                return []

            async def execute(self, inputs, context):
                return {}

        node = MockNode()
        assert node.health_check is None

    def test_base_node_config_schema_optional(self):
        """config_schema é opcional (default: {})."""

        class MockNode(BaseNode):
            @property
            def node_id(self):
                return "mock_3"

            @property
            def node_type(self):
                return "mock"

            @property
            def input_ports(self):
                return []

            @property
            def output_ports(self):
                return []

            async def execute(self, inputs, context):
                return {}

        node = MockNode()
        assert node.config_schema == {}

    def test_base_node_metadata_has_defaults(self):
        """metadata tem defaults."""

        class MockNode(BaseNode):
            @property
            def node_id(self):
                return "mock_4"

            @property
            def node_type(self):
                return "mock"

            @property
            def input_ports(self):
                return []

            @property
            def output_ports(self):
                return []

            async def execute(self, inputs, context):
                return {}

        node = MockNode()
        metadata = node.metadata
        assert metadata["name"] == "mock_4"
        assert metadata["type"] == "mock"
        assert "version" in metadata
        assert "description" in metadata

    @pytest.mark.asyncio
    async def test_base_node_execute(self):
        """execute é assíncrono."""

        class MockNode(BaseNode):
            @property
            def node_id(self):
                return "mock_5"

            @property
            def node_type(self):
                return "mock"

            @property
            def input_ports(self):
                return [Port("msg", PortType.TEXT, "Message")]

            @property
            def output_ports(self):
                return [Port("result", PortType.TEXT, "Result")]

            async def execute(self, inputs, context):
                await __import__("asyncio").sleep(0.01)  # Simula async work
                return {"result": f"Processed: {inputs.get('msg')}"}

        node = MockNode()
        ctx = ExecutionContext()
        result = await node.execute({"msg": "hello"}, ctx)
        assert result["result"] == "Processed: hello"
