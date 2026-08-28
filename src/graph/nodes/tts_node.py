"""TTSNode — wrapper de BaseNode sobre AudioService.synthesize()."""

from typing import Any, Dict, List
from src.graph.base_node import BaseNode, Port, PortType
from src.graph.execution_context import ExecutionContext


class TTSNode(BaseNode):
    """
    Nó de Text-to-Speech.

    Delega para `AudioService.synthesize()`, que resolve o provider
    configurado (`settings.TTS_PROVIDER`, hoje "kokoro" ou "gtts") e
    grava telemetria (custo sempre $0 — providers locais). Este nó não
    reimplementa síntese, só expõe o serviço existente sob a interface
    BaseNode.
    """

    @property
    def node_id(self) -> str:
        return "tts_default"

    @property
    def node_type(self) -> str:
        return "tts_provider"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="text",
                type_=PortType.TEXT,
                description="Texto a sintetizar em áudio"
            ),
            Port(
                name="lang",
                type_=PortType.TEXT,
                description="Idioma (default 'pt')",
                required=False
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="audio_path",
                type_=PortType.FILE,
                description="Caminho do arquivo de áudio gerado (MP3, temporário)"
            ),
            Port(
                name="ok",
                type_=PortType.BOOLEAN,
                description="Se a síntese teve sucesso"
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        text = inputs.get("text")
        if not text:
            raise ValueError("'text' is required")

        lang = inputs.get("lang", "pt")

        from src.infrastructure.services.audio_service import get_audio_service

        service = get_audio_service()
        result = await service.synthesize(text, lang)

        if not result.ok:
            raise RuntimeError(f"TTS failed: {result.error}")

        return {
            "audio_path": result.audio_path,
            "ok": result.ok,
        }

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "Text-to-Speech via AudioService (provider configurável em settings.TTS_PROVIDER)",
        }
