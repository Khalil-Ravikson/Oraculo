"""
src/application/graph/nodes/core.py — Nós Centrais (Classificação e RAG)
========================================================================
Este arquivo contém os nós principais do LangGraph:
1. node_classify: Delega o roteamento para o OraculoRouter (Semântico + LLM).
2. node_rag: Executa o Pipeline RAG (Recuperação e Geração) se for o caso.
"""
import logging
import re
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Singletons & Helpers de Injeção
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_llm_provider():
    """LLM Provider singleton — evita re-instanciar o cliente Gemini."""
    from src.infrastructure.adapters.gemini_provider import GeminiProvider
    return GeminiProvider()

@lru_cache(maxsize=1)
def _get_retriever():
    """Retriever singleton com adapter pré-aquecido."""
    from src.application.use_cases.retrieve_context_use_case import RetrieveContextUseCase
    from src.infrastructure.adapters.redis_vector_adapter import RedisVectorAdapter
    return RetrieveContextUseCase(RedisVectorAdapter())

def _get_system_prompt() -> str:
    """Lê system prompt do Redis (admin pode alterar via !prompt)."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        from src.application.graph.prompts import SYSTEM_UEMA
        custom = get_redis_text().get("admin:system_prompt")
        if isinstance(custom, bytes):
            custom = custom.decode()
        return custom or SYSTEM_UEMA
    except Exception:
        from src.application.graph.prompts import SYSTEM_UEMA
        return SYSTEM_UEMA

# ─────────────────────────────────────────────────────────────────────────────
# NÓ 1: CLASSIFY (O Porteiro Inteligente)
# ─────────────────────────────────────────────────────────────────────────────

async def node_classify(state: "OracleState", oraculo_router) -> dict:
    """
    Nó do LangGraph responsável por definir o state["route"].
    Usa o OraculoRouter (Injetado via Builder) que orquestra o Semantic e o Pydantic Router.
    """
    msg = (state.get("current_input") or "").strip()
    is_admin = state.get("is_admin", False)

    # ── 1. Retomada HITL (Human-in-the-Loop) ──
    pending = state.get("pending_confirmation")
    conf_result = state.get("confirmation_result")
    if pending and conf_result not in ("confirmed", "cancelled", "awaiting_token"):
        msg_lower = msg.lower().strip()
        if msg_lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
            return {"confirmation_result": "confirmed"}
        elif msg_lower in ("não", "nao", "n", "no", "cancelar"):
            return {
                "confirmation_result": "cancelled",
                "final_response": "❌ Operação cancelada.",
                "route": "respond_only",
            }
        return {
            "final_response": f"{pending}\n\nResponda *SIM* para confirmar ou *NÃO* para cancelar.",
            "route": "respond_only",
        }

    # ── 2. Modo manutenção ──
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        maintenance = r.get("admin:maintenance_mode")
        if isinstance(maintenance, bytes): maintenance = maintenance.decode()
        if maintenance == "1" and not is_admin:
            return {
                "final_response": "🔧 *O Oráculo está em manutenção para melhorias.*\n\nVoltarei em breve! 🎓",
                "route": "respond_only",
            }
    except Exception:
        pass

    # ── 3. O Roteamento de Elite (Oraculo Router Cascade) ──
    contexto = {
        "curso": state.get("curso"),
        "periodo": state.get("periodo"),
        "centro": state.get("centro"),
    }
    
    # Executa a regra de negócio do roteador injetado
    resultado = oraculo_router.rotear(mensagem=msg, contexto=contexto, is_admin=is_admin)

    logger.info("🚦 Classificação Final: Rota '%s' | Skip Cache: %s", 
                resultado.get("route", "rag_node"), resultado.get("skip_cache", False))

    # O LangGraph vai pegar esse dict e atualizar o OracleState
    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 2: RAG (O Cientista Pesquisador)
# ─────────────────────────────────────────────────────────────────────────────

async def node_rag(state: "OracleState") -> dict:
    """
    Pipeline RAG: recupera contexto → gera resposta via Gemini.
    """
    from src.application.graph.prompts import montar_prompt_geracao

    msg      = state.get("current_input", "")
    user_ctx = state.get("user_context") or {}
    curso    = state.get("curso") or user_ctx.get("curso", "")
    periodo  = state.get("periodo") or user_ctx.get("periodo", "")
    centro   = state.get("centro") or ""
    nome     = state.get("user_name", "")

    perfil_str = ""
    if nome or curso:
        partes = []
        if nome:    partes.append(f"Aluno: {nome}")
        if curso:   partes.append(f"Curso: {curso}")
        if periodo: partes.append(f"Período: {periodo}")
        if centro:  partes.append(f"Centro: {centro}")
        perfil_str = " | ".join(partes)

    # ── Recuperação RAG ──
    contexto_rag = ""
    crag_score   = 0.0
    fonte        = ""

    try:
        from src.rag.query.transformer import QueryTransformer
        from src.rag.query.protocols import RawQuery

        route   = state.get("route", "geral").upper()
        qt_raw  = RawQuery(text=msg, fatos_usuario=[])
        transformer = QueryTransformer.build_for_route(route)
        qt      = transformer.transform(qt_raw)

        from src.rag.query.protocols import TransformedQuery
        transformed = TransformedQuery(
            original=qt.original,
            primary=qt.primary,
            variants=getattr(qt, "variants", []),
            strategy_used=getattr(qt, "strategy_used", "passthrough"),
            was_transformed=getattr(qt, "was_transformed", False),
        )

        retriever = _get_retriever()
        resultado = await retriever.executar(transformed)

        if resultado.encontrou:
            contexto_rag = resultado.contexto_formatado
            if resultado.chunks:
                scores = [c.rrf_score for c in resultado.chunks if c.rrf_score > 0]
                crag_score = sum(scores) / len(scores) if scores else 0.0
                fonte = resultado.chunks[0].titulo_fonte if resultado.chunks else ""

    except Exception as e:
        logger.warning("⚠️ RAG retrieval falhou: %s — gerando sem contexto", e)

    # ── Geração LLM ──
    system_prompt = _get_system_prompt()
    prompt_final  = montar_prompt_geracao(
        pergunta=msg,
        contexto_rag=contexto_rag,
        perfil_usuario=perfil_str,
    )

    resposta_texto = ""
    try:
        llm = _get_llm_provider()
        resp = await llm.gerar_resposta_async(
            prompt=prompt_final,
            system_instruction=system_prompt,
            temperatura=0.2,
            max_tokens=1024,
        )

        if resp.sucesso and resp.conteudo:
            resposta_texto = resp.conteudo
            logger.info("✅ node_rag | user=%s | crag=%.3f | fonte=%s",
                        (state.get("user_id") or "?")[-8:], crag_score, fonte[:40])
        else:
            logger.error("❌ LLM retornou vazio: %s", resp.erro)
            resposta_texto = "Desculpe, tive dificuldade em gerar uma resposta. Pode reformular a pergunta?"

    except Exception as e:
        logger.exception("❌ Gemini falhou em node_rag: %s", e)
        resposta_texto = "Estou tendo uma instabilidade técnica momentânea. Tente novamente em alguns segundos. 🙏"

    return {
        "final_response": resposta_texto,
        "rag_context":    contexto_rag[:500] if contexto_rag else "",
        "crag_score":     crag_score,
    }