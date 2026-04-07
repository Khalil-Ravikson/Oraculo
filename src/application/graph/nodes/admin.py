import logging
import re
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

async def node_admin_interceptor(state: "OracleState") -> dict:
    """
    Verifica se a mensagem é um comando admin e se o token já foi validado.

    FLUXO:
      1. Não é admin → passa para classify (sem alteração)
      2. É admin, sem token verificado → verifica se a mensagem é o token
           a) Token correto → marca como verificado, processa comando
           b) Token errado  → solicita novamente
      3. É admin, token verificado → processa comando normalmente

    SEGURANÇA:
      O token de admin é um TOTP ou senha extra definida no .env
      (ADMIN_CONFIRMATION_TOKEN). Diferente da senha do portal web,
      este token é usado para comandos via WhatsApp.
    """
    if not state.get("is_admin"):
        return {}   # não é admin → grafo continua normal

    from src.infrastructure.settings import settings
    msg = (state.get("current_input") or "").strip()

    # ── Retomada após HITL admin ──────────────────────────────────────────────
    if state.get("pending_confirmation"):
        return _processar_resposta_hitl(state, msg)

    # ── Detecta se é um comando admin (começa com ! ou /) ────────────────────
    is_command = bool(re.match(r'^[!/]', msg))
    if not is_command:
        return {}   # admin enviando mensagem normal → trata como aluno

    # ── Verifica double-check de token ────────────────────────────────────────
    if not state.get("admin_token_verified"):
        # Guarda o comando original e solicita o token
        return {
            "pending_confirmation": (
                f"🔐 *Confirmação necessária*\n\n"
                f"Comando recebido: `{msg[:50]}`\n\n"
                f"Por segurança, insira o *token de autorização admin* "
                f"(ADMIN_CONFIRMATION_TOKEN do .env):"
            ),
            "admin_command":        msg,
            "confirmation_result":  "awaiting_token",
            "route":                "respond_only",
            "final_response": (
                f"🔐 *Confirmação necessária*\n\n"
                f"Comando: `{msg[:50]}`\n\n"
                f"Insira o token de autorização:"
            ),
        }

    # Admin já verificado → roteia para handler de comandos
    return {
        "route": "admin",
        "admin_command": msg,
    }


def _processar_resposta_hitl(state: "OracleState", msg: str) -> dict:
    """Processa resposta do admin para uma confirmação pendente."""
    from src.infrastructure.settings import settings

    confirmation_type = state.get("confirmation_result")

    # Verificação do token de autorização
    if confirmation_type == "awaiting_token":
        token_correto = settings.ADMIN_CONFIRMATION_TOKEN
        if msg == token_correto:
            logger.info("✅ Admin token verificado com sucesso.")
            return {
                "admin_token_verified": True,
                "pending_confirmation": None,
                "confirmation_result":  None,
                "route": "admin",
                # Mantém o comando original que estava guardado
            }
        else:
            logger.warning("❌ Token admin incorreto.")
            return {
                "pending_confirmation": None,
                "confirmation_result":  "cancelled",
                "final_response": "❌ Token inválido. Comando cancelado por segurança.",
                "route": "respond_only",
            }

    # Confirmação de ação CRUD (SIM/NÃO)
    msg_lower = msg.lower().strip()
    if msg_lower in ("sim", "s", "yes", "y", "confirmo"):
        return {"confirmation_result": "confirmed"}
    elif msg_lower in ("não", "nao", "n", "no", "cancelar"):
        return {
            "confirmation_result": "cancelled",
            "final_response": "❌ Ação cancelada.",
            "route": "respond_only",
        }
    else:
        return {
            "final_response": (
                f"{state.get('pending_confirmation', '')}\n\n"
                f"Responda *SIM* ou *NÃO*."
            ),
            "route": "respond_only",
        }


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 1 — Admin Command Handler
# ─────────────────────────────────────────────────────────────────────────────

async def node_admin_command(state: "OracleState") -> dict:
    """
    Executa comandos admin sem passar pelo RAG.
    Registra toda ação no IAuditLog.
    """
    command = (state.get("admin_command") or "").strip()
    admin_id = state.get("user_id", "admin")

    logger.info("⚙️  Admin command: %s | admin=%s", command, admin_id)

    try:
        from src.application.use_cases.admin_commands import AdminCommandsUseCase
        # O use case é stateless — criado aqui, sem injeção de estado
        use_case = AdminCommandsUseCase()
        resposta = await use_case.executar(command, admin_id)
        return {
            "final_response":  resposta,
            "audit_action":    command,
            "admin_command":   None,
        }
    except Exception as e:
        logger.exception("❌ Erro no comando admin '%s': %s", command, e)
        return {
            "final_response": f"❌ Erro ao executar `{command[:30]}`: `{str(e)[:80]}`",
        }