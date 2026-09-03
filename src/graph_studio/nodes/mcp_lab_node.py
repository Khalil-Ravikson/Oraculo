"""MCPLabNode — wrapper de BaseNode sobre mcp_lab.router.tentar_rotear()."""

from typing import Any, Dict, List
from src.graph_studio.base_node import BaseNode, Port, PortType
from src.graph_studio.execution_context import ExecutionContext


class MCPLabNode(BaseNode):
    """
    Nó do laboratório MCP (`mcp_lab/`).

    Delega para `mcp_lab.router.tentar_rotear(mensagem, chat_id)`, que
    intercepta comandos com prefixo `stack `/`brave ` (StackExchange/
    GitHub/Brave Search via gateway MCP `gateway.pipeworx.io`) e retorna
    `None` quando a mensagem não é desse tipo — comportamento de "detour
    opcional", não de nó que sempre produz resultado.

    Continua marcado como laboratório (`docs/historico/
    pesquisa_arquitetura_producao.md` §3): não promovido a feature de
    produto, sem integração de tool-calling nativo do LLM (as tools MCP
    aqui são fixas por regex, não escolhidas pelo modelo). Este nó só
    expõe o laboratório sob a interface BaseNode — não muda essa decisão.
    """

    @property
    def node_id(self) -> str:
        return "lab_mcp"

    @property
    def node_type(self) -> str:
        return "lab_router"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="mensagem",
                type_=PortType.TEXT,
                description="Mensagem do usuário (interceptada só se começar com 'stack '/'brave ')"
            ),
            Port(
                name="chat_id",
                type_=PortType.TEXT,
                description="ID do chat (usado por algumas tools, ex: envio de imagem)",
                required=False
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="resultado",
                type_=PortType.STRUCTURED,
                description="Dict de resposta da tool MCP, ou None se não interceptado"
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

        chat_id = inputs.get("chat_id", "")

        from mcp_lab.router import tentar_rotear

        resultado = await tentar_rotear(mensagem, chat_id)

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
            "description": "Laboratório MCP (StackExchange/GitHub/Brave via gateway.pipeworx.io) — não é produto de produção",
        }
