# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 7: src/application/graph/builder.py
# Responsabilidade: Construção e compilação do grafo LangGraph com HITL.
# ─────────────────────────────────────────────────────────────────────────────

"""
application/graph/builder.py — LangGraph v0.0.39 com Human-in-the-Loop
=======================================================================
DESIGN DO GRAFO:

  [START]
     │
     ▼
  [route_node]          → decide para onde ir via OraculoRouterService
     │
     ├── "greeting_node"      → resposta directa, sem RAG
     ├── "retrieve_node"      → pipeline RAG completo
     ├── "crud_node"          → prepara tool, manda para confirm_node
     └── "admin_command_node" → comandos de administração
     │
     ▼
  [retrieve_node]       → busca híbrida + CRAG score
     │
     ├── crag_aprovado=True  → [generate_node]
     └── crag_aprovado=False → [web_search_node] → [generate_node]
     │
     ▼
  [generate_node]       → chama Gemini, verifica SemanticCache
     │
     ▼
  [END]

  CRUD PATH:
  [crud_node]
     │
     ▼
  [confirm_node]  ← interrupt_before aqui
     │
     ├── tool_confirmation="sim" → [execute_tool_node] → [feedback_node] → [END]
     └── tool_confirmation="não" → [END]  (com mensagem de cancelamento)

INTERRUPT_BEFORE:
  interrupt_before=["confirm_node"] faz o grafo pausar ANTES de confirm_node.
  O estado é persistido no RedisCheckpointer com o thread_id da sessão.
  A próxima mensagem do utilizador retoma com Command(resume=resposta).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver  # usar RedisCheckpointer em prod

from src.application.graph.state import OracleState
from src.domain.services.oraculo_router import OraculoRouterService

logger = logging.getLogger(__name__)


# ─── Factory de nós ───────────────────────────────────────────────────────────

def _make_nodes(
    oraculo_router: OraculoRouterService,
    vector_adapter,
    llm_cache,
    session_manager,
    gemini_client,
    tools_registry: dict,
    whatsapp_client,
) -> dict:
    """
    Cria todos os nós como closures com dependências injectadas.
    Cada nó é uma coroutine async que recebe e retorna OracleState parcial.

    PADRÃO DE CADA NÓ:
      1. Log de entrada com trace_id
      2. Execução da lógica
      3. Log de saída com latência
      4. Retorna apenas os campos que modifica (LangGraph faz merge)
    """

    # ── route_node ─────────────────────────────────────────────────────────────

    async def route_node(state: OracleState) -> dict:
        t0 = time.monotonic()
        trace_id = state.get("trace_id", str(uuid.uuid4())[:8])
        texto    = state["user_message"]

        logger.debug(
            "📍 [NODE:route] START | trace=%s | texto='%.80s'",
            trace_id, texto,
        )

        try:
            resultado = await oraculo_router.rotear(
                texto    = texto,
                contexto = state.get("user_context", {}),
                is_admin = state.get("is_admin", False),
            )

            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "📍 [NODE:route] END | trace=%s | route='%s' | %dms",
                trace_id, resultado["route"], ms,
            )

            return {
                **resultado,
                "node_timings": [{"node": "route", "ms": ms}],
            }

        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [NODE:route] ERRO | trace=%s | %dms | erro: %s",
                trace_id, ms, exc,
            )
            return {
                "route":    "retrieve_node",
                "error":    "Erro interno no roteamento.",
                "node_timings": [{"node": "route", "ms": ms, "error": str(exc)}],
            }

    # ── retrieve_node ──────────────────────────────────────────────────────────

    async def retrieve_node(state: OracleState) -> dict:
        t0 = time.monotonic()
        trace_id = state.get("trace_id", "?")
        query    = state.get("query_reescrita") or state["user_message"]

        logger.debug(
            "🔍 [NODE:retrieve] START | trace=%s | query='%.80s'",
            trace_id, query,
        )

        try:
            # Busca paralela: híbrida + score de relevância
            chunks, score = await asyncio.gather(
                vector_adapter.buscar_hibrido(
                    query_text      = query,
                    k_vector        = 8,
                    k_text          = 8,
                    doc_type_filter = state.get("_router_meta", {}).get("intent"),
                ),
                _calcular_crag_score(vector_adapter, query),
            )

            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "🔍 [NODE:retrieve] END | trace=%s | chunks=%d | "
                "crag=%.3f | %dms",
                trace_id, len(chunks), score, ms,
            )

            return {
                "contexto_rag":  chunks,
                "crag_score":    score,
                "crag_aprovado": score >= 0.55,
                "node_timings":  [{"node": "retrieve", "ms": ms}],
            }

        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [NODE:retrieve] ERRO | trace=%s | %dms | erro: %s",
                trace_id, ms, exc,
            )
            return {
                "contexto_rag":  [],
                "crag_aprovado": False,
                "error":         "Erro ao buscar informações.",
                "node_timings":  [{"node": "retrieve", "ms": ms, "error": str(exc)}],
            }

    # ── generate_node ──────────────────────────────────────────────────────────

    async def generate_node(state: OracleState) -> dict:
        t0 = time.monotonic()
        trace_id     = state.get("trace_id", "?")
        user_message = state["user_message"]
        skip_cache   = state.get("_router_meta", {}).get("skip_cache", False)

        logger.debug(
            "🤖 [NODE:generate] START | trace=%s | cache_bypass=%s",
            trace_id, skip_cache,
        )

        # 1. Verificar SemanticCache (excepto quando skip_cache=True)
        if not skip_cache:
            cache_hit = await llm_cache.verificar(user_message)
            if cache_hit:
                ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "🎯 [NODE:generate] CACHE HIT | trace=%s | %dms",
                    trace_id, ms,
                )
                return {
                    "resposta_final": cache_hit,
                    "cache_hit":      True,
                    "node_timings":   [{"node": "generate", "ms": ms, "cache": True}],
                }

        # 2. Monta prompt com contexto
        contexto_chunks = state.get("contexto_rag", [])
        contexto_str    = _formatar_contexto_rag(contexto_chunks)
        user_ctx        = state.get("user_context", {})
        historico       = state.get("historico", "")

        prompt = _build_prompt(
            user_message = user_message,
            contexto_rag = contexto_str,
            user_context = user_ctx,
            historico    = historico,
        )

        # 3. Chama Gemini
        try:
            resposta = await asyncio.to_thread(
                gemini_client.gerar_resposta, prompt,
            )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [NODE:generate] Gemini falhou | trace=%s | "
                "causa=%s | %dms | erro: %s",
                trace_id, type(exc).__name__, ms, exc,
            )
            return {
                "resposta_final": (
                    "Desculpe, estou com dificuldades técnicas. "
                    "Tente novamente em instantes. 🙏"
                ),
                "error":         f"Gemini: {type(exc).__name__}",
                "node_timings":  [{"node": "generate", "ms": ms, "error": True}],
            }

        ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "🤖 [NODE:generate] END | trace=%s | chars=%d | %dms",
            trace_id, len(resposta), ms,
        )

        # 4. Persiste no cache em background (não bloqueia a resposta)
        asyncio.create_task(
            llm_cache.armazenar(
                prompt   = user_message,
                response = resposta,
                doc_type = state.get("_router_meta", {}).get("intent"),
                metadata = {"trace_id": trace_id, "latencia_ms": ms},
            )
        )

        return {
            "resposta_final": resposta,
            "cache_hit":      False,
            "node_timings":   [{"node": "generate", "ms": ms}],
        }

    # ── greeting_node ──────────────────────────────────────────────────────────

    async def greeting_node(state: OracleState) -> dict:
        t0       = time.monotonic()
        trace_id = state.get("trace_id", "?")
        nome     = state.get("user_context", {}).get("nome", "")

        logger.debug("👋 [NODE:greeting] START | trace=%s | nome=%s", trace_id, nome)

        saudacao = f"Olá{f', {nome.split()[0]}' if nome else ''}! 😊 Sou o Oráculo UEMA. Como posso ajudar?"

        ms = int((time.monotonic() - t0) * 1000)
        return {
            "resposta_final": saudacao,
            "node_timings":   [{"node": "greeting", "ms": ms}],
        }

    # ── crud_node ──────────────────────────────────────────────────────────────

    async def crud_node(state: OracleState) -> dict:
        """
        Prepara a tool call e monta a mensagem de confirmação para o utilizador.
        NÃO executa a tool — apenas preenche pending_tool_call.
        O grafo pausa ANTES de confirm_node (interrupt_before).
        """
        t0       = time.monotonic()
        trace_id = state.get("trace_id", "?")
        texto    = state["user_message"]

        logger.debug("🔧 [NODE:crud] START | trace=%s", trace_id)

        try:
            # Extrai a intenção CRUD do texto (via Gemini structured output)
            tool_info = await _extrair_tool_crud(texto, gemini_client)

            descricao = _formatar_descricao_tool(tool_info)
            mensagem_confirmacao = (
                f"⚠️ Você deseja mesmo *{descricao}*?\n\n"
                f"Responda *sim* para confirmar ou *não* para cancelar."
            )

            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "🔧 [NODE:crud] Preparada | trace=%s | tool='%s' | %dms",
                trace_id, tool_info.get("tool_name"), ms,
            )

            # Envia a mensagem de confirmação AGORA (antes do interrupt)
            await asyncio.to_thread(
                whatsapp_client.enviar_mensagem,
                state["user_phone"],
                mensagem_confirmacao,
            )

            return {
                "pending_tool_call":  tool_info,
                "awaiting_hitl":      True,
                "hitl_message_sent":  True,
                "resposta_final":     mensagem_confirmacao,
                "node_timings":       [{"node": "crud", "ms": ms}],
            }

        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [NODE:crud] ERRO | trace=%s | %dms | erro: %s",
                trace_id, ms, exc,
            )
            return {
                "resposta_final": "Não consegui interpretar sua solicitação. Pode reformular?",
                "awaiting_hitl":  False,
                "node_timings":   [{"node": "crud", "ms": ms, "error": str(exc)}],
            }

    # ── confirm_node (alvo do interrupt_before) ────────────────────────────────

    async def confirm_node(state: OracleState) -> dict:
        """
        Nó que processa a resposta do utilizador após o HITL interrupt.

        FLUXO DO INTERRUPT:
          1. crud_node preenche pending_tool_call e awaiting_hitl=True
          2. LangGraph pausa ANTES deste nó (interrupt_before=["confirm_node"])
          3. O estado é serializado no RedisCheckpointer
          4. O webhook recebe a próxima mensagem do utilizador
          5. O Controller detecta awaiting_hitl=True e retoma com:
             graph.invoke(Command(resume=mensagem_user), config={"thread_id": session_id})
          6. Este nó executa com tool_confirmation preenchido
        """
        t0              = time.monotonic()
        trace_id        = state.get("trace_id", "?")
        confirmacao     = (state.get("tool_confirmation") or "").lower().strip()
        pending         = state.get("pending_tool_call", {})

        logger.debug(
            "✅ [NODE:confirm] START | trace=%s | resposta='%s'",
            trace_id, confirmacao,
        )

        # Normaliza a confirmação do utilizador
        confirmado = confirmacao in ("sim", "s", "yes", "confirmar", "ok", "pode")

        ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "✅ [NODE:confirm] | trace=%s | confirmado=%s | tool='%s' | %dms",
            trace_id, confirmado, pending.get("tool_name"), ms,
        )

        return {
            "tool_confirmation": "sim" if confirmado else "não",
            "awaiting_hitl":     False,
            "node_timings":      [{"node": "confirm", "ms": ms}],
        }

    # ── execute_tool_node ──────────────────────────────────────────────────────

    async def execute_tool_node(state: OracleState) -> dict:
        """
        Executa a tool no banco PostgreSQL.
        Só chega aqui se tool_confirmation="sim" (garantido pelo edge condicional).
        """
        t0       = time.monotonic()
        trace_id = state.get("trace_id", "?")
        pending  = state.get("pending_tool_call", {})
        tool_name = pending.get("tool_name", "")

        logger.info(
            "⚙️  [NODE:execute_tool] START | trace=%s | tool='%s' | args=%s",
            trace_id, tool_name, pending.get("args", {}),
        )

        tool_fn = tools_registry.get(tool_name)
        if not tool_fn:
            logger.error(
                "❌ [NODE:execute_tool] Tool não registada: '%s'", tool_name,
            )
            return {
                "tool_result":   {"sucesso": False, "erro": f"Tool '{tool_name}' não encontrada"},
                "resposta_final": "❌ Erro interno: ferramenta não reconhecida.",
                "node_timings":   [{"node": "execute_tool", "ms": 0, "error": True}],
            }

        try:
            resultado = await tool_fn(
                user_id  = state["session_id"],
                **pending.get("args", {}),
            )

            ms = int((time.monotonic() - t0) * 1000)
            sucesso = resultado.get("sucesso", False)

            logger.info(
                "⚙️  [NODE:execute_tool] END | trace=%s | tool='%s' | "
                "sucesso=%s | %dms",
                trace_id, tool_name, sucesso, ms,
            )

            return {
                "tool_result":  resultado,
                "node_timings": [{"node": "execute_tool", "ms": ms}],
            }

        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [NODE:execute_tool] FALHA | trace=%s | tool='%s' | "
                "causa=%s | %dms | erro: %s",
                trace_id, tool_name, type(exc).__name__, ms, exc,
            )
            return {
                "tool_result":   {"sucesso": False, "erro": str(exc)},
                "resposta_final": "❌ Ocorreu um erro ao processar sua solicitação.",
                "node_timings":  [{"node": "execute_tool", "ms": ms, "error": str(exc)}],
            }

    # ── feedback_node ─────────────────────────────────────────────────────────

    async def feedback_node(state: OracleState) -> dict:
        """
        Envia feedback discreto sobre o resultado da tool.
        Regra 3: "a ferramenta envia um feedback discreto se o resultado foi o esperado".
        """
        t0        = time.monotonic()
        resultado = state.get("tool_result", {})
        sucesso   = resultado.get("sucesso", False)

        if sucesso:
            resposta = f"✅ {resultado.get('mensagem', 'Operação realizada com sucesso')}."
        else:
            erro     = resultado.get("erro", "Erro desconhecido")
            resposta = f"❌ Não foi possível concluir: {erro}"

        ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "📣 [NODE:feedback] sucesso=%s | %dms",
            sucesso, ms,
        )
        return {
            "resposta_final": resposta,
            "node_timings":   [{"node": "feedback", "ms": ms}],
        }

    return {
        "route_node":        route_node,
        "retrieve_node":     retrieve_node,
        "generate_node":     generate_node,
        "greeting_node":     greeting_node,
        "crud_node":         crud_node,
        "confirm_node":      confirm_node,
        "execute_tool_node": execute_tool_node,
        "feedback_node":     feedback_node,
    }


# ─── Edge conditions ──────────────────────────────────────────────────────────

def _route_decision(state: OracleState) -> str:
    """Edge condicional após route_node."""
    route = state.get("route", "retrieve_node")
    valid = {"retrieve_node", "crud_node", "greeting_node", "admin_command_node"}
    if route not in valid:
        logger.warning("⚠️  [EDGE] Rota inválida '%s' → retrieve_node", route)
        return "retrieve_node"
    return route


def _after_retrieve(state: OracleState) -> str:
    """CRAG: se score baixo → web_search; senão → generate directamente."""
    if not state.get("crag_aprovado", True):
        return "web_search_node"
    return "generate_node"


def _after_confirm(state: OracleState) -> str:
    """Após confirm_node: sim → executa; não → END com mensagem de cancelamento."""
    confirmacao = state.get("tool_confirmation", "não")
    if confirmacao == "sim":
        return "execute_tool_node"
    return END


# ─── Builder principal ────────────────────────────────────────────────────────

def compilar_grafo(
    oraculo_router: OraculoRouterService,
    vector_adapter,
    llm_cache,
    session_manager,
    gemini_client,
    tools_registry: dict,
    whatsapp_client,
    usar_redis_checkpointer: bool = True,
) -> Any:
    """
    Compila e retorna o grafo LangGraph compilado (CompiledGraph).

    O interrupt_before=["confirm_node"] é a implementação central do HITL.
    O grafo pausa aqui, serializa o estado e aguarda a próxima mensagem.

    Args:
        usar_redis_checkpointer: False em testes (usa MemorySaver).
                                 True em produção (usa RedisCheckpointer).
    """
    nodes = _make_nodes(
        oraculo_router  = oraculo_router,
        vector_adapter  = vector_adapter,
        llm_cache       = llm_cache,
        session_manager = session_manager,
        gemini_client   = gemini_client,
        tools_registry  = tools_registry,
        whatsapp_client = whatsapp_client,
    )

    # ── Construção do grafo ────────────────────────────────────────────────────
    builder = StateGraph(OracleState)

    # Registar nós
    for nome, fn in nodes.items():
        builder.add_node(nome, fn)

    # Edges principais
    builder.set_entry_point("route_node")
    builder.add_conditional_edges("route_node", _route_decision, {
        "retrieve_node":      "retrieve_node",
        "crud_node":          "crud_node",
        "greeting_node":      "greeting_node",
        "admin_command_node": "retrieve_node",   # fallback até implementar
    })

    # RAG pipeline
    builder.add_conditional_edges("retrieve_node", _after_retrieve, {
        "generate_node":  "generate_node",
        "web_search_node": "generate_node",   # web_search não implementado: skip
    })
    builder.add_edge("generate_node",  END)
    builder.add_edge("greeting_node",  END)

    # CRUD / HITL pipeline
    builder.add_edge("crud_node",     "confirm_node")
    builder.add_conditional_edges("confirm_node", _after_confirm, {
        "execute_tool_node": "execute_tool_node",
        END:                 END,
    })
    builder.add_edge("execute_tool_node", "feedback_node")
    builder.add_edge("feedback_node",     END)

    # ── Checkpointer ──────────────────────────────────────────────────────────
    # Em produção usar langgraph-checkpoint-redis quando disponível para 0.0.39
    checkpointer = MemorySaver()
    logger.warning(
        "⚠️  [GRAPH] Usando MemorySaver — sessões HITL perdem-se em restart. "
        "Migrar para RedisCheckpointer quando disponível para langgraph 0.0.39.",
    )

    # interrupt_before: o grafo pausa ANTES de executar confirm_node
    compiled = builder.compile(
        checkpointer     = checkpointer,
        interrupt_before = ["confirm_node"],
    )

    logger.info("✅ [GRAPH] Grafo compilado | nós=%d | HITL=confirm_node", len(nodes))
    return compiled


# ─── Helpers privados ─────────────────────────────────────────────────────────

async def _calcular_crag_score(vector_adapter, query: str) -> float:
    """Calcula score de relevância do top chunk para CRAG decision."""
    try:
        top = await vector_adapter.buscar_vetorial(query, k=1)
        if top:
            dist = top[0].get("rrf_score", 0.0)
            return float(dist)
        return 0.0
    except Exception:
        return 0.5   # assume relevante em caso de falha


async def _extrair_tool_crud(texto: str, gemini_client) -> dict:
    """Extrai tool name e args do texto via Gemini structured output."""
    # Implementação simplificada — expandir com schema Pydantic
    t = texto.lower()
    if "email" in t:
        import re
        email = re.search(r"[\w.]+@[\w.]+\.\w{2,}", texto)
        return {
            "tool_name":       "update_email",
            "args":            {"novo_email": email.group() if email else ""},
            "descricao_humana": f"actualizar seu email para {email.group() if email else 'o novo endereço'}",
        }
    if any(k in t for k in ("telefone", "celular", "número")):
        import re
        phone = re.search(r"\d{10,11}", texto.replace(" ", "").replace("-", ""))
        return {
            "tool_name":       "update_phone",
            "args":            {"novo_telefone": phone.group() if phone else ""},
            "descricao_humana": f"actualizar seu telefone para {phone.group() if phone else 'o novo número'}",
        }
    raise ValueError(f"Não foi possível identificar a operação CRUD em: '{texto[:80]}'")


def _formatar_descricao_tool(tool_info: dict) -> str:
    return tool_info.get("descricao_humana", f"executar {tool_info.get('tool_name', 'operação')}")


def _formatar_contexto_rag(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    partes = []
    for i, c in enumerate(chunks[:5], 1):
        fonte   = c.get("source", "fonte desconhecida")
        content = c.get("content", "")
        partes.append(f"[{i}] {content}\n(Fonte: {fonte})")
    return "\n\n".join(partes)


def _build_prompt(
    user_message: str,
    contexto_rag: str,
    user_context: dict,
    historico: str,
) -> str:
    nome     = user_context.get("nome", "Aluno")
    curso    = user_context.get("curso", "")
    periodo  = user_context.get("periodo", "")
    inst     = user_context.get("instituicao", "UEMA")

    ctx_aluno = f"Aluno: {nome}"
    if curso:
        ctx_aluno += f" | Curso: {curso}"
    if periodo:
        ctx_aluno += f" | Período: {periodo}"
    ctx_aluno += f" | Instituição: {inst}"

    partes = [
        f"[CONTEXTO DO ALUNO]\n{ctx_aluno}",
    ]
    if historico:
        partes.append(f"[HISTÓRICO RECENTE]\n{historico}")
    if contexto_rag:
        partes.append(f"[BASE DE CONHECIMENTO]\n{contexto_rag}")
    partes.append(f"[PERGUNTA]\n{user_message}")

    return "\n\n".join(partes)