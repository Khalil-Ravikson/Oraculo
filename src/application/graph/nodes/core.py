import logging
import re
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)


async def node_classify(state: "OracleState") -> dict:
    """
    Classifica a intenção da mensagem e define a rota.
    Não chama LLM — usa routing semântico Redis (0 tokens).
    """
    msg = (state.get("current_input") or "").strip()

    # ── Retomada HITL (confirmação CRUD pendente) ─────────────────────────────
    if state.get("pending_confirmation") and state.get("confirmation_result") not in (
        "confirmed", "cancelled", "awaiting_token"
    ):
        msg_lower = msg.lower().strip()
        if msg_lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
            return {"confirmation_result": "confirmed"}
        elif msg_lower in ("não", "nao", "n", "no", "cancelar"):
            return {
                "confirmation_result": "cancelled",
                "final_response": "❌ Operação cancelada.",
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

    # ── Verificação de modo manutenção ────────────────────────────────────────
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        if r.get("admin:maintenance_mode") == "1" and not state.get("is_admin"):
            return {
                "final_response": (
                    "🔧 *O Oráculo está em manutenção para melhorias.*\n\n"
                    "Voltarei em breve com novidades! 🎓"
                ),
                "route": "respond_only",
            }
    except Exception:
        pass

    # ── Routing semântico (0 tokens) ──────────────────────────────────────────
    try:
        from src.domain.semantic_router import rotear
        from src.domain.entities import EstadoMenu
        resultado = rotear(msg, EstadoMenu.MAIN)
        rota = resultado.rota.value.lower()
    except Exception:
        rota = "geral"

    # ── Detecta intent CRUD ───────────────────────────────────────────────────
    _CRUD_RE = re.compile(
        r"(atualiz|mudar|alterar|corrig|trocar|editar|modificar).{0,30}"
        r"(nome|email|telefone|matrícula|curso|senha|dados)",
        re.IGNORECASE,
    )
    if _CRUD_RE.search(msg) and state.get("user_role") != "guest":
        return {"route": "crud", "tool_name": "update_student_data"}

    # ── Saudações curtas → resposta imediata ──────────────────────────────────
    if len(msg.split()) <= 3 and re.match(
        r"^(oi|olá|ola|bom\s*dia|boa\s*tarde|boa\s*noite|ei|hey)$",
        msg, re.IGNORECASE,
    ):
        return {"route": "greeting"}

    return {"route": rota or "rag"}


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 3 — RAG (recuperação + geração)
# ─────────────────────────────────────────────────────────────────────────────

async def node_rag(state: "OracleState") -> dict:
    """
    RAG custom em Python puro — sem LangChain chains.
    Lê o system_prompt do Redis (prompt dinâmico do admin).
    """
    from src.application.graph.prompts import montar_prompt_geracao

    msg     = state.get("current_input", "")
    user_ctx = state.get("user_context", {})
    perfil  = (
        f"Aluno: {state.get('user_name', '')} | "
        f"Curso: {user_ctx.get('curso', '?')} | "
        f"Período: {user_ctx.get('periodo', '?')}"
        if user_ctx.get("curso") else ""
    )

    # ── Recupera contexto RAG ─────────────────────────────────────────────────
    contexto_rag = ""
    crag_score   = 0.0
    try:
        from src.application.use_cases.retrieve_context_use_case import RetrieveContextUseCase
        from src.infrastructure.adapters.redis_vector_adapter import RedisVectorAdapter
        from src.rag.query_transform import QueryTransformada

        qt = QueryTransformada(
            query_original=msg, query_principal=msg, foi_transformada=False,
        )
        resultado = await RetrieveContextUseCase(RedisVectorAdapter()).executar(qt)
        contexto_rag = resultado.contexto_formatado if resultado.encontrou else ""
        if resultado.chunks:
            scores = [c.rrf_score for c in resultado.chunks if c.rrf_score > 0]
            crag_score = sum(scores) / len(scores) if scores else 0.0
    except Exception as e:
        logger.warning("⚠️  RAG falhou, gerando sem contexto: %s", e)

    # ── System prompt dinâmico (admin pode alterar via Redis) ─────────────────
    system_prompt = _get_dynamic_system_prompt()

    # ── Monta prompt final ────────────────────────────────────────────────────
    prompt = montar_prompt_geracao(
        pergunta=msg,
        contexto_rag=contexto_rag,
        perfil_usuario=perfil,
    )

    # ── Geração via LLM provider ──────────────────────────────────────────────
    resposta_texto = "Desculpe, não consegui gerar uma resposta no momento."
    try:
        from src.infrastructure.adapters.gemini_provider import GeminiProvider
        llm = GeminiProvider()
        resposta = await llm.gerar_resposta_async(
            prompt=prompt,
            system_instruction=system_prompt,
        )
        if resposta.sucesso:
            resposta_texto = resposta.conteudo
    except Exception as e:
        logger.exception("❌ LLM falhou: %s", e)

    return {
        "final_response": resposta_texto,
        "rag_context":    contexto_rag[:500] if contexto_rag else "",
        "crag_score":     crag_score,
    }


def _get_dynamic_system_prompt() -> str:
    """Lê o system prompt do Redis — permite alteração em tempo real pelo admin."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        from src.application.graph.prompts import SYSTEM_UEMA
        custom = get_redis_text().get("admin:system_prompt")
        return custom or SYSTEM_UEMA
    except Exception:
        from src.application.graph.prompts import SYSTEM_UEMA
        return SYSTEM_UEMA