"""
Regressão do funil de CRUD de cadastro (HITL) do experimento LangGraph —
mesmo padrão/motivo do test_langgraph_ticket_hitl.py (MemorySaver, isolado
do bug de checkpointer Redis). Escopo igual ao crud_tool.py original
(src/agents/tickets/crud_tool.py): só setor (CentroEnum) e telefone.
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
    """Isola os testes do funil de CRUD da dependência de Postgres real.

    rbac.checar_permissao_chamado() (chamado por crud_tool.py em cada passo do
    funil) chama buscar_pessoa_por_telefone(), que abre uma conexão de verdade
    ao banco — sem Postgres no ambiente de teste, isso derruba os testes com
    OSError de conexão recusada, não uma falha de lógica do funil. Nenhum
    teste aqui exercita RBAC/lookup em si, só o funil do grafo, então simula
    "sem cadastro encontrado" e liga DEV_TEST_SKIP_REGISTRATION pra
    checar_permissao_chamado sintetizar o usuário de teste permissivo que ela
    já sabe montar (rbac.py:22-31) sem tocar o banco.
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
    mocka "sem sessão pendente" sem tocar Redis de verdade.

    Também neutraliza o rate limit real de InputGuardrail (mesmo commit) —
    ver comentário equivalente em test_langgraph_ticket_hitl.py (achado
    via CI com Redis real: um teste dessa suíte encadeando várias chamadas
    de dlg.processar() na mesma sessão tropeçava no rate limit real, sem
    relação com o que o teste queria exercitar)."""
    async def _sem_sessao(*a, **k):
        return None

    monkeypatch.setattr(
        "src.capabilities.persistence.redis_state.get_hitl_session", _sem_sessao,
    )
    monkeypatch.setattr(
        "src.application.chain.guardrails.InputGuardrail._check_rate_limit",
        lambda self, user_id, r: (False, ""),
    )


@pytest.mark.asyncio
async def test_crud_atualizar_setor_completo(monkeypatch):
    from src.infrastructure import settings as settings_module
    monkeypatch.setattr(settings_module.settings, "DEV_TEST_NO_DB_WRITE", True)

    app = build_graph()
    config = {"configurable": {"thread_id": "test_crud_setor"}}

    r = await app.ainvoke(
        {"session_id": "test_crud_setor", "message": "quero atualizar meu cadastro", "route": "crud"},
        config=config,
    )
    assert "setor" in _pergunta(r).lower() and "telefone" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="setor"), config=config)
    assert "novo setor" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="CCT"), config=config)
    assert "confirma" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="sim"), config=config)
    assert "__interrupt__" not in r or not r["__interrupt__"]
    assert "[DEV]" in r["answer"]
    assert "CCT" in r["answer"]


@pytest.mark.asyncio
async def test_crud_valor_invalido_repergunta():
    app = build_graph()
    config = {"configurable": {"thread_id": "test_crud_invalido"}}

    await app.ainvoke(
        {"session_id": "test_crud_invalido", "message": "atualizar meu setor", "route": "crud"},
        config=config,
    )
    r = await app.ainvoke(Command(resume="setor"), config=config)
    assert "novo setor" in _pergunta(r).lower()

    # "Engenharia" não é sigla válida do CentroEnum.
    r = await app.ainvoke(Command(resume="Engenharia"), config=config)
    pergunta = _pergunta(r)
    assert "inválido" in pergunta.lower()
    assert "novo setor" in pergunta.lower()  # re-perguntou


@pytest.mark.asyncio
async def test_crud_campo_telefone_linguagem_natural():
    app = build_graph()
    config = {"configurable": {"thread_id": "test_crud_telefone"}}

    await app.ainvoke(
        {"session_id": "test_crud_telefone", "message": "quero mudar meu cadastro", "route": "crud"},
        config=config,
    )
    r = await app.ainvoke(Command(resume="quero mudar meu número"), config=config)
    assert "telefone" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="98988887777"), config=config)
    assert "confirma" in _pergunta(r).lower()

    r = await app.ainvoke(Command(resume="cancelar"), config=config)
    assert "__interrupt__" not in r or not r["__interrupt__"]
    assert r["answer"] == "❌ Atualização cancelada."


@pytest.mark.asyncio
async def test_dispatcher_nao_vaza_estado_entre_crud_e_ticket(monkeypatch):
    from src.infrastructure import settings as settings_module
    monkeypatch.setattr(settings_module.settings, "DEV_TEST_NO_DB_WRITE", True)

    dlg._graph = None
    dlg._graph = build_graph()

    # rotear() só é chamado na classificação inicial de cada fluxo — respostas
    # válidas subsequentes (setor/CCT/sim) resumem direto, sem reclassificar.
    decisoes = iter([_fake_decision("CRUD"), _fake_decision("TICKET_ABERTURA")])

    async def _rotear_sequencial(*a, **k):
        return next(decisoes)

    monkeypatch.setattr("src.router.supervisor.rotear", _rotear_sequencial)

    session_id = "test_dispatcher_crud_ticket"

    await dlg.processar("atualizar cadastro", session_id, {})
    await dlg.processar("setor", session_id, {})
    await dlg.processar("CCT", session_id, {})
    r_crud = await dlg.processar("sim", session_id, {})
    assert "CCT" in r_crud.answer

    # Ticket novo na MESMA sessão — não pode herdar nada do CRUD anterior.
    r_ticket = await dlg.processar("abrir chamado", session_id, {})
    assert "Incidente" in r_ticket.answer and "Requisição" in r_ticket.answer

    dlg._graph = None
