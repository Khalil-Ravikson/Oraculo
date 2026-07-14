"""
src/application/workers/worker_sigaa.py
=========================================
Worker Celery para automação de tarefas no SIGAA — emagrecido na Fase 5 do
PLANO_REFATORACAO_SUPERVISOR.md (seção 2.3): só desempacota o evento Celery,
chama `agents/sigaa/service.py::SigaaService` (decisão + formatação +
elegibilidade) e publica o resultado. Scraping puro vive em
`capabilities/sigaa/browser.py`.

Implementa os fluxos:
- A: Busca na Biblioteca Pública e Exportação
- B: Cadastro em Evento de Extensão (Autenticado)
- C: Monitoramento de Processos Seletivos
- Portal do Discente: notas, índice (CR/IRA), histórico, estrutura curricular, turmas, calendário
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.agents.sigaa.service import SigaaService
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)

_service = SigaaService()


def _evento_seguro_para_log(event: dict) -> dict:
    """Cópia do evento com a senha mascarada, para nunca logar credenciais em texto plano."""
    seguro = dict(event)
    if seguro.get("senha"):
        seguro["senha"] = "***"
    return seguro


# ── Tarefas Celery ────────────────────────────────────────────────────────────

@register("sigaa_biblioteca")
@celery_app.task(name="worker_sigaa_biblioteca", bind=True, max_retries=3, queue="default")
def worker_sigaa_biblioteca_task(self, event: dict) -> dict:
    """Worker para busca na biblioteca pública e exportação de metadados."""
    logger.info("🤖 [WORKER SIGAA] Iniciando busca na biblioteca. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_biblioteca(event))

@register("sigaa_extensao")
@celery_app.task(name="worker_sigaa_extensao", bind=True, max_retries=2, queue="default")
def worker_sigaa_extensao_task(self, event: dict) -> dict:
    """Worker para inscrição autenticada em eventos de extensão."""
    logger.info("🤖 [WORKER SIGAA] Iniciando inscrição em evento de extensão. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_extensao(event))

@register("sigaa_processos")
@celery_app.task(name="worker_sigaa_processos", bind=True, max_retries=3, queue="default")
def worker_sigaa_processos_task(self, event: dict) -> dict:
    """Worker para monitoramento de processos seletivos ativos e download de editais."""
    logger.info("🤖 [WORKER SIGAA] Iniciando varredura de processos seletivos. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_processos(event))

@register("sigaa_notas")
@celery_app.task(name="worker_sigaa_notas", bind=True, max_retries=2, queue="default")
def worker_sigaa_notas_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de notas. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_notas(event))

@register("sigaa_indice")
@celery_app.task(name="worker_sigaa_indice", bind=True, max_retries=2, queue="default")
def worker_sigaa_indice_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de índices. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_indice(event))

@register("sigaa_historico")
@celery_app.task(name="worker_sigaa_historico", bind=True, max_retries=2, queue="default")
def worker_sigaa_historico_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando emissão de histórico. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_historico(event))

@register("sigaa_estrutura")
@celery_app.task(name="worker_sigaa_estrutura", bind=True, max_retries=2, queue="default")
def worker_sigaa_estrutura_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de estrutura curricular. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_estrutura(event))

@register("sigaa_turmas")
@celery_app.task(name="worker_sigaa_turmas", bind=True, max_retries=2, queue="default")
def worker_sigaa_turmas_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de turmas. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_turmas(event))

@register("sigaa_calendario")
@celery_app.task(name="worker_sigaa_calendario", bind=True, max_retries=2, queue="default")
def worker_sigaa_calendario_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de calendário acadêmico. Event: %s", _evento_seguro_para_log(event))
    return asyncio.run(_run_calendario(event))


# ── Publicação de resultado (Redis Stream + cache + task history) ────────────

def _publicar_resultado(event: dict, status: str, answer: str, data: Any = None) -> None:
    plan_id = event.get("plan_id", "hitl_fast_path")
    step_id = event.get("step_id", "s1")
    session_id = event.get("session_id", "default_session")

    from src.infrastructure.redis_client import get_redis_text
    import json
    r = get_redis_text()

    cache_data = {
        "status": status,
        "answer": answer,
        "data": data
    }
    r.setex(f"plan:results:{plan_id}:{step_id}", 300, json.dumps(cache_data, ensure_ascii=False))

    r.xadd(
        "oraculo:stream:final_responses",
        {
            "plan_id": plan_id,
            "session_id": session_id,
            "status": status,
            "answer": answer,
            "latency_ms": "100",
            "ts": str(time.time())
        },
        maxlen=2000, approximate=True
    )

    # Save to Task History (Layer 3) so LLMOrchestrator can reference it later
    try:
        r.hset(f"task_hist:{session_id}", mapping={
            "last_worker": "sigaa",
            "last_result": answer[:400],
            "ts": str(int(time.time())),
        })
        r.expire(f"task_hist:{session_id}", 1800)
    except Exception:
        pass


# ── Executores de Fluxo (fino: desempacota event -> SigaaService -> publica) ──

async def _run_biblioteca(event: dict) -> dict:
    plan_id = event.get("plan_id", "")
    resultado = await _service.buscar_biblioteca(
        autor=event.get("autor", ""),
        titulo=event.get("titulo", ""),
        assunto=event.get("assunto", ""),
    )
    return {"status": resultado.status, "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

async def _run_extensao(event: dict) -> dict:
    plan_id = event.get("plan_id", "")
    resultado = await _service.inscrever_extensao(nome_evento=event.get("nome_evento", ""))
    return {"status": resultado.status, "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

async def _run_processos(event: dict) -> dict:
    plan_id = event.get("plan_id", "")
    resultado = await _service.processos_seletivos(
        nivel=event.get("nivel", "L"),
        filtro_titulo=event.get("filtro_titulo", ""),
    )
    return {"status": resultado.status, "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

async def _run_notas(event: dict) -> dict:
    from src.infrastructure.redis_client import get_redis_text
    import json

    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")

    r = get_redis_text()
    login = event.get("login", "")
    senha = event.get("senha", "")
    auth_token = event.get("auth_token", "")
    if auth_token and not senha:
        raw = r.get(f"hitl:auth_token:{auth_token}")
        if raw:
            senha = json.loads(raw if isinstance(raw, str) else raw.decode()).get("senha", "")
            r.delete(f"hitl:auth_token:{auth_token}")

    resultado = await _service.consultar_notas(login, senha, session_id)
    if resultado.status != "ok":
        return {"status": "error", "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

    _publicar_resultado(event, "ok", resultado.answer, resultado.data)
    return {"status": "ok", "answer": resultado.answer, "error": "", "plan_id": plan_id}

async def _run_indice(event: dict) -> dict:
    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    login = event.get("login", "")
    senha = event.get("senha", "")

    resultado = await _service.consultar_indice(login, senha, session_id)
    if resultado.status != "ok":
        return {"status": "error", "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

    _publicar_resultado(event, "ok", resultado.answer, resultado.data)
    return {"status": "ok", "answer": resultado.answer, "error": "", "plan_id": plan_id}

async def _run_historico(event: dict) -> dict:
    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    query = event.get("query", "")
    login = event.get("login", "")
    senha = event.get("senha", "")

    resultado = await _service.emitir_historico(login, senha, session_id, query)
    if resultado.status != "ok":
        return {"status": "error", "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

    _publicar_resultado(event, "ok", resultado.answer, resultado.data)
    return {"status": "ok", "answer": resultado.answer, "error": "", "plan_id": plan_id}

async def _run_estrutura(event: dict) -> dict:
    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    login = event.get("login", "")
    senha = event.get("senha", "")

    resultado = await _service.consultar_estrutura(login, senha, session_id)
    if resultado.status != "ok":
        return {"status": "error", "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

    _publicar_resultado(event, "ok", resultado.answer, resultado.data)
    return {"status": "ok", "answer": resultado.answer, "error": "", "plan_id": plan_id}

async def _run_turmas(event: dict) -> dict:
    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    query = event.get("query", "")
    login = event.get("login", "")
    senha = event.get("senha", "")

    resultado = await _service.consultar_turmas(login, senha, session_id, query)
    if resultado.status != "ok":
        return {"status": "error", "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

    _publicar_resultado(event, "ok", resultado.answer, resultado.data)
    return {"status": "ok", "answer": resultado.answer, "error": "", "plan_id": plan_id}

async def _run_calendario(event: dict) -> dict:
    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    login = event.get("login", "")
    senha = event.get("senha", "")

    resultado = await _service.consultar_calendario(login, senha, session_id)
    if resultado.status != "ok":
        return {"status": "error", "answer": resultado.answer, "error": resultado.error, "plan_id": plan_id}

    _publicar_resultado(event, "ok", resultado.answer, resultado.data)
    return {"status": "ok", "answer": resultado.answer, "error": "", "plan_id": plan_id}
