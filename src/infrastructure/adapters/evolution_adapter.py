# src/infrastructure/adapters/evolution_adapter.py
from src.domain.ports.message_gateway import IMessageGateway
from src.services.evolution_service import EvolutionService

class EvolutionAdapter(IMessageGateway):
    """Adapta o EvolutionService existente para o Port IMessageGateway."""

    def __init__(self):
        self._svc = EvolutionService()

    async def enviar_mensagem(self, chat_id: str, texto: str) -> bool:
        return await self._svc.enviar_mensagem(chat_id, texto)

    async def enviar_typing(self, chat_id: str) -> None:
        # Evolution API suporta typing indicator
        pass