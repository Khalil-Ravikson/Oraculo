# src/application/graph/nodes.py
"""
Cada nó é uma função pura async que recebe o state e retorna um dict
com apenas os campos que foram modificados.
"""
from __future__ import annotations
import logging
import re

from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

# ── Nó 0: Classify ────────────────────────────────────────────────────────

async def node_classify(state: OracleState) -> dict:
    """
    Guardrails + routing. Zero chamadas ao LLM.
    Usa o SemanticRouter existente (Redis KNN).
    """
    msg = state["current_input"].strip()

    # Retomada de confirmação HITL: usuário respondeu "sim" ou "não"
    if state.get("pending_confirmation"):
        answer = msg.lower()
        if answer in ("sim", "s", "yes", "y", "confirmo", "pode", "ok"):
            return {"confirmation_result": "confirmed"}
        if answer in ("não", "nao", "n", "no", "cancelar", "cancela"):
            return {
                "confirmation_result": "cancelled",
                "final_response": "❌ Operação cancelada. Como mais posso ajudar?",
                "route": "respond_only",
            }
        # Resposta ambígua: repergunta
        return {
            "final_response": (
                f"{state['pending_confirmation']}\n\n"
                "Responda *SIM* para confirmar ou *NÃO* para cancelar."
            ),
            "route": "respond_only",
        }

    # Roteamento normal
    from src.domain.semantic_router import rotear
    from src.domain.entities import EstadoMenu

    resultado = rotear(msg, EstadoMenu.MAIN)
    route     = _resolver_rota(msg, resultado)

    return {
        "route":     route,
        "tool_name": resultado.tool_name,
        "tool_args": None,
    }


def _resolver_rota(msg: str, resultado) -> str:
    """Decide entre 'rag', 'crud', 'greeting'."""
    _CRUD_INTENT = re.compile(
        r"(atualiz|mudar|alterar|corrig|trocar).{0,30}"
        r"(nome|email|telefone|matrícula|curso|dados)",
        re.IGNORECASE,
    )
    if _CRUD_INTENT.search(msg):
        return "crud"
    if resultado.rota.value == "GERAL" and len(msg.split()) <= 4:
        return "greeting"
    return "rag"


# ── Nó 1: RAG ─────────────────────────────────────────────────────────────

async def node_rag(state: OracleState) -> dict:
    """
    Executa o pipeline RAG existente.
    Reaproveita TODO o código do core.py atual sem reescrever nada.
    """
    from src.rag.query_transform import transformar_query
    from src.rag.hybrid_retriever import recuperar
    from src.domain.entities import EstadoMenu
    from src.domain.semantic_router import rotear
    from src.memory.long_term_memory import buscar_fatos_relevantes, fatos_como_string
    from src.memory.working_memory import get_historico_compactado
    from src.providers.gemini_provider import chamar_gemini, SYSTEM_UEMA
    from src.agent.prompts import montar_prompt_geracao

    user_id   = state["user_id"]
    session_id = state["user_phone"]
    mensagem   = state["current_input"]

    # Reutiliza pipeline existente
    resultado_routing = rotear(mensagem, EstadoMenu.MAIN)
    fatos     = buscar_fatos_relevantes(user_id=user_id, pergunta=mensagem)
    historico = get_historico_compactado(session_id)

    qt = transformar_query(mensagem, fatos_usuario=fatos)
    recuperacao = recuperar(qt)

    # Injeta contexto do aluno no prompt (Regra 2 — silencioso)
    ctx = state.get("user_context", {})
    contexto_usuario = (
        f"Aluno: {state['user_name']} | "
        f"Curso: {ctx.get('curso', '?')} | "
        f"Período: {ctx.get('periodo', '?')}"
        if ctx else ""
    )

    prompt = montar_prompt_geracao(
        pergunta      = mensagem,
        contexto_rag  = recuperacao.contexto_formatado,
        fatos_usuario = fatos_como_string(fatos),
        historico     = historico.texto_formatado,
        perfil_usuario= contexto_usuario,
    )

    resp = chamar_gemini(
        prompt=prompt,
        system_instruction=SYSTEM_UEMA,
    )

    return {
        "final_response": resp.conteudo if resp.sucesso else "Desculpe, tive um problema técnico.",
        "rag_context":    recuperacao.contexto_formatado,
        "crag_score":     0.0,
    }


