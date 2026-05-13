"""
MessageRouter — Gatekeeper do Celery.

FLUXO:
  1. GroupFilter      → bloqueia grupos não autorizados
  2. GroupTrigger     → em grupos, só processa $, ! ou @oraculo
  3. RegistrationFunnel → garante cadastro antes de comandos
  4. CommandDispatch  → $ → AdminCommands, ! → PublicCommands
  5. LLMDispatch      → @oraculo ou privado → OracleChain
"""
from __future__ import annotations
import logging, re
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


class DispatchTarget(Enum):
    IGNORE          = auto()
    REGISTER_MODE   = auto()
    ADMIN_COMMAND   = auto()
    PUBLIC_COMMAND  = auto()
    LLM             = auto()


@dataclass
class RouterDecision:
    target:    DispatchTarget
    command:   str = ""          # ex: "M", "CR", "L" para admin; "5" para público
    text:      str = ""          # texto limpo para LLM
    reason:    str = ""


class MessageRouter:
    """
    Stateless. Recebe dados da mensagem + contexto Redis/DB
    e retorna RouterDecision.
    """

    # ── Padrões de trigger ────────────────────────────────────────────────────
    _RE_ADMIN_CMD  = re.compile(r"^\$(\w+)(?:\s+(.*))?$", re.S)
    _RE_PUBLIC_CMD = re.compile(r"^!(\w+)(?:\s+(.*))?$", re.S)
    _RE_MENTION    = re.compile(r"@oraculo\b", re.I)
    _RE_USELESS    = re.compile(
        r"^(ok|okay|certo|tá|ta|sim|não|nao|👍|👌|✅|oi|olá|opa|tudo)\s*[!.?]*$",
        re.I,
    )

    def route(
        self,
        text:          str,
        sender_jid:    str,
        is_group:      bool,
        is_admin:      bool,
        is_registered: bool,
        in_register_mode: bool,
        allowed_group_jid: str,
        remote_jid:    str,
    ) -> RouterDecision:
        
        # ── 0. TRAVA DE SEGURANÇA (MODO BETA) ─────────────────────────────────
        # Se a mensagem for num grupo, mas NÃO for o grupo oficial de testes -> BLOQUEIA
        if is_group and remote_jid != allowed_group_jid:
            return RouterDecision(DispatchTarget.IGNORE, reason="grupo_estranho_ignorado")

        # Se a mensagem for no PRIVADO, mas a pessoa NÃO FOR ADMIN -> BLOQUEIA
        # Isso impede que seus amigos, familiares ou curiosos ativem a IA
        if not is_group and not is_admin:
            return RouterDecision(DispatchTarget.IGNORE, reason="privado_bloqueado_no_beta")

        # ── 1. Gatilhos dentro do Grupo Permitido ─────────────────────────────
        if is_group:
            # Dentro do grupo: só responde a comandos ($, !) ou se o bot for mencionado (@oraculo)
            if not (text.startswith("$") or text.startswith("!") or
                    self._RE_MENTION.search(text)):
                return RouterDecision(DispatchTarget.IGNORE, reason="sem_trigger_grupo")

        # ── 2. Mensagem inútil em privado ─────────────────────────────────────
        if not is_group and self._RE_USELESS.match(text):
            return RouterDecision(DispatchTarget.IGNORE, reason="mensagem_inutil")

        # ── 3. Admin Commands ($) — não requer cadastro ───────────────────────
        if text.startswith("$"):
            if not is_admin:
                return RouterDecision(DispatchTarget.IGNORE, reason="nao_e_admin")
            m = self._RE_ADMIN_CMD.match(text)
            if m:
                return RouterDecision(
                    DispatchTarget.ADMIN_COMMAND,
                    command=m.group(1).upper(),
                    text=(m.group(2) or "").strip(),
                )
            return RouterDecision(DispatchTarget.IGNORE, reason="admin_cmd_invalido")

        # ── 4. Funil de Cadastro ──────────────────────────────────────────────
        if in_register_mode:
            # Mensagem é parte do fluxo de cadastro — não roteia para outros targets
            return RouterDecision(DispatchTarget.REGISTER_MODE, text=text)

        if not is_registered:
            return RouterDecision(DispatchTarget.REGISTER_MODE, text=text)

        # ── 5. Public Commands (!) ────────────────────────────────────────────
        if text.startswith("!"):
            m = self._RE_PUBLIC_CMD.match(text)
            if m:
                return RouterDecision(
                    DispatchTarget.PUBLIC_COMMAND,
                    command=m.group(1).lower(),
                    text=(m.group(2) or "").strip(),
                )

        # ── 6. LLM — @oraculo em grupo OU texto livre em privado ─────────────
        clean_text = self._RE_MENTION.sub("", text).strip()
        if not clean_text:
            return RouterDecision(DispatchTarget.IGNORE, reason="texto_vazio_apos_mention")

        return RouterDecision(DispatchTarget.LLM, text=clean_text)