"""
Regressão do funil de ticket (HITL) do experimento LangGraph
(langgraph_experiment/). Roda com MemorySaver — deliberadamente NÃO usa
AsyncRedisSaver — pra isolar a lógica dos nodes do bug conhecido do
checkpointer Redis com múltiplos interrupts pendentes (ver
langgraph_experiment/nodes.py e .claude.md). Se este teste passar mas o
funil real (Redis) quebrar, o problema é do checkpointer, não dos nodes.
"""
from __future__ import annotations

import pytest
from langgraph.types import Command

from langgraph_experiment.graph import build_graph


def _pergunta(result: dict) -> str:
    return result["__interrupt__"][0].value["question"]


@pytest.mark.asyncio
async def test_funil_ticket_completo_com_respostas_validas():
    app = build_graph()
    config = {"configurable": {"thread_id": "test_ticket_ok"}}

    r = await app.ainvoke(
        {"session_id": "test_ticket_ok", "message": "abrir um chamado", "route": "ticket"},
        config=config,
    )
    assert "Incidente" in _pergunta(r) and "Requisição" in _pergunta(r)

    r = await app.ainvoke(Command(resume="2"), config=config)
    assert "categoria" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="4"), config=config)
    assert "descreva" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="Preciso de acesso ao SIGAA"), config=config)
    assert "confirma" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="sim"), config=config)
    assert "__interrupt__" not in r or not r["__interrupt__"]
    assert "Ticket de teste registrado" in r["answer"]
    assert "Requisicao" in r["answer"]
    assert "Acesso e Conta" in r["answer"]


@pytest.mark.asyncio
async def test_funil_ticket_reprergunta_em_resposta_invalida():
    app = build_graph()
    config = {"configurable": {"thread_id": "test_ticket_invalido"}}

    await app.ainvoke(
        {"session_id": "test_ticket_invalido", "message": "abrir chamado", "route": "ticket"},
        config=config,
    )

    # Resposta livre em vez de "1"/"2" — não pode avançar pra pergunta de categoria.
    r = await app.ainvoke(Command(resume="Incidente"), config=config)
    pergunta = _pergunta(r)
    assert "inválida" in pergunta.lower()
    assert "Incidente" in pergunta and "Requisição" in pergunta  # re-perguntou a MESMA questão

    # Agora responde certo — avança normalmente.
    r = await app.ainvoke(Command(resume="1"), config=config)
    assert "categoria" in _pergunta(r).lower()

    # Categoria inválida (fora da lista) — re-pergunta em vez de aceitar.
    r = await app.ainvoke(Command(resume="99"), config=config)
    pergunta = _pergunta(r)
    assert "válido" in pergunta.lower() or "valido" in pergunta.lower()
    assert "categoria" in pergunta.lower()


@pytest.mark.asyncio
async def test_funil_ticket_cancelamento():
    app = build_graph()
    config = {"configurable": {"thread_id": "test_ticket_cancela"}}

    await app.ainvoke(
        {"session_id": "test_ticket_cancela", "message": "abrir chamado", "route": "ticket"},
        config=config,
    )
    await app.ainvoke(Command(resume="1"), config=config)
    await app.ainvoke(Command(resume="1"), config=config)
    r = await app.ainvoke(Command(resume="teste de cancelamento"), config=config)
    assert "confirma" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="não"), config=config)
    assert "__interrupt__" not in r or not r["__interrupt__"]
    assert r["answer"] == "❌ Ticket cancelado."
