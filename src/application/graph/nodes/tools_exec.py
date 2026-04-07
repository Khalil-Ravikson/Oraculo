import logging
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)
# ─────────────────────────────────────────────────────────────────────────────
# NÓ 4 — Ask Confirm (HITL — pede confirmação antes do CRUD)
# ─────────────────────────────────────────────────────────────────────────────

async def node_ask_confirm(state: "OracleState") -> dict:
    """
    Para a execução e pede confirmação ao usuário.
    O LangGraph pausará aqui (interrupt_before=["exec_tool_node"]).
    """
    tool   = state.get("tool_name", "ação")
    args   = state.get("tool_args", {})

    # Descreve o que vai acontecer em linguagem natural
    descricao = _descrever_acao(tool, args)

    pergunta = (
        f"⚠️ *Confirmação necessária*\n\n"
        f"{descricao}\n\n"
        f"Responda *SIM* para confirmar ou *NÃO* para cancelar."
    )

    return {
        "pending_confirmation": pergunta,
        "confirmation_result":  "pending",
        "final_response":       pergunta,
    }


def _descrever_acao(tool: str, args: dict) -> str:
    """Converte nome da tool e argumentos em texto legível."""
    descricoes = {
        "update_student_email":    f"Alterar e-mail para `{args.get('novo_valor', '?')}`",
        "update_student_telefone": f"Alterar telefone para `{args.get('novo_valor', '?')}`",
        "update_student_data":     f"Atualizar dados: {args}",
    }
    return descricoes.get(tool, f"Executar ação `{tool}`")


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 5 — Exec Tool (executa CRUD após confirmação)
# ─────────────────────────────────────────────────────────────────────────────

async def node_exec_tool(state: "OracleState") -> dict:
    """
    Executa a tool CRUD no banco de dados.
    Só é alcançado após interrupt_before ser liberado (confirmação recebida).
    Registra a ação no audit log.
    """
    tool    = state.get("tool_name", "")
    args    = dict(state.get("tool_args") or {})
    user_id = state.get("user_id", "")

    # Garante que user_id está nos args (tools de CRUD sempre precisam)
    args["user_id"] = user_id

    logger.info("🔧 Exec tool '%s' para user=%s", tool, user_id)

    try:
        from src.domain.tools.crud_tools import executar_tool
        resultado = await executar_tool(tool, args)
        mensagem  = resultado.get("mensagem", "✅ Ação concluída.")

        # Registra no audit log
        await _registrar_audit(user_id, tool, args, "ok")

        return {
            "final_response":       mensagem,
            "pending_confirmation": None,
            "confirmation_result":  None,
            "tool_name":            None,
            "tool_args":            None,
        }
    except Exception as e:
        logger.exception("❌ Tool '%s' falhou: %s", tool, e)
        await _registrar_audit(user_id, tool, args, f"erro: {str(e)[:80]}")
        return {
            "final_response": (
                "❌ Ocorreu um erro ao executar a ação. "
                "Tente novamente ou contate o suporte."
            ),
            "pending_confirmation": None,
            "confirmation_result":  None,
        }


async def _registrar_audit(user_id: str, action: str, payload: dict, resultado: str) -> None:
    """Registra ação no audit log de forma não-bloqueante."""
    try:
        from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
        await RedisAuditLog().registar(
            admin_id=user_id, action=action, target=user_id,
            payload=payload, resultado=resultado,
        )
    except Exception as e:
        logger.debug("⚠️  Audit log falhou (não crítico): %s", e)

