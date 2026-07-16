import pytest
from unittest.mock import MagicMock, AsyncMock
from src.application.routing.command_builder import (
    CommandContext,
    dispatch_admin,
    dispatch_public,
    _ADMIN_COMMANDS,
    _PUBLIC_COMMANDS,
    _autodiscover_commands,
)


@pytest.fixture
def mock_context():
    """Cria um contexto de comando mockado para os testes."""
    return CommandContext(
        sender_jid="5598999999999@s.whatsapp.net",
        chat_id="5598999999999@g.us",
        text="",
        redis_text=MagicMock(),
        db_session=AsyncMock(),
    )


def test_autodiscover_commands_registers_correctly():
    """Garante que a autodescoberta é executada e carrega comandos básicos no registro."""
    _autodiscover_commands()
    
    # Comandos de Gerência ($)
    assert "M" in _ADMIN_COMMANDS
    assert "MO" in _ADMIN_COMMANDS
    assert "L" in _ADMIN_COMMANDS
    assert "CR" in _ADMIN_COMMANDS
    assert "C" in _ADMIN_COMMANDS
    
    # Comandos Públicos (!)
    assert "feedback" in _PUBLIC_COMMANDS
    assert "ytb" in _PUBLIC_COMMANDS
    assert "sticker" in _PUBLIC_COMMANDS
    
    # Aliases de avaliação !1 a !5
    for i in range(1, 6):
        assert str(i) in _PUBLIC_COMMANDS
        assert _PUBLIC_COMMANDS[str(i)] == _PUBLIC_COMMANDS["feedback"]


@pytest.mark.asyncio
async def test_dispatch_admin_maintenance_on(mock_context):
    """Garante que o comando de ativação de manutenção ($M) funciona."""
    mock_context.r.set = MagicMock()
    
    resposta = await dispatch_admin("M", mock_context)
    
    assert "Modo manutenção *ATIVADO*" in resposta
    mock_context.r.set.assert_called_once_with("admin:gemini_blocked", "1")


@pytest.mark.asyncio
async def test_dispatch_admin_maintenance_off(mock_context):
    """Garante que o comando de desativação de manutenção ($MO) funciona."""
    mock_context.r.delete = MagicMock()
    
    resposta = await dispatch_admin("MO", mock_context)
    
    assert "Modo manutenção *DESATIVADO*" in resposta
    mock_context.r.delete.assert_called_once_with("admin:gemini_blocked")


@pytest.mark.asyncio
async def test_dispatch_public_unknown():
    """Garante que um comando público inexistente devolve mensagem de erro apropriada."""
    ctx = CommandContext("sender", "chat", "", MagicMock())
    resposta = await dispatch_public("comando_inexistente", ctx)
    assert "não reconhecido" in resposta
