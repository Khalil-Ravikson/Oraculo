"""TriggerNode — nó-fonte: injeta a mensagem de teste no início de um fluxo."""

from typing import Any, Dict, List
from src.graph.base_node import BaseNode, Port, PortType
from src.graph.execution_context import ExecutionContext


class TriggerNode(BaseNode):
    """
    Ponto de entrada de um fluxo montado no Graph Studio.

    Não fala com nenhum serviço — só devolve, como saída, a frase que o
    operador digitou no painel de teste (`inputs["mensagem_teste"]`) e um
    rótulo de rota (`inputs["rota"]`, default "SANDBOX"). É o nó que torna a
    pipeline mínima do exemplo legível no canvas: **Mensagem de teste → LLM**.

    Como não tem portas de entrada, o `GraphExecutor` o trata como fonte e
    entrega os `inputs` iniciais da execução (ver `graph_executor.py`).
    """

    @property
    def node_id(self) -> str:
        return "trigger_mensagem"

    @property
    def node_type(self) -> str:
        return "trigger"

    @property
    def input_ports(self) -> List[Port]:
        return []

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="text",
                type_=PortType.TEXT,
                description="Texto da mensagem de teste"
            ),
            Port(
                name="rota",
                type_=PortType.TEXT,
                description="Rótulo de rota (telemetria) — 'SANDBOX' no teste do Studio",
                required=False
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        return {
            "text": inputs.get("mensagem_teste", ""),
            "rota": inputs.get("rota", "SANDBOX"),
        }

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": (
                "Ponto de entrada para testar um fluxo — injeta a frase que "
                "você digitar no painel de teste."
            ),
        }
