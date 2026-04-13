"""
src/application/graph/nodes/core.py — Padrão Ouro Agentic RAG
"""
from __future__ import annotations
import logging
from functools import lru_cache
from typing import TYPE_CHECKING
from langchain_core.messages import HumanMessage, AIMessage

if TYPE_CHECKING:
    from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

CRAG_THRESHOLD     = 0.30   # abaixo → relevance = "no"
MAX_REWRITE_LOOPS  = 2       # evita loop infinito

def _get_llm():
    from src.infrastructure.adapters.gemini_provider import GeminiProvider
    return GeminiProvider()

@lru_cache(maxsize=1)
def _get_retriever():
    from src.application.use_cases.retrieve_context_use_case import RetrieveContextUseCase
    from src.infrastructure.adapters.redis_vector_adapter import RedisVectorAdapter
    return RetrieveContextUseCase(RedisVectorAdapter())

def _system_prompt() -> str:
    try:
        from src.infrastructure.redis_client import get_redis_text
        from src.application.graph.prompts import SYSTEM_UEMA
        val = get_redis_text().get("admin:system_prompt")
        if val:
            return val.decode() if isinstance(val, bytes) else val
        return SYSTEM_UEMA
    except Exception:
        from src.application.graph.prompts import SYSTEM_UEMA
        return SYSTEM_UEMA


