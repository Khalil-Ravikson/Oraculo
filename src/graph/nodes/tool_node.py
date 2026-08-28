"""ToolNode — wrapper de BaseNode sobre capabilities/registry.py::executar_tool()."""

from typing import Any, Dict, List
from src.graph.base_node import BaseNode, Port, PortType
from src.graph.execution_context import ExecutionContext


class ToolNode(BaseNode):
    """
    Nó de execução de tool (capability).

    Delega para `capabilities/registry.py::executar_tool(tool_name, args)`,
    que já faz autodiscovery (`pkgutil`) das tools registradas via
    decorator `@tool(...)` e despacha pra função async correspondente.
    Este nó é um "meta-node": não representa UMA tool específica, e sim o
    dispatcher de tools inteiro — `tool_name` escolhe qual tool roda em
    cada execução, permitindo que um grafo declarativo (Fase 2 do adendo
    de nós, ainda não implementada) referencie tools sem precisar de um
    nó por tool.
    """

    @property
    def node_id(self) -> str:
        return "tool_default"

    @property
    def node_type(self) -> str:
        return "tool"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="tool_name",
                type_=PortType.TEXT,
                description="Nome da tool registrada a executar (ver registry.available())"
            ),
            Port(
                name="args",
                type_=PortType.STRUCTURED,
                description="Argumentos da tool (dict, repassado como **kwargs)",
                required=False
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="result",
                type_=PortType.STRUCTURED,
                description="Dict de retorno da tool"
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        tool_name = inputs.get("tool_name")
        if not tool_name:
            raise ValueError("'tool_name' is required")

        args = inputs.get("args", {})

        from src.capabilities.registry import executar_tool

        result = await executar_tool(tool_name, args)

        return {"result": result}

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "Dispatcher de tools via capabilities/registry.py (autodiscovery + manifesto de capability)",
        }
