from __future__ import annotations

import re

from langgraph.types import interrupt

from langgraph_experiment.state import OraculoState

# Mesmo nível de heurística do L1 (regex) do Supervisor real
# (src/router/supervisor.py) — versão reduzida só para rotear entre os dois
# nodes deste experimento, não uma réplica das 5 camadas.
_RE_TICKET = re.compile(
    r"\b(ticket|chamado|abrir\s+chamado|problema\s+t[eé]cnico|suporte\s+t[eé]cnico)\b",
    re.I,
)


def classify_node(state: OraculoState) -> dict:
    # Se quem chamou o grafo já decidiu a rota (ex: dispatcher_langgraph.py,
    # que reaproveita o Supervisor real), respeita — só cai no regex quando
    # invocado direto (ex: run_test.py, teste manual via CLI).
    if state.route in ("rag", "ticket"):
        return {}
    route = "ticket" if _RE_TICKET.search(state.message) else "rag"
    return {"route": route}


async def rag_node(state: OraculoState) -> dict:
    """Reaproveita RAGSearchService/SynthesisService reais — nenhuma lógica
    de busca/síntese duplicada, só o orquestrador (LangGraph) muda."""
    from src.agents.academic_knowledge.service import RAGSearchService
    from src.agents.academic_knowledge.synthesis import SynthesisService

    rag = RAGSearchService()
    result = await rag.buscar(state.message)
    if not result.ok or not result.data.get("found"):
        return {"answer": result.message or "Não encontrei informações sobre isso nos documentos da UEMA."}

    synth = SynthesisService()
    synth_result = await synth.sintetizar(
        chunks=result.data.get("chunks", []),
        plan_ctx={"query": state.message},
    )
    return {"answer": synth_result.answer if synth_result.ok else f"[erro synthesis] {synth_result.error}"}


# ─────────────────────────────────────────────────────────────────────────────
# Funil de ticket — 1 interrupt() por node (não 4 empilhados no mesmo node).
#
# Motivo (ver plano/investigação): múltiplos interrupt() sequenciais no mesmo
# node é um padrão documentado como válido pelo LangGraph, mas a implementação
# real do checkpointer usado aqui (AsyncRedisSaver, pacote
# langgraph-checkpoint-redis) tem bugs abertos conhecidos especificamente na
# resumption de múltiplos interrupts pendentes:
#   - https://github.com/langchain-ai/langgraph/issues/5074
#   - https://github.com/redis-developer/langgraph-redis/issues/133
# Reproduzido nesta branch: funil de 4 interrupts no mesmo node funcionava no
# 1º resume e quebrava no 2º (o `aget_state().next` voltava vazio como se o
# grafo tivesse terminado). Quebrar em 1 node por pergunta reduz a
# dependência à trilha mais simples/testada do pacote (exatamente 1 interrupt
# pendente por vez) sem trocar de checkpointer.
#
# Validação: cada node valida a resposta e, se inválida, a edge condicional
# correspondente volta pro mesmo node (novo interrupt(), com a mensagem de
# erro prefixada) em vez de aceitar qualquer texto silenciosamente — bug
# separado achado na versão anterior (responder "Incidente" por extenso virava
# "Requisicao" sem avisar ninguém).
# ─────────────────────────────────────────────────────────────────────────────


def _com_erro(pergunta: str, erro: str) -> str:
    return f"{erro}\n\n{pergunta}" if erro else pergunta


async def ticket_ask_tipo(state: OraculoState) -> dict:
    pergunta = _com_erro(
        "É um *Incidente* (algo parou) ou uma *Requisição* (pedido novo)? Responda 1 ou 2.",
        state.ticket_error,
    )
    resposta = interrupt({"question": pergunta})
    texto = str(resposta).strip()
    if texto == "1":
        return {"ticket_data": {**state.ticket_data, "tipo": "Incidente"}, "ticket_error": ""}
    if texto == "2":
        return {"ticket_data": {**state.ticket_data, "tipo": "Requisicao"}, "ticket_error": ""}
    return {"ticket_error": "❌ Resposta inválida — responda apenas 1 ou 2."}


def _tipo_valido(state: OraculoState) -> str:
    return "ticket_ask_categoria" if state.ticket_data.get("tipo") else "ticket_ask_tipo"


async def ticket_ask_categoria(state: OraculoState) -> dict:
    from src.agents.tickets.ticket_flow import SEED_CATEGORIAS

    categoria_por_id = {c["id"]: c["nome"] for c in SEED_CATEGORIAS}
    lista = "\n".join(f"{c['id']}. {c['nome']}" for c in SEED_CATEGORIAS)
    pergunta = _com_erro(f"Qual categoria melhor descreve o problema?\n{lista}", state.ticket_error)
    resposta = interrupt({"question": pergunta})
    texto = str(resposta).strip()
    if texto.isdigit() and int(texto) in categoria_por_id:
        nome = categoria_por_id[int(texto)]
        return {"ticket_data": {**state.ticket_data, "categoria": nome}, "ticket_error": ""}
    return {"ticket_error": "❌ Escolha um número válido da lista acima."}


def _categoria_valida(state: OraculoState) -> str:
    return "ticket_ask_queixa" if state.ticket_data.get("categoria") else "ticket_ask_categoria"


async def ticket_ask_queixa(state: OraculoState) -> dict:
    pergunta = _com_erro("Descreva o problema ou pedido com suas palavras:", state.ticket_error)
    resposta = interrupt({"question": pergunta})
    texto = str(resposta).strip()
    if len(texto) < 3:
        return {"ticket_error": "❌ Descreva com um pouco mais de detalhe (mínimo 3 caracteres)."}
    return {"ticket_data": {**state.ticket_data, "queixa": texto}, "ticket_error": ""}


def _queixa_valida(state: OraculoState) -> str:
    return "ticket_confirm" if state.ticket_data.get("queixa") else "ticket_ask_queixa"


async def ticket_confirm(state: OraculoState) -> dict:
    d = state.ticket_data
    resumo = f"Tipo: {d.get('tipo')}\nCategoria: {d.get('categoria')}\nDescrição: {d.get('queixa')}"
    pergunta = _com_erro(f"{resumo}\n\nConfirma o envio? (sim/não)", state.ticket_error)
    resposta = interrupt({"question": pergunta})
    texto = str(resposta).strip().lower()
    if texto in ("sim", "s", "confirmo"):
        return {"ticket_confirmed": True, "ticket_error": ""}
    if texto in ("não", "nao", "n"):
        return {"ticket_confirmed": False, "ticket_error": "", "answer": "❌ Ticket cancelado."}
    return {"ticket_confirmed": None, "ticket_error": "❌ Responda apenas sim ou não."}


def _confirm_route(state: OraculoState) -> str:
    if state.ticket_confirmed is True:
        return "ticket_save"
    if state.ticket_confirmed is False:
        return "__end__"
    return "ticket_confirm"


async def ticket_save(state: OraculoState) -> dict:
    """Só efeito colateral (grava o ticket) — separado do node de confirmação
    (ticket_confirm) pra manter idempotência: se o checkpoint falhar depois
    daqui, o próximo resume não repete a pergunta de confirmação."""
    from src.capabilities.persistence.dev_dump import salvar_json_dev

    d = state.ticket_data
    resumo = f"Tipo: {d.get('tipo')}\nCategoria: {d.get('categoria')}\nDescrição: {d.get('queixa')}"
    caminho = salvar_json_dev("tickets_dev_langgraph", state.session_id, d)
    return {"answer": f"✅ Ticket de teste registrado (LangGraph)! Salvo em {caminho}\n\n{resumo}"}
