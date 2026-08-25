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
import src.application.runtime.dispatcher_langgraph as dlg
from src.router.contracts import RouterDecision


def _pergunta(result: dict) -> str:
    return result["__interrupt__"][0].value["question"]


def _fake_decision(rota: str) -> RouterDecision:
    return RouterDecision(
        rota=rota, confianca=1.0, motivo="teste", cache_hit=False,
        cache_layer="miss", latencia_ms=0, dag_hint={},
    )


@pytest.fixture(autouse=True)
def _sem_postgres_real(monkeypatch):
    """Isola os testes do funil de ticket da dependência de Postgres real.

    rbac.checar_permissao_chamado() (chamado por ticket_flow.py em cada passo
    do funil) chama buscar_pessoa_por_telefone(), que abre uma conexão de
    verdade ao banco — sem Postgres no ambiente de teste, isso derruba os
    testes com OSError de conexão recusada, não uma falha de lógica do funil.
    Nenhum teste aqui exercita RBAC/lookup em si, só o funil do grafo/
    dispatcher, então simula "sem cadastro encontrado" e liga
    DEV_TEST_SKIP_REGISTRATION pra checar_permissao_chamado sintetizar o
    usuário de teste permissivo que ela já sabe montar (rbac.py:22-31) sem
    tocar o banco.
    """
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "DEV_TEST_SKIP_REGISTRATION", True)

    async def _fake_buscar(*a, **k):
        return None

    monkeypatch.setattr(
        "src.capabilities.persistence.pessoa_lookup.buscar_pessoa_por_telefone",
        _fake_buscar,
    )


@pytest.fixture(autouse=True)
def _sem_redis_hitl_legado(monkeypatch):
    """dispatcher_langgraph.py::processar() passou a chamar
    handle_hitl_continuation (Fase 2d) antes de rotear — abre uma conexão
    real ao Redis via redis_state.get_hitl_session(). Nenhum teste deste
    arquivo cobre o HITL legado (SIGAA), só o funil nativo do grafo, então
    mocka "sem sessão pendente" sem tocar Redis de verdade."""
    async def _sem_sessao(*a, **k):
        return None

    monkeypatch.setattr(
        "src.capabilities.persistence.redis_state.get_hitl_session", _sem_sessao,
    )


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
async def test_funil_ticket_linguagem_natural():
    app = build_graph()
    config = {"configurable": {"thread_id": "test_ticket_natural"}}

    await app.ainvoke(
        {"session_id": "test_ticket_natural", "message": "abrir chamado", "route": "ticket"},
        config=config,
    )
    r = await app.ainvoke(Command(resume="É um incidente, meu pc quebrou"), config=config)
    assert "categoria" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="Hardware"), config=config)
    assert "descreva" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="Impressora não liga"), config=config)
    assert "confirma" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="pode enviar"), config=config)
    assert "__interrupt__" not in r or not r["__interrupt__"]
    assert "Hardware" in r["answer"]


@pytest.mark.asyncio
async def test_funil_ticket_reprergunta_em_resposta_invalida():
    app = build_graph()
    config = {"configurable": {"thread_id": "test_ticket_invalido"}}

    await app.ainvoke(
        {"session_id": "test_ticket_invalido", "message": "abrir chamado", "route": "ticket"},
        config=config,
    )

    # Resposta ambígua, sem nenhuma palavra-chave reconhecível.
    r = await app.ainvoke(Command(resume="não sei ao certo"), config=config)
    pergunta = _pergunta(r)
    assert "não entendi" in pergunta.lower() or "nao entendi" in pergunta.lower()
    assert "Incidente" in pergunta and "Requisição" in pergunta  # re-perguntou a MESMA questão

    # Agora responde certo — avança normalmente.
    r = await app.ainvoke(Command(resume="1"), config=config)
    assert "categoria" in _pergunta(r).lower()

    # Categoria inválida (fora da lista, sem sinônimo reconhecível) — re-pergunta.
    r = await app.ainvoke(Command(resume="99"), config=config)
    pergunta = _pergunta(r)
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

    r = await app.ainvoke(Command(resume="deixa pra lá"), config=config)
    assert "__interrupt__" not in r or not r["__interrupt__"]
    assert r["answer"] == "❌ Ticket cancelado."


