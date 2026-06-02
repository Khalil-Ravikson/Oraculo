from __future__ import annotations
import logging
import importlib
import pkgutil
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class CommandContext:
    """Carrega tudo que um comando pode precisar, mantendo as dependências injetadas."""
    def __init__(
        self,
        sender_jid:  str,
        chat_id:     str,
        text:        str,         # Argumento passado após o gatilho do comando
        redis_text,               # Cliente Redis com decode_responses=True
        db_session=None,
    ):
        self.sender_jid = sender_jid
        self.chat_id    = chat_id
        self.text       = text
        self.r          = redis_text
        self.db         = db_session


class BaseCommand(ABC):
    """Classe base abstrata para todos os comandos de atendimento e gerência."""
    trigger: str

    @abstractmethod
    async def execute(self, ctx: CommandContext) -> str:
        """Executa a lógica de negócio do comando e retorna a mensagem de resposta."""
        pass


_ADMIN_COMMANDS: dict[str, BaseCommand] = {}
_PUBLIC_COMMANDS: dict[str, BaseCommand] = {}
_COMMANDS_LOADED = False


def register_command(trigger: str, is_admin: bool = False):
    """
    Decorador para auto-registrar comandos.
    Mapeia comandos de gerência ($) e de uso público (!).
    """
    def decorator(cls):
        instance = cls()
        instance.trigger = trigger
        if is_admin:
            _ADMIN_COMMANDS[trigger.upper()] = instance
            logger.info("✅ [COMMAND BUILDER] Comando Admin registrado: '$%s'", trigger.upper())
        else:
            _PUBLIC_COMMANDS[trigger.lower()] = instance
            logger.info("✅ [COMMAND BUILDER] Comando Público registrado: '!%s'", trigger.lower())
        return cls
    return decorator


def _autodiscover_commands():
    """
    Varre dinamicamente a pasta src/application/commands e carrega todos
    os arquivos para registrar os decoradores de comandos.
    """
    global _COMMANDS_LOADED
    if _COMMANDS_LOADED:
        return

    try:
        import src.application.commands as commands_pkg
    except ImportError:
        logger.warning("⚠️  [COMMAND BUILDER] Pacote src.application.commands não pôde ser importado.")
        return

    # Percorre todos os arquivos dentro do pacote commands
    for _, module_name, is_pkg in pkgutil.iter_modules(commands_pkg.__path__):
        if not is_pkg:
            full_module_name = f"src.application.commands.{module_name}"
            try:
                importlib.import_module(full_module_name)
            except Exception as e:
                logger.error("❌ [COMMAND BUILDER] Erro ao carregar comando %s: %s", full_module_name, e)

    # Adiciona aliases do feedback para os números de avaliação 1 a 5
    feedback_cmd = _PUBLIC_COMMANDS.get("feedback")
    if feedback_cmd:
        for i in range(1, 6):
            _PUBLIC_COMMANDS[str(i)] = feedback_cmd

    _COMMANDS_LOADED = True


async def dispatch_admin(trigger: str, ctx: CommandContext) -> str:
    """Despacha a execução para o comando de administração correspondente."""
    _autodiscover_commands()
    cmd = _ADMIN_COMMANDS.get(trigger.upper())
    if not cmd:
        return f"❓ Comando `${trigger}` não reconhecido. Disponíveis: {list(_ADMIN_COMMANDS.keys())}"
    if trigger in ("1", "2", "3", "4", "5"):
        ctx.text = trigger
    return await cmd.execute(ctx)


async def dispatch_public(trigger: str, ctx: CommandContext) -> str:
    """Despacha a execução para o comando público correspondente."""
    _autodiscover_commands()
    cmd = _PUBLIC_COMMANDS.get(trigger.lower())
    if not cmd:
        return f"❓ Comando `!{trigger}` não reconhecido."
    if trigger in ("1", "2", "3", "4", "5"):
        ctx.text = trigger
    return await cmd.execute(ctx)
