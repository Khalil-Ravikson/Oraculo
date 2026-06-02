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