# ─────────────────────────────────────────────────────────────────────────────
# Testes no nível do dispatcher (dispatcher_langgraph.processar) — cobrem o
# vazamento de estado entre execuções e o "detour" institucional, que só
# existem nesse nível (não no grafo puro). Usam MemorySaver via monkeypatch
# do singleton `_graph` (bypassa a criação do AsyncRedisSaver real) e mockam
# rotear()/responder_rag_direto pra não depender de Gemini/Postgres vivos.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_dispatcher_singleton():
    dlg._graph = None
    yield
    dlg._graph = None


@pytest.mark.asyncio
async def test_dispatcher_nao_vaza_estado_entre_tickets_na_mesma_sessao(monkeypatch):
    dlg._graph = build_graph()

    async def _rotear_ticket(*a, **k):
        return _fake_decision("TICKET_ABERTURA")

    monkeypatch.setattr("src.router.supervisor.rotear", _rotear_ticket)

    session_id = "test_dispatcher_vazamento"

    # 1º ticket, completo e confirmado com dados válidos.
    await dlg.processar("abrir chamado", session_id, {})
    await dlg.processar("2", session_id, {})
    await dlg.processar("4", session_id, {})
    await dlg.processar("sem wifi", session_id, {})
    r1 = await dlg.processar("sim", session_id, {})
    assert "Requisicao" in r1.answer

    # 2º ticket na MESMA sessão — responde tipo/categoria de propósito errado.
    await dlg.processar("abrir outro chamado", session_id, {})
    r_tipo_invalido = await dlg.processar("não sei", session_id, {})
    assert "não entendi" in r_tipo_invalido.answer.lower() or "nao entendi" in r_tipo_invalido.answer.lower()

    await dlg.processar("1", session_id, {})  # agora responde certo (Incidente)
    r_categoria_invalida = await dlg.processar("xyz", session_id, {})
    assert "categoria" in r_categoria_invalida.answer.lower()

    await dlg.processar("2", session_id, {})  # Hardware
    r_final = await dlg.processar("pc pegou fogo", session_id, {})
    assert "confirma" in r_final.answer.lower()
    r_confirma = await dlg.processar("sim", session_id, {})
    # Não pode ter herdado tipo=Requisicao/categoria=Rede do 1º ticket.
    assert "Incidente" in r_confirma.answer
    assert "Hardware" in r_confirma.answer
    assert "Rede e Conectividade" not in r_confirma.answer


@pytest.mark.asyncio
async def test_dispatcher_detour_institucional_preserva_ticket(monkeypatch):
    dlg._graph = build_graph()

    decisoes = iter([_fake_decision("TICKET_ABERTURA"), _fake_decision("GERAL")])

    async def _rotear_sequencial(*a, **k):
        return next(decisoes)

    monkeypatch.setattr("src.router.supervisor.rotear", _rotear_sequencial)

    async def _fake_rag(mensagem: str, **kwargs) -> str:
        return "A UEMA foi fundada em 1981."

    monkeypatch.setattr("langgraph_experiment.nodes.responder_rag_direto", _fake_rag)

    session_id = "test_dispatcher_detour"
    r0 = await dlg.processar("abrir chamado", session_id, {})
    assert "Incidente" in r0.answer

    # Pergunta institucional em vez de responder 1/2.
    r_detour = await dlg.processar("antes, me conte a história da UEMA", session_id, {})
    assert "UEMA foi fundada em 1981" in r_detour.answer
    assert "Incidente" in r_detour.answer and "Requisição" in r_detour.answer  # reapresentou a pergunta pendente
    assert r_detour.status == "hitl_pending"

    # Ticket continua exatamente onde estava — resposta válida agora avança.
    r_avanca = await dlg.processar("1", session_id, {})
    assert "categoria" in r_avanca.answer.lower()
