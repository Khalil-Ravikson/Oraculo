"""ChannelNode — wrapper de BaseNode sobre EvolutionAdapter (envio WhatsApp)."""

from typing import Any, Dict, List
from src.graph_studio.base_node import BaseNode, Port, PortType
from src.graph_studio.execution_context import ExecutionContext

_ACTIONS = ("text", "typing", "media_url")


class ChannelNode(BaseNode):
    """
    Nó de envio de mensagem por canal (hoje só WhatsApp via EvolutionAdapter).

    Delega para `EvolutionAdapter`, que já resolve base URL/instance/apikey
    via settings. `IMessageGateway` (`src/domain/ports/message_gateway.py`)
    é código morto (nenhum import real, assinatura `chat_id`/`texto` não
    bate com a classe concreta) — este nó usa `EvolutionAdapter` direto,
    não essa interface.

    Meta-node por `action` (mesmo padrão de `ToolNode`), porque
    `EvolutionAdapter` tem vários métodos de envio (texto, digitando,
    mídia, reação, botões, lista) e um nó por método seria repetição —
    hoje cobre os 3 mais usados num fluxo de grafo (`text`/`typing`/
    `media_url`); os demais (reação/botões/lista) seguem o mesmo padrão
    se algum dia forem necessários num grafo, não implementados agora
    (YAGNI).

    **Só cobre o sentido de saída (enviar).** Mensagem recebida (inbound)
    hoje chega via webhook HTTP (`src/application/webhook/webhook_controller.py`)
    + task Celery, não como execução request/response de um nó — é um
    ponto de entrada (trigger), não algo que se chama com inputs e espera
    output no mesmo request. Modelar isso como nó de grafo é escopo da
    Fase 7 completa (canais além do WhatsApp), não coberto aqui.
    """

    @property
    def node_id(self) -> str:
        return "channel_whatsapp"

    @property
    def node_type(self) -> str:
        return "channel"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="number",
                type_=PortType.TEXT,
                description="Número/JID de destino"
            ),
            Port(
                name="action",
                type_=PortType.TEXT,
                description=f"Ação a executar: {', '.join(_ACTIONS)} (default 'text')",
                required=False
            ),
            Port(
                name="payload",
                type_=PortType.STRUCTURED,
                description=(
                    "Argumentos específicos da action — "
                    "'text': {text}; "
                    "'typing': {duration_ms}; "
                    "'media_url': {url, mediatype, mimetype, caption, filename}"
                ),
                required=False
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="ok",
                type_=PortType.BOOLEAN,
                description="Se o envio teve sucesso"
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        number = inputs.get("number")
        if not number:
            raise ValueError("'number' is required")

        action = inputs.get("action", "text")
        if action not in _ACTIONS:
            raise ValueError(f"Unknown action '{action}', expected one of {_ACTIONS}")

        payload = inputs.get("payload", {})

        from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter

        adapter = EvolutionAdapter()

        if action == "text":
            text = payload.get("text")
            if not text:
                raise ValueError("payload.text is required for action 'text'")
            ok = await adapter.enviar_mensagem(number, text)
        elif action == "typing":
            ok = await adapter.enviar_digitando(number, payload.get("duration_ms", 2000))
        else:  # media_url
            url = payload.get("url")
            mediatype = payload.get("mediatype")
            mimetype = payload.get("mimetype")
            if not url or not mediatype or not mimetype:
                raise ValueError("payload.{url,mediatype,mimetype} are required for action 'media_url'")
            ok = await adapter.enviar_midia_url(
                number, url, mediatype, mimetype,
                caption=payload.get("caption", ""),
                filename=payload.get("filename", ""),
            )

        return {"ok": ok}

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "Envio de mensagem WhatsApp via EvolutionAdapter (text/typing/media_url)",
        }