class OraculoCoreNodes:
    """
    Nós do Agentic RAG agrupados em classe para injeção limpa do router.
    Resolve o bug do LangGraph ao permitir injeção de dependência nativa.
    """

    def __init__(self, oraculo_router):
        self._router = oraculo_router

    # ── NÓ 1: Classify ───────────────────────────────────────────────────────

    async def node_classify(self, state: "OracleState") -> dict:
        """
        Porteiro do grafo: decide a rota sem tocar em LLM sempre que possível.
        """
        msgs = state.get("messages", [])
        msg  = msgs[-1].content if msgs else state.get("current_input", "")

        is_admin = state.get("is_admin", False)

        # Retomada HITL — o usuário estava respondendo uma confirmação
        pending = state.get("pending_confirmation")
        if pending and state.get("confirmation_result") not in (
            "confirmed", "cancelled", "awaiting_token"
        ):
            lower = msg.lower().strip()
            if lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
                return {"confirmation_result": "confirmed"}
            if lower in ("não", "nao", "n", "no", "cancelar"):
                return {
                    "confirmation_result": "cancelled",
                    "final_response": "❌ Operação cancelada.",
                    "route": "respond_only",
                }
            return {
                "final_response": f"{pending}\n\nResponda *SIM* ou *NÃO*.",
                "route": "respond_only",
            }

        # Modo manutenção (admin bypassa)
        try:
            from src.infrastructure.redis_client import get_redis_text
            flag = get_redis_text().get("admin:maintenance_mode")
            if isinstance(flag, bytes): flag = flag.decode()
            if flag == "1" and not is_admin:
                return {
                    "final_response": "🔧 *Oráculo em manutenção.* Voltarei em breve!",
                    "route": "respond_only",
                }
        except Exception:
            pass

        # Roteamento (cascata: KNN → Pydantic/LLM)
        contexto = {
            "curso":   state.get("curso"),
            "periodo": state.get("periodo"),
            "centro":  state.get("centro"),
        }
        resultado = await self._router.rotear(msg, contexto, is_admin)
        return resultado

    # ── NÓ 2: Retrieve ───────────────────────────────────────────────────────

    async def node_retrieve(self, state: "OracleState") -> dict:
        """Busca documentos no Redis. Não gera resposta — apenas recupera."""
        msgs  = state.get("messages", [])
        query = msgs[-1].content if msgs else state.get("current_input", "")
        route = state.get("route", "geral").upper()

        rag_context = ""
        crag_score  = 0.0

        try:
            from src.rag.query.transformer import QueryTransformer
            from src.rag.query.protocols import RawQuery

            raw = RawQuery(text=query, fatos_usuario=[])
            transformer = QueryTransformer.build_for_route(route)
            qt = transformer.transform(raw)

            retriever = _get_retriever()
            resultado = await retriever.executar(qt)

            if resultado.encontrou:
                rag_context = resultado.contexto_formatado
                scores = [
                    c.rrf_score for c in resultado.chunks if c.rrf_score > 0
                ]
                crag_score = sum(scores) / len(scores) if scores else 0.0

        except Exception as e:
            logger.warning("⚠️  node_retrieve falhou: %s", e)

        return {"rag_context": rag_context, "crag_score": crag_score}

    # ── NÓ 3: Grade Documents ────────────────────────────────────────────────

    async def node_grade_documents(self, state: "OracleState") -> dict:
        """
        CRAG: avalia a qualidade do retrieval.
        Se baixo E ainda temos tentativas → relevance = "no" → rewrite.
        """
        score      = state.get("crag_score", 0.0)
        loop_count = state.get("loop_count", 0)

        if score < CRAG_THRESHOLD and loop_count < MAX_REWRITE_LOOPS:
            logger.info(
                "📉 CRAG score baixo (%.3f) — reescrevendo query (loop %d/%d)",
                score, loop_count + 1, MAX_REWRITE_LOOPS,
            )
            return {"relevance": "no"}

        return {"relevance": "yes"}

    # ── NÓ 4: Rewrite Query ──────────────────────────────────────────────────

    async def node_rewrite_query(self, state: "OracleState") -> dict:
        """
        Self-RAG: pede ao LLM uma versão melhorada da query para nova busca.
        """
        msgs         = state.get("messages", [])
        query_orig   = msgs[-1].content if msgs else state.get("current_input", "")
        loop_count   = state.get("loop_count", 0)

        prompt_rewrite = (
            f"A busca pela pergunta abaixo não encontrou documentos relevantes.\n"
            f"Reescreva-a de forma mais técnica e específica para busca acadêmica UEMA.\n"
            f"Responda APENAS com a pergunta reescrita, sem explicações.\n\n"
            f"Pergunta original: {query_orig}"
        )

        nova_query = query_orig  # fallback
        try:
            llm  = _get_llm()
            resp = await llm.gerar_resposta_async(
                prompt=prompt_rewrite,
                temperatura=0.1,
                max_tokens=150,
            )
            if resp.sucesso and resp.conteudo.strip():
                nova_query = resp.conteudo.strip()
                logger.info("🔄 Query reescrita: '%s' → '%s'", query_orig[:50], nova_query[:50])
        except Exception as e:
            logger.warning("⚠️  node_rewrite_query LLM falhou: %s", e)

        return {
            "messages":   [HumanMessage(content=nova_query)],
            "loop_count": loop_count + 1,
        }

    # ── NÓ 5: Generate ───────────────────────────────────────────────────────

    async def node_generate(self, state: "OracleState") -> dict:
        """
        Escritor final: monta prompt com contexto RAG e gera a resposta.
        """
        from src.application.graph.prompts import montar_prompt_geracao

        msgs = state.get("messages", [])
        query = msgs[-1].content if msgs else state.get("current_input", "")
        rag_context = state.get("rag_context", "")

        perfil = ""
        if state.get("user_name") or state.get("curso"):
            perfil = f"Aluno: {state.get('user_name', '')} | Curso: {state.get('curso', '')}"

        prompt = montar_prompt_geracao(
            pergunta=query,
            contexto_rag=rag_context,
            perfil_usuario=perfil,
        )

        resposta_padrao = "Desculpe, tive dificuldade em formular a resposta."
        
        try:
            llm = _get_llm()
            resp = await llm.gerar_resposta_async(
                prompt=prompt,
                system_instruction=_system_prompt(),
                temperatura=0.2,
            )
            
            if resp.sucesso and resp.conteudo:
                return {
                    "messages": [AIMessage(content=resp.conteudo)],
                    "final_response": resp.conteudo,
                }
            
            return {
                "messages": [AIMessage(content=resposta_padrao)],
                "final_response": resposta_padrao,
            }

        except Exception as e:
            logger.error(f"🚨 Erro no node_generate: {e}")
            return {
                "messages": [AIMessage(content=resposta_padrao)],
                "final_response": resposta_padrao,
            }