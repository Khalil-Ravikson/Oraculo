"""RestLabNode — wrapper de BaseNode sobre rest_lab.router.tentar_rotear()."""

from typing import Any, Dict, List
from src.graph_studio.base_node import BaseNode, Port, PortType
from src.graph_studio.execution_context import ExecutionContext


class RestLabNode(BaseNode):
    """
    Nó do laboratório REST (`rest_lab/`).

    Delega para `rest_lab.router.tentar_rotear(mensagem)`, que intercepta
    comandos com prefixo `rest ` (listar/buscar/criar/atualizar/deletar
    via API REST de estudo) e retorna `None` quando a mensagem não é
    desse tipo — mesmo contrato de "detour opcional" do `MCPLabNode`.

    Continua marcado como laboratório de estudo: regex fastpath puro, sem
    LLM function-calling (decisão deliberada, documentada no próprio
    módulo). Este nó só expõe o laboratório sob a interface BaseNode.
    """

    @property
    def node_id(self) -> str:
        return "lab_rest"

    @property
    def node_type(self) -> str:
        return "lab_router"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="mensagem",
                type_=PortType.TEXT,
                description="Mensagem do usuário (interceptada só se começar com 'rest ')"
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="resultado",
                type_=PortType.STRUCTURED,
                description="Dict de resposta do comando REST, ou None se não interceptado"
            ),
            Port(
                name="intercepted",
                type_=PortType.BOOLEAN,
                description="Se a mensagem foi reconhecida e tratada por este laboratório"
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        mensagem = inputs.get("mensagem")
        if not mensagem:
            raise ValueError("'mensagem' is required")

        from rest_lab.router import tentar_rotear

        resultado = await tentar_rotear(mensagem)

        return {
            "resultado": resultado,
            "intercepted": resultado is not None,
        }

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "Laboratório REST (regex fastpath, sem LLM function-calling) — estudo, não produto",
        }