# ── Nó 2: Ask Confirm (HITL) ──────────────────────────────────────────────

async def node_ask_confirm(state: OracleState) -> dict:
    """
    Formula a pergunta de confirmação e PAUSA.
    O interrupt_before="exec_tool" faz a pausa acontecer automaticamente
    antes do próximo nó — este nó só prepara a mensagem.
    """
    tool_name = state.get("tool_name", "")
    tool_args = state.get("tool_args", {})

    pergunta = _formatar_confirmacao(tool_name, tool_args, state["user_name"])

    return {
        "pending_confirmation": pergunta,
        "confirmation_result":  "pending",
        "final_response":       pergunta,
    }


def _formatar_confirmacao(tool_name: str, args: dict, nome: str) -> str:
    templates = {
        "update_student_email": (
            "📧 {nome}, deseja mesmo alterar seu e-mail para:\n"
            "*{novo_valor}*?\n\nResponda *SIM* para confirmar ou *NÃO* para cancelar."
        ),
        "update_student_curso": (
            "📚 {nome}, deseja mesmo alterar seu curso para:\n"
            "*{novo_valor}*?\n\nResponda *SIM* para confirmar ou *NÃO* para cancelar."
        ),
    }
    tmpl = templates.get(
        tool_name,
        "✏️ {nome}, deseja confirmar a alteração?\n\n"
        "Dados: *{novo_valor}*\n\nResponda *SIM* ou *NÃO*.",
    )
    return tmpl.format(nome=nome, novo_valor=args.get("novo_valor", "?"))


# ── Nó 3: Exec Tool (pós-confirmação) ─────────────────────────────────────

async def node_exec_tool(state: OracleState) -> dict:
    """
    Executa a tool CRUD no banco.
    Só chega aqui se confirmation_result == 'confirmed'.
    """
    from src.tools.crud_tools import executar_tool

    tool_name = state["tool_name"]
    tool_args = {**(state.get("tool_args") or {}), "user_id": state["user_id"]}

    try:
        resultado = await executar_tool(tool_name, tool_args)
        feedback  = f"✅ Feito! {resultado.get('mensagem', 'Dados atualizados com sucesso.')}"
    except Exception as e:
        logger.exception("❌ Erro ao executar tool %s: %s", tool_name, e)
        feedback = "❌ Ocorreu um erro técnico. Tente novamente ou contate o suporte."

    return {
        "final_response":       feedback,
        "pending_confirmation": None,
        "confirmation_result":  None,
    }


# ── Nó 4: Greeting ────────────────────────────────────────────────────────

async def node_greeting(state: OracleState) -> dict:
    nome = state["user_name"].split()[0]
    ctx  = state.get("user_context", {})

    resp = (
        f"Olá, {nome}! 👋 Sou o *Oráculo UEMA*.\n\n"
        f"Posso ajudar com:\n"
        f"📅 Calendário e prazos\n"
        f"📋 Edital PAES 2026\n"
        f"📞 Contatos da universidade\n"
        f"💻 Suporte técnico (CTIC)\n\n"
        f"O que você precisa hoje?"
    )
    if ctx.get("curso"):
        resp = f"Olá, {nome}! ({ctx['curso']}) 👋\n" + resp

    return {"final_response": resp}


# ── Nó 5: Respond ─────────────────────────────────────────────────────────

async def node_respond(state: OracleState) -> dict:
    """
    Nó terminal: persiste na memória e retorna.
    O envio real acontece na task Celery após o grafo terminar.
    """
    from src.memory.working_memory import adicionar_mensagem

    session_id = state["user_phone"]
    resposta   = state.get("final_response") or "Não consegui processar sua solicitação."

    adicionar_mensagem(session_id, "user",      state["current_input"])
    adicionar_mensagem(session_id, "assistant", resposta)

    return {"final_response": resposta}