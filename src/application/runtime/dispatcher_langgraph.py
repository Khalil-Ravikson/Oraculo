"""
src/application/runtime/dispatcher_langgraph.py
====================================================
EXPERIMENTO — existe só na worktree/branch `langgraph`. Não é uma versão
"nova" do dispatcher.py original, é um wrapper fino que intercepta só as
rotas TICKET_ABERTURA e RAG (GERAL/CALENDARIO/EDITAL/CONTATOS/WIKI) para
desviar pro StateGraph do LangGraph (`langgraph_experiment/graph.py`).
Qualquer outra rota (SIGAA, comandos !@$, CRUD, etc.) é 100% delegada pro
`application/runtime/dispatcher.py` ORIGINAL — sem duplicar guardrails,
Orchestrator, HITL do SIGAA, cache semântico, nem nenhuma outra lógica que
já existe lá.

Classificação de rota reaproveita `router/supervisor.py::rotear()` (o
Supervisor real, 5 camadas) — não o classify_node interno do
langgraph_experiment (que é só um regex simplificado usado no teste via
CLI). Isso evita reabrir o problema dos "três cérebros" documentado em
`notas.md` item 5.1: aqui só tem UM classificador decidindo, o mesmo que
already roda em produção.

Checkpointer: `AsyncRedisSaver` (não `MemorySaver`) — obrigatório porque a
API e os workers Celery rodam em processos/containers diferentes; um
funil de ticket que pausa (`interrupt()`) num processo e retoma noutro
precisa que o estado esteja em Redis, não em memória local.

Ativado trocando o import em `process_message_task.py`:
    from src.application.runtime.dispatcher import processar as cognitive_processar
    →
    from src.application.runtime.dispatcher_langgraph import processar as cognitive_processar
"""
from __future__ import annotations

import asyncio
import logging
import time

from src.application.runtime.dispatcher import OSResult
from src.application.runtime.dispatcher import processar as _processar_original

logger = logging.getLogger(__name__)

_ROTAS_LANGGRAPH = {"TICKET_ABERTURA", "GERAL", "CALENDARIO", "EDITAL", "CONTATOS", "WIKI"}

_graph = None
_saver_cm = None
_setup_lock = asyncio.Lock()


async def _get_graph():
    global _graph, _saver_cm
    if _graph is not None:
        return _graph
    async with _setup_lock:
        if _graph is not None:
            return _graph

        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        from langgraph_experiment.graph import build_graph
        from src.infrastructure.settings import settings

        _saver_cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL)
        saver = await _saver_cm.__aenter__()  # mantido aberto pela vida do processo, mesmo padrão de singleton de client usado em synthesis.py/embeddings.py
        await saver.asetup()
        _graph = build_graph(saver)
        logger.info("🧪 [LANGGRAPH] Grafo compilado com AsyncRedisSaver — checkpoint compartilhado entre API/workers.")
    return _graph


def _thread_config(session_id: str) -> dict:
    return {"configurable": {"thread_id": f"lg_ticket_{session_id}"}}


async def _tem_interrupt_pendente(app, config) -> bool:
    state = await app.aget_state(config)
    return bool(state.next)


def _to_os_result(result: dict, rota: str, t0: float) -> OSResult:
    ms = int((time.monotonic() - t0) * 1000)
    interrupts = result.get("__interrupt__")
    if interrupts:
        pergunta = interrupts[0].value.get("question", "")
        return OSResult(
            answer=pergunta, plan_id="langgraph_hitl", rota=rota,
            cache_hit=False, total_ms=ms, status="hitl_pending",
        )
    return OSResult(
        answer=result.get("answer", ""), plan_id="langgraph_final", rota=rota,
        cache_hit=False, total_ms=ms, status="ok",
    )


async def processar(
    message: str,
    session_id: str,
    user_context: dict,
    history: str = "",
    fatos: list[str] | None = None,
) -> OSResult:
    t0 = time.monotonic()
    app = await _get_graph()
    config = _thread_config(session_id)

    # ── 0. Retomada de um interrupt() pendente (funil de ticket em andamento) ──
    if await _tem_interrupt_pendente(app, config):
        from langgraph.types import Command

        state = await app.aget_state(config)
        rota = "TICKET_ABERTURA" if state.values.get("route") == "ticket" else state.values.get("route", "GERAL").upper()
        result = await app.ainvoke(Command(resume=message), config=config)
        return _to_os_result(result, rota, t0)

    # ── 1. Classificação (reaproveita o Supervisor real, não duplica regra) ────
    from src.router.supervisor import rotear

    decision = await rotear(message, session_id, user_context)

    if decision.rota not in _ROTAS_LANGGRAPH:
        # Não é escopo deste experimento (SIGAA, CRUD, comandos, greeting...)
        # → delega inteiro pro pipeline original, sem retrabalho nosso.
        return await _processar_original(message, session_id, user_context, history, fatos)

    route = "ticket" if decision.rota == "TICKET_ABERTURA" else "rag"
    logger.info("🧪 [LANGGRAPH] rota=%s → node=%s (session=%s)", decision.rota, route, session_id)

    result = await app.ainvoke(
        {"session_id": session_id, "message": message, "route": route},
        config=config,
    )
    return _to_os_result(result, decision.rota, t0)
