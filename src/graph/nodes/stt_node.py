"""STTNode — wrapper de BaseNode sobre AudioService.transcribe()."""

from typing import Any, Dict, List
from src.graph.base_node import BaseNode, Port, PortType
from src.graph.execution_context import ExecutionContext


class STTNode(BaseNode):
    """
    Nó de Speech-to-Text.

    Delega para `AudioService.transcribe()`, que já resolve o provider
    configurado (`settings.STT_PROVIDER`, hoje só "gemini") e grava
    telemetria de custo (Postgres + Prometheus). Este nó não reimplementa
    nada — só expõe o serviço existente sob a interface BaseNode, pra
    aparecer no NodeRegistry e poder ser conectado em um grafo.

    Trocar de provider continua sendo config (`STT_PROVIDER`), não código —
    este nó não hardcoda "gemini" em lugar nenhum.
    """

    @property
    def node_id(self) -> str:
        return "stt_default"

    @property
    def node_type(self) -> str:
        return "stt_provider"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="audio_bytes",
                type_=PortType.AUDIO,
                description="Bytes de áudio a transcrever"
            ),
            Port(
                name="mime_type",
                type_=PortType.TEXT,
                description="MIME type do áudio (audio/ogg, audio/mp4, audio/wav, audio/webm)",
                required=False
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="text",
                type_=PortType.TEXT,
                description="Texto transcrito"
            ),
            Port(
                name="ok",
                type_=PortType.BOOLEAN,
                description="Se a transcrição teve sucesso"
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        audio_bytes = inputs.get("audio_bytes")
        if audio_bytes is None:
            raise ValueError("'audio_bytes' is required")

        mime_type = inputs.get("mime_type", "audio/ogg")

        from src.infrastructure.services.audio_service import get_audio_service

        service = get_audio_service()
        result = await service.transcribe(audio_bytes, mime_type)

        if not result.ok:
            raise RuntimeError(f"STT failed: {result.error}")

        return {
            "text": result.text,
            "ok": result.ok,
        }

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "Speech-to-Text via AudioService (provider configurável em settings.STT_PROVIDER)",
        }
