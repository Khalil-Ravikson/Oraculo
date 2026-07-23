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
    if state.get("route") in ("rag", "ticket"):
        return {}
    route = "ticket" if _RE_TICKET.search(state["message"]) else "rag"
    return {"route": route}


async def rag_node(state: OraculoState) -> dict:
    """Reaproveita RAGSearchService/SynthesisService reais — nenhuma lógica
    de busca/síntese duplicada, só o orquestrador (LangGraph) muda."""
    from src.agents.academic_knowledge.service import RAGSearchService
    from src.agents.academic_knowledge.synthesis import SynthesisService

    rag = RAGSearchService()
    result = await rag.buscar(state["message"])
    if not result.ok or not result.data.get("found"):
        return {"answer": result.message or "Não encontrei informações sobre isso nos documentos da UEMA."}

    synth = SynthesisService()
    synth_result = await synth.sintetizar(
        chunks=result.data.get("chunks", []),
        plan_ctx={"query": state["message"]},
    )
    return {"answer": synth_result.answer if synth_result.ok else f"[erro synthesis] {synth_result.error}"}


async def ticket_node(state: OraculoState) -> dict:
    """
    Funil de ticket reduzido (tipo -> categoria -> queixa -> confirmação),
    usando `interrupt()` do LangGraph para HITL multi-turn em vez da state
    machine manual em Redis (`agents/tickets/ticket_flow.py`).

    Reaproveita a lista real de categorias (SEED_CATEGORIAS) e o mesmo
    capability de persistência de teste (`dev_dump.salvar_json_dev`) do
    fluxo de produção — só o mecanismo de pausar/retomar a conversa muda.
    """
    from src.agents.tickets.ticket_flow import SEED_CATEGORIAS
    from src.capabilities.persistence.dev_dump import salvar_json_dev

    data: dict = {}

    tipo_raw = interrupt({"question": "É um *Incidente* (algo parou) ou uma *Requisição* (pedido novo)? Responda 1 ou 2."})
    data["tipo"] = "Incidente" if str(tipo_raw).strip() == "1" else "Requisicao"

    lista = "\n".join(f"{c['id']}. {c['nome']}" for c in SEED_CATEGORIAS)
    cat_raw = interrupt({"question": f"Qual categoria melhor descreve o problema?\n{lista}"})
    cat_id = int(str(cat_raw).strip()) if str(cat_raw).strip().isdigit() else 0
    categoria = next((c["nome"] for c in SEED_CATEGORIAS if c["id"] == cat_id), "Outros")
    data["categoria"] = categoria

    queixa = interrupt({"question": "Descreva o problema ou pedido com suas palavras:"})
    data["queixa"] = str(queixa).strip()

    resumo = f"Tipo: {data['tipo']}\nCategoria: {data['categoria']}\nDescrição: {data['queixa']}"
    confirm = interrupt({"question": f"{resumo}\n\nConfirma o envio? (sim/não)"})

    if str(confirm).strip().lower() in ("sim", "s", "confirmo"):
        caminho = salvar_json_dev("tickets_dev_langgraph", state["session_id"], data)
        return {"answer": f"✅ Ticket de teste registrado (LangGraph)! Salvo em {caminho}\n\n{resumo}", "ticket_data": data}

    return {"answer": "❌ Ticket cancelado.", "ticket_data": data}
