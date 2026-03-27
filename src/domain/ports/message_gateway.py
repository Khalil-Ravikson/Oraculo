# src/domain/ports/message_gateway.py
from typing import Protocol

class IMessageGateway(Protocol):
    """Contrato para envio de mensagens — agnóstico de provedor."""

    async def enviar_mensagem(self, chat_id: str, texto: str) -> bool:
        """Retorna True se enviou com sucesso."""
        ...

    async def enviar_typing(self, chat_id: str) -> None:
        """Sinaliza 'digitando...' para o usuário."""
        ...