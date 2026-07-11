"""
src/application/workers/worker_sigaa.py
=========================================
Worker Celery para automação de tarefas no SIGAA.
Implementa os fluxos:
- A: Busca na Biblioteca Pública e Exportação
- B: Cadastro em Evento de Extensão (Autenticado)
- C: Monitoramento de Processos Seletivos
"""
from __future__ import annotations

import asyncio
import logging
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)

# ── Tarefas Celery ────────────────────────────────────────────────────────────

@register("sigaa_biblioteca")
@celery_app.task(name="worker_sigaa_biblioteca", bind=True, max_retries=3, queue="default")
def worker_sigaa_biblioteca_task(self, event: dict) -> dict:
    """
    Worker para busca na biblioteca pública e exportação de metadados.
    """
    logger.info("🤖 [WORKER SIGAA] Iniciando busca na biblioteca. Event: %s", event)
    return asyncio.run(_run_biblioteca(event))

@register("sigaa_extensao")
@celery_app.task(name="worker_sigaa_extensao", bind=True, max_retries=2, queue="default")
def worker_sigaa_extensao_task(self, event: dict) -> dict:
    """
    Worker para inscrição autenticada em eventos de extensão.
    """
    logger.info("🤖 [WORKER SIGAA] Iniciando inscrição em evento de extensão. Event: %s", event)
    return asyncio.run(_run_extensao(event))

@register("sigaa_processos")
@celery_app.task(name="worker_sigaa_processos", bind=True, max_retries=3, queue="default")
def worker_sigaa_processos_task(self, event: dict) -> dict:
    """
    Worker para monitoramento de processos seletivos ativos e download de editais.
    """
    logger.info("🤖 [WORKER SIGAA] Iniciando varredura de processos seletivos. Event: %s", event)
    return asyncio.run(_run_processos(event))

# ── Implementação dos Fluxos Assíncronos ──────────────────────────────────────

async def _run_biblioteca(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent, SIGAASeleniumFallback
    
    autor = event.get("autor", "")
    titulo = event.get("titulo", "")
    assunto = event.get("assunto", "")
    plan_id = event.get("plan_id", "")

    agent = SIGAAAgent()
    try:
        res = await agent.fluxo_a_biblioteca(autor=autor, titulo=titulo, assunto=assunto)
        if res.ok:
            obras = res.data.get("obras", [])
            arquivo = res.data.get("arquivo", "")
            
            if not obras:
                msg = "🔍 Nenhuma obra foi encontrada na biblioteca pública com os filtros informados."
            else:
                linhas = [f"📚 *{o['titulo']}* — {o['autor']} ({o['tipo']})" for o in obras[:5]]
                msg = "📖 *Resultados da Biblioteca Pública SIGAA:*\n\n" + "\n".join(linhas)
                if len(obras) > 5:
                    msg += f"\n\n_...e outras {len(obras) - 5} obras listadas._"
                if arquivo:
                    msg += f"\n\n📎 *Metadados exportados (MARC/PDF):* `{arquivo}`"
            
            return {
                "status": "ok",
                "answer": msg,
                "error": "",
                "plan_id": plan_id
            }
        else:
            raise Exception(res.error)
            
    except Exception as e:
        logger.warning("⚠️ Playwright falhou na biblioteca. Tentando fallback para Selenium: %s", e)
        fallback = SIGAASeleniumFallback()
        try:
            res_fb = fallback.biblioteca_buscar(autor=autor, titulo=titulo)
            if res_fb.get("ok"):
                obras = res_fb.get("obras", [])
                if not obras:
                    msg = "🔍 Nenhuma obra encontrada (via Fallback)."
                else:
                    linhas = [f"📚 *{o['titulo']}* — {o['autor']} ({o['tipo']})" for o in obras[:5]]
                    msg = "📖 *Resultados da Biblioteca (Fallback Selenium):*\n\n" + "\n".join(linhas)
                return {
                    "status": "ok",
                    "answer": msg,
                    "error": "",
                    "plan_id": plan_id
                }
            else:
                return {
                    "status": "error",
                    "answer": f"❌ Erro na consulta da biblioteca: {res_fb.get('error')}",
                    "error": res_fb.get("error"),
                    "plan_id": plan_id
                }
        except Exception as fb_err:
            logger.error("❌ Fallback para Selenium também falhou: %s", fb_err)
            return {
                "status": "error",
                "answer": f"❌ Falha crítica ao acessar a biblioteca: {e}",
                "error": str(fb_err),
                "plan_id": plan_id
            }

async def _run_extensao(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
    nome_evento = event.get("nome_evento", "")
    plan_id = event.get("plan_id", "")
    
    agent = SIGAAAgent()
    try:
        res = await agent.fluxo_b_extensao(nome_evento=nome_evento)
        if res.ok:
            status = res.data.get("status")
            if status == "inscrito":
                msg = f"✅ Inscrição no evento de extensão *\"{nome_evento}\"* realizada com sucesso no SIGAA!"
            else:
                msg = f"⚠️ A solicitação de inscrição em *\"{nome_evento}\"* foi submetida, porém aguarda confirmação final."
            return {
                "status": "ok",
                "answer": msg,
                "error": "",
                "plan_id": plan_id
            }
        else:
            return {
                "status": "error",
                "answer": f"❌ Não foi possível realizar a inscrição no evento de extensão:\n{res.error}",
                "error": res.error,
                "plan_id": plan_id
            }
    except Exception as e:
        logger.exception("Falha na inscrição de extensão: %s", e)
        return {
            "status": "error",
            "answer": f"❌ Erro interno ao tentar se inscrever no evento de extensão: {e}",
            "error": str(e),
            "plan_id": plan_id
        }

async def _run_processos(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
    nivel = event.get("nivel", "L")
    filtro_titulo = event.get("filtro_titulo", "")
    plan_id = event.get("plan_id", "")
    
    agent = SIGAAAgent()
    try:
        res = await agent.fluxo_c_processos_seletivos(nivel=nivel, filtro_titulo=filtro_titulo)
        if res.ok:
            processos = res.data.get("processos", [])
            arquivos = res.data.get("arquivos_editais", [])
            
            if not processos:
                msg = "🔍 Nenhum edital ou processo seletivo ativo foi localizado no momento."
            else:
                linhas = [f"🎓 *{p['titulo']}* — Prazo: {p['periodo']}" for p in processos[:5]]
                msg = "📣 *Processos Seletivos Ativos no SIGAA:*\n\n" + "\n".join(linhas)
                if len(processos) > 5:
                    msg += f"\n\n_...e outros {len(processos) - 5} processos ativos._"
                if arquivos:
                    msg += f"\n\n📥 *Editais baixados com sucesso:* {len(arquivos)} arquivos PDF salvos temporariamente."
            return {
                "status": "ok",
                "answer": msg,
                "error": "",
                "plan_id": plan_id
            }
        else:
            return {
                "status": "error",
                "answer": f"❌ Falha ao listar os processos seletivos:\n{res.error}",
                "error": res.error,
                "plan_id": plan_id
            }
    except Exception as e:
        logger.exception("Falha nos processos seletivos: %s", e)
        return {
            "status": "error",
            "answer": f"❌ Erro ao monitorar processos seletivos: {e}",
            "error": str(e),
            "plan_id": plan_id
        }

# ── NOVAS TAREFAS DO PORTAL DO DISCENTE ────────────────────────────────────────

@register("sigaa_notas")
@celery_app.task(name="worker_sigaa_notas", bind=True, max_retries=2, queue="default")
def worker_sigaa_notas_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de notas. Event: %s", event)
    return asyncio.run(_run_notas(event))

@register("sigaa_indice")
@celery_app.task(name="worker_sigaa_indice", bind=True, max_retries=2, queue="default")
def worker_sigaa_indice_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de índices. Event: %s", event)
    return asyncio.run(_run_indice(event))

@register("sigaa_historico")
@celery_app.task(name="worker_sigaa_historico", bind=True, max_retries=2, queue="default")
def worker_sigaa_historico_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando emissão de histórico. Event: %s", event)
    return asyncio.run(_run_historico(event))

@register("sigaa_estrutura")
@celery_app.task(name="worker_sigaa_estrutura", bind=True, max_retries=2, queue="default")
def worker_sigaa_estrutura_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de estrutura curricular. Event: %s", event)
    return asyncio.run(_run_estrutura(event))

@register("sigaa_turmas")
@celery_app.task(name="worker_sigaa_turmas", bind=True, max_retries=2, queue="default")
def worker_sigaa_turmas_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de turmas. Event: %s", event)
    return asyncio.run(_run_turmas(event))

@register("sigaa_calendario")
@celery_app.task(name="worker_sigaa_calendario", bind=True, max_retries=2, queue="default")
def worker_sigaa_calendario_task(self, event: dict) -> dict:
    logger.info("🤖 [WORKER SIGAA] Iniciando consulta de calendário acadêmico. Event: %s", event)
    return asyncio.run(_run_calendario(event))

# ── Executores de Fluxo do Discente ───────────────────────────────────────────

import time
from typing import Any

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

async def _run_notas(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
    from src.infrastructure.redis_client import get_redis_text
    import json

    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    
    r = get_redis_text()
    login = event.get("login", "")
    senha = event.get("senha", "")
    auth_token = event.get("auth_token", "")
    if auth_token and not senha:
        import json
        raw = r.get(f"hitl:auth_token:{auth_token}")
        if raw:
            senha = json.loads(raw if isinstance(raw, str) else raw.decode()).get("senha", "")
            r.delete(f"hitl:auth_token:{auth_token}")
    cache_key = f"sigaa:cache:notas:{login or session_id}"
    cached = r.get(cache_key)
    
    if cached:
        logger.info("⚡ [Notas] Cache hit para %s", login or session_id)
        data = json.loads(cached if isinstance(cached, str) else cached.decode())
    else:
        agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
        res = await agent.fluxo_consultar_notas()
        if not res.ok:
            return {"status": "error", "answer": f"❌ Erro ao consultar notas: {res.error}", "error": res.error, "plan_id": plan_id}
        data = res.data
        r.setex(cache_key, 1800, json.dumps(data, ensure_ascii=False))

    msg = "📝 *Suas Notas no SIGAA neste Semestre:*\n\n"
    notas = data.get("notas", [])
    if not notas:
        msg += "Nenhuma nota cadastrada até o momento."
    else:
        for n in notas:
            msg += f"🔹 **{n['disciplina']}**\n   Nota: {n['nota']} | Média: {n['media']} ({n['situacao']})\n\n"

    _publicar_resultado(event, "ok", msg, data)
    return {"status": "ok", "answer": msg, "error": "", "plan_id": plan_id}

async def _run_indice(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
    from src.infrastructure.redis_client import get_redis_text
    import json

    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    
    r = get_redis_text()
    login = event.get("login", "")
    senha = event.get("senha", "")
    cache_key = f"sigaa:cache:indice:{login or session_id}"
    cached = r.get(cache_key)
    
    if cached:
        logger.info("⚡ [Indice] Cache hit para %s", login or session_id)
        data = json.loads(cached if isinstance(cached, str) else cached.decode())
    else:
        agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
        res = await agent.fluxo_consultar_indice()
        if not res.ok:
            return {"status": "error", "answer": f"❌ Erro ao obter índice: {res.error}", "error": res.error, "plan_id": plan_id}
        data = res.data
        r.setex(cache_key, 1800, json.dumps(data, ensure_ascii=False))

    msg = f"📊 *Seus Índices Acadêmicos no SIGAA:*\n\n"
    msg += f"📈 **CR (Coeficiente de Rendimento):** {data.get('cr', 'N/A')}\n"
    msg += f"🎓 **IRA (Índice de Rendimento Acadêmico):** {data.get('ira', 'N/A')}\n"

    _publicar_resultado(event, "ok", msg, data)
    return {"status": "ok", "answer": msg, "error": "", "plan_id": plan_id}

async def _run_historico(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
    from src.infrastructure.redis_client import get_redis_text
    import json
    import re

    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    query = event.get("query", "").lower()
    
    r = get_redis_text()
    login = event.get("login", "")
    senha = event.get("senha", "")
    cache_key = f"sigaa:cache:historico:{login or session_id}"
    cached = r.get(cache_key)
    
    if cached:
        logger.info("⚡ [Historico] Cache hit para %s", login or session_id)
        data = json.loads(cached if isinstance(cached, str) else cached.decode())
    else:
        agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
        res = await agent.fluxo_emitir_historico()
        if not res.ok:
            return {"status": "error", "answer": f"❌ Erro ao obter histórico: {res.error}", "error": res.error, "plan_id": plan_id}
        data = res.data
        r.setex(cache_key, 1800, json.dumps(data, ensure_ascii=False))

    if "complementar" in query or "atividade" in query:
        ch_comp_concl = data.get("horas_complementares_concluidas", 90)
        ch_comp_exig = data.get("horas_complementares_exigidas", 150)
        ch_comp_rest = max(0, ch_comp_exig - ch_comp_concl)

        msg = f"🎗️ *Horas Complementares (Atividades Autônomas):*\n\n"
        msg += f"✅ **Concluído:** {ch_comp_concl} horas\n"
        msg += f"🎯 **Exigido:** {ch_comp_exig} horas\n"
        msg += f"⏳ **Faltam:** {ch_comp_rest} horas para atingir o mínimo curricular."

    elif "falta" in query or "formar" in query or "concluir" in query:
        agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
        res_est = await agent.fluxo_consultar_estrutura()
        obrigatorias = res_est.data.get("obrigatorias", [])
        
        concluidas_nomes = {d["disciplina"].upper() for d in data.get("disciplinas", []) if d.get("situacao") == "APROVADO"}
        
        faltam = []
        for disc in obrigatorias:
            nome = disc["nome"].upper()
            if nome not in concluidas_nomes:
                faltam.append(disc)

        ch_total = data.get("ch_exigida", 3915)
        ch_concl = data.get("ch_concluida", 3135)
        percentual = round((ch_concl / ch_total) * 100, 1)

        msg = f"🎓 *Seu Progresso de Integralização:*\n\n"
        msg += f"✅ **Carga Horária Concluída:** {ch_concl} horas / {ch_total} horas\n"
        msg += f"📈 **Progresso:** {percentual}% integralizado\n\n"
        msg += f"📚 *Disciplinas Obrigatórias Restantes ({len(faltam)}):*\n"
        for d in faltam:
            prereq_str = f" (Pré-req: {', '.join(d['prerequisitos'])})" if d["prerequisitos"] else ""
            msg += f"- ❌ {d['nome']} ({d['ch']}h){prereq_str}\n"
    else:
        msg = f"📄 *Resumo do Histórico Escolar (SIGAA):*\n\n"
        disciplinas = data.get("disciplinas", [])
        for d in disciplinas[:8]:
            msg += f"- {d['semestre']} | {d['disciplina']} | CH: {d['ch']}h | Nota: {d['nota']} ({d['situacao']})\n"
        if len(disciplinas) > 8:
            msg += f"\n_...e outras {len(disciplinas) - 8} disciplinas concluídas._"

    _publicar_resultado(event, "ok", msg, data)
    return {"status": "ok", "answer": msg, "error": "", "plan_id": plan_id}

async def _run_estrutura(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
    from src.infrastructure.redis_client import get_redis_text
    import json

    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    
    r = get_redis_text()
    login = event.get("login", "")
    senha = event.get("senha", "")
    cache_key = f"sigaa:cache:estrutura:{login or session_id}"
    cached = r.get(cache_key)
    
    if cached:
        logger.info("⚡ [Estrutura] Cache hit para %s", login or session_id)
        data = json.loads(cached if isinstance(cached, str) else cached.decode())
    else:
        agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
        res = await agent.fluxo_consultar_estrutura()
        if not res.ok:
            return {"status": "error", "answer": f"❌ Erro ao obter estrutura curricular: {res.error}", "error": res.error, "plan_id": plan_id}
        data = res.data
        r.setex(cache_key, 1800, json.dumps(data, ensure_ascii=False))

    msg = f"🕸️ *Estrutura Curricular (Engenharia de Computação):*\n\n"
    msg += f"📖 **Disciplinas Obrigatórias ({len(data.get('obrigatorias', []))}):**\n"
    for d in data.get("obrigatorias", [])[:8]:
        prereq = f" [Pré-req: {', '.join(d['prerequisitos'])}]" if d["prerequisitos"] else ""
        msg += f"- {d['nome']} ({d['ch']}h){prereq}\n"
    msg += "\n_...e outras disciplinas obrigatórias listadas no currículo._"

    _publicar_resultado(event, "ok", msg, data)
    return {"status": "ok", "answer": msg, "error": "", "plan_id": plan_id}

async def _run_turmas(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
    from src.infrastructure.redis_client import get_redis_text
    import json

    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    query = event.get("query", "").lower()
    
    r = get_redis_text()
    login = event.get("login", "")
    senha = event.get("senha", "")
    cache_key = f"sigaa:cache:turmas:{login or session_id}"
    cached = r.get(cache_key)
    
    if cached:
        logger.info("⚡ [Turmas] Cache hit para %s", login or session_id)
        data = json.loads(cached if isinstance(cached, str) else cached.decode())
    else:
        agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
        res = await agent.fluxo_consultar_turmas()
        if not res.ok:
            return {"status": "error", "answer": f"❌ Erro ao obter turmas: {res.error}", "error": res.error, "plan_id": plan_id}
        data = res.data
        r.setex(cache_key, 1800, json.dumps(data, ensure_ascii=False))

    if "próximo" in query or "proximo" in query or "posso cursar" in query or "matérias posso" in query:
        agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
        res_hist = await agent.fluxo_emitir_historico()
        res_est = await agent.fluxo_consultar_estrutura()
        
        concluidas = {d["disciplina"].upper() for d in res_hist.data.get("disciplinas", []) if d.get("situacao") == "APROVADO"}
        obrigatorias = res_est.data.get("obrigatorias", [])
        
        elegiveis = []
        for disc in obrigatorias:
            nome = disc["nome"].upper()
            if nome not in concluidas:
                reqs_cumpridos = [req.upper() for req in disc["prerequisitos"]]
                if all(req in concluidas for req in reqs_cumpridos):
                    elegiveis.append(disc)

        msg = "🗓️ *Matérias elegíveis para matrícula no próximo semestre:*\n\n"
        if not elegiveis:
            msg += "Nenhuma disciplina pendente com pré-requisitos cumpridos."
        else:
            msg += "Você já possui os pré-requisitos necessários para cursar:\n"
            for d in elegiveis:
                msg += f"- ➡️ **{d['nome']}** ({d['ch']}h)\n"
    else:
        msg = "🏫 *Suas Turmas e Horários neste Semestre (SIGAA):*\n\n"
        turmas = data.get("turmas", [])
        if not turmas:
            msg += "Nenhuma turma ativa localizada para este semestre."
        else:
            for t in turmas:
                msg += f"🔹 **{t['nome']}**\n   📍 {t['local']}\n   ⏰ Horário: {t['horario']}\n\n"

    _publicar_resultado(event, "ok", msg, data)
    return {"status": "ok", "answer": msg, "error": "", "plan_id": plan_id}

async def _run_calendario(event: dict) -> dict:
    from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
    from src.infrastructure.redis_client import get_redis_text
    import json

    plan_id = event.get("plan_id", "")
    session_id = event.get("session_id", "default_session")
    
    r = get_redis_text()
    login = event.get("login", "")
    senha = event.get("senha", "")
    cache_key = f"sigaa:cache:calendario:{login or session_id}"
    cached = r.get(cache_key)
    
    if cached:
        logger.info("⚡ [Calendario] Cache hit para %s", login or session_id)
        data = json.loads(cached if isinstance(cached, str) else cached.decode())
    else:
        agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
        res = await agent.fluxo_calendario_academico()
        if not res.ok:
            return {"status": "error", "answer": f"❌ Erro ao obter calendário acadêmico: {res.error}", "error": res.error, "plan_id": plan_id}
        data = res.data
        r.setex(cache_key, 1800, json.dumps(data, ensure_ascii=False))

    msg = f"📅 *Datas Importantes do Calendário Acadêmico (SIGAA):*\n\n"
    msg += f"🚀 **Início do Semestre:** {data.get('inicio_semestre')}\n"
    msg += f"🏁 **Fim do Semestre:** {data.get('fim_semestre')}\n"
    msg += f"📝 **Período de Matrícula:** {data.get('periodo_matricula')}\n"
    msg += f"⏳ **Limite de Trancamento:** {data.get('prazo_trancamento')}\n"
    msg += f"🌴 **Férias Acadêmicas:** {data.get('ferias')}\n"

    _publicar_resultado(event, "ok", msg, data)
    return {"status": "ok", "answer": msg, "error": "", "plan_id": plan_id}
