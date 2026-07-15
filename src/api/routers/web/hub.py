# src/api/hub.py
"""
Hub Admin — Controller MVC para o portal web do admin.

SEPARAÇÃO MVC:
  M (Model):      dados vêm de admin_api.py (REST) e Redis
  V (View):       templates/hub/*.html (Jinja2)
  C (Controller): este arquivo (hub.py)

ROTAS:
  GET  /                → redirect para /hub se logado, /hub/login se não
  GET  /hub/            → dashboard principal (requer cookie admin_token)
  GET  /hub/login       → página de login
  POST /hub/login       → processa login (seta cookie + redirect)
  GET  /hub/logout      → limpa cookie + redirect para /hub/login
  GET  /hub/metrics     → SSE: stream de métricas a cada 2s
  GET  /hub/audit       → página de audit log

FLUXO DE AUTH:
  1. GET /hub/ sem cookie → redirect /hub/login
  2. POST /hub/login com credenciais válidas → cookie admin_token (24h) + redirect /hub/
  3. GET /hub/ com cookie válido → renderiza dashboard
  4. GET /hub/logout → delete cookie + redirect /hub/login
"""
from __future__ import annotations

import logging
import json
import asyncio
from fastapi import APIRouter, Depends, Form, Request,HTTPException 
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


from src.api.middleware.auth_middleware import TokenPayload
from src.application.use_cases.admin_auth import get_admin_auth
from src.infrastructure.settings import settings

logger    = logging.getLogger(__name__)
router    = APIRouter(prefix="/hub", tags=["Portal Admin"])
templates = Jinja2Templates(directory="templates")


def _verificar_cookie(request: Request) -> TokenPayload | None:
    """Verifica cookie admin_token sem lançar exception (para redirects)."""
    token = request.cookies.get("admin_token")
    if not token:
        return None
    auth = get_admin_auth()
    if auth.token_esta_bloqueado(token):
        return None
    return auth.verificar_token(token)


# ─────────────────────────────────────────────────────────────────────────────
# Rotas públicas
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, erro: str = ""):
    """Página de login do portal admin."""
    if _verificar_cookie(request):
        return RedirectResponse("/hub/", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="hub/login.html",
        context={"request": request, "erro": erro},
    )


@router.post("/login")
async def login_submit(
    request:  Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Processa login: valida credenciais → seta cookie → redirect."""
    auth   = get_admin_auth()
    result = auth.login(username, password)

    if not result.sucesso:
        return RedirectResponse(
            f"/hub/login?erro={result.erro}",
            status_code=302,
        )

    response = RedirectResponse("/hub/", status_code=302)
    response.set_cookie(
        key="admin_token",
        value=result.access_token,
        max_age=result.expires_in,
        httponly=True,
        samesite="lax",
        secure=False,  # True em HTTPS produção
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Limpa cookie e invalida token."""
    token = request.cookies.get("admin_token")
    if token:
        get_admin_auth().invalidar_token(token)

    response = RedirectResponse("/hub/login", status_code=302)
    response.delete_cookie("admin_token")
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Rotas protegidas
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard principal — requer autenticação."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="hub/index.html",
        context={
            "request":  request,
            "username": payload.sub,
            "modelo":   settings.GEMINI_MODEL,
            "dev_mode": settings.DEV_MODE,
        },
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    """Página de auditoria."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="hub/audit.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    """Página de gestão de utilizadores."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="hub/users.html",
        context={"request": request, "username": payload.sub},
    )


# ─────────────────────────────────────────────────────────────────────────────
# SSE — Métricas em tempo real (polling Redis a cada 2-3s)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/metrics")
async def metrics_stream(request: Request):
    """
    Server-Sent Events: envia métricas do Redis a cada 2 segundos.
    """
    import asyncio, json
    from fastapi.responses import StreamingResponse

    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)

    async def gerador():
        import datetime
        from src.infrastructure.redis_client import get_redis_text
        
        while True:
            if await request.is_disconnected():
                break
            try:
                r = get_redis_text()
                # Leitura direta e segura do Redis para não depender de UseCases antigos
                mem_info = r.info("memory")
                ram_usada = mem_info.get("used_memory", 0) / 1024 / 1024
                
                dados = {
                    "ts": datetime.datetime.now().isoformat(),
                    "ram_mb": round(ram_usada, 1),
                    "status": "online"
                }
                yield f"data: {json.dumps(dados, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'erro': str(e)})}\n\n"

            await asyncio.sleep(2.5)

    return StreamingResponse(
        gerador(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

    

@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    """Página do Simulador de Chat Web."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    # Cria o session_id igual ao que você usa no chat/stream
    session_id = f"web_session_{payload.sub}"

    return templates.TemplateResponse(
        request=request,
        name="hub/chat.html",
        context={"request": request, "username": payload.sub,"session_id": session_id},
    )


def _sse_step(step: str, status: str, detail: str, elapsed: float = 0, extra: dict | None = None) -> str:
    import json as _json
    p = {"type": "step", "step": step, "status": status, "detail": detail, "ms": int(elapsed * 1000)}
    if extra:
        p.update(extra)
    return f"data: {_json.dumps(p, ensure_ascii=False)}\n\n"


@router.get("/chat/stream")
async def chat_stream(request: Request, msg: str = "", thread_id: str = ""):
    if not msg:
        return JSONResponse({"error": "Mensagem obrigatória"}, status_code=400)

    import time as _t

    async def _generator():
        t_total = _t.monotonic()
        try:
            # ── 0. HITL Interception ──────────────────────
            from src.infrastructure.redis_client import get_redis_text
            import json as _json
            r = get_redis_text()
            hitl_state_raw = r.get(f"hitl:session:{thread_id}")
            if hitl_state_raw:
                try:
                    hitl_state = _json.loads(hitl_state_raw if isinstance(hitl_state_raw, str) else hitl_state_raw.decode())
                    action = hitl_state.get("action")
                    msg_clean = msg.strip()
                    msg_lower = msg_clean.lower()
                    
                    if action == "sigaa_collect_cpf":
                        import re
                        cpf = re.sub(r"\D", "", msg_clean)
                        if len(cpf) != 11:
                            resp = {
                                'type': 'response',
                                'text': "❌ **CPF Inválido!**\nO CPF deve conter exatamente 11 dígitos numéricos. Por favor, informe seu CPF novamente:",
                                'rota': "SIGAA",
                                'total_ms': 10,
                                'action_buttons': [],
                                'status': 'hitl_pending'
                            }
                            yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                            return
                        # Avança para coletar senha
                        hitl_state["action"] = "sigaa_collect_password"
                        hitl_state["cpf"] = cpf
                        r.setex(f"hitl:session:{thread_id}", 300, _json.dumps(hitl_state, ensure_ascii=False))
                        resp = {
                            'type': 'response',
                            'text': "🔐 **CPF recebido!**\nAgora, por favor, envie sua **senha do SIGAA** para iniciarmos o acesso (sua senha é transmitida de forma segura e não será salva persistentemente):",
                            'rota': "SIGAA",
                            'total_ms': 10,
                            'action_buttons': [],
                            'status': 'hitl_pending'
                        }
                        yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                        return
                    
                    elif action == "sigaa_collect_password":
                        senha = msg_clean
                        cpf = hitl_state.get("cpf")
                        target_action = hitl_state.get("target_action")
                        event = hitl_state.get("event", {})
                        
                        event["login"] = cpf
                        event["senha"] = senha
                        event["hitl_confirmed"] = True
                        
                        r.delete(f"hitl:session:{thread_id}")
                        
                        yield _sse_step("router", "skip", "Autenticação em andamento")
                        yield _sse_step("planner", "skip", "HITL Validado")
                        yield _sse_step("dispatch", "running", f"Disparando worker {target_action}...")
                        t0 = _t.monotonic()
                        
                        from src.application.workers.registry import _autodiscover_workers, _REGISTRY
                        _autodiscover_workers()
                        fn = _REGISTRY.get(target_action)
                        if fn:
                            fn.s(event).apply_async()
                            yield _sse_step("dispatch", "ok", "Worker enfileirado", _t.monotonic() - t0)
                            yield _sse_step("synthesis", "running", "Autenticando e extraindo dados do SIGAA...")
                            t0 = _t.monotonic()
                            
                            from src.application.runtime.dispatcher import _aguardar_resposta_final
                            final_data = await _aguardar_resposta_final(event["plan_id"], timeout=15.0)
                            synth_ms = _t.monotonic() - t0
                            
                            if final_data is None:
                                answer = "⏳ A sua requisição continua sendo processada em background. Você será notificado quando terminar."
                                action_buttons = []
                                status = "warning"
                                yield _sse_step("synthesis", "warning", "Processamento em background", synth_ms)
                            else:
                                answer = final_data.get("answer", "")
                                action_buttons = final_data.get("action_buttons", [])
                                status = final_data.get("status", "ok")
                                yield _sse_step("synthesis", "ok", f"{len(answer)} chars gerados", synth_ms)
                            
                            total_ms = int((_t.monotonic() - t_total) * 1000)
                            response_payload = {
                                'type': 'response',
                                'text': answer,
                                'rota': "SIGAA",
                                'total_ms': total_ms,
                                'action_buttons': action_buttons,
                                'status': status
                            }
                            yield f"data: {_json.dumps(response_payload, ensure_ascii=False)}\n\n"
                            
                            metrics_payload = {
                                'type': 'metrics',
                                'rota': "SIGAA",
                                'total_ms': total_ms,
                                'workers': 1,
                                'confianca': 1.0
                            }
                            yield f"data: {_json.dumps(metrics_payload, ensure_ascii=False)}\n\n"
                            return
                        else:
                            resp = {'type': 'response', 'text': f"❌ Falha crítica: worker '{target_action}' não encontrado.", 'rota': "SIGAA", 'total_ms': 10, 'action_buttons': [], 'status': 'error'}
                            yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                            return

                    if msg_lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
                        r.delete(f"hitl:session:{thread_id}")
                        action = hitl_state.get("action")
                        
                        yield _sse_step("router", "skip", "HITL Interceptado")
                        yield _sse_step("planner", "skip", "HITL Interceptado")
                        
                        if action == "media_download":
                            from src.application.workers.registry import dispatch
                            yield _sse_step("dispatch", "ok", "Enviando worker de mídia...")
                            dispatch(hitl_state.get("worker_name"), hitl_state.get("event"))
                            
                            yield _sse_step("synthesis", "ok", "Download iniciado no background")
                            total_ms = int((_t.monotonic() - t_total) * 1000)
                            
                            resp = {'type': 'response', 'text': "✅ Download confirmado! Verifique os logs para acompanhar o progresso.", 'rota': "HITL", 'total_ms': total_ms, 'action_buttons': [], 'status': 'ok'}
                            yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                            yield f"data: {_json.dumps({'type': 'metrics', 'rota': 'HITL', 'total_ms': total_ms, 'workers': 1, 'confianca': 1.0}, ensure_ascii=False)}\n\n"
                            return
                        else:
                            resp = {'type': 'response', 'text': f"✅ Ação '{action}' confirmada.", 'rota': "HITL", 'total_ms': 10, 'action_buttons': [], 'status': 'ok'}
                            yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                            return
                            
                    elif msg_lower in ("nao", "não", "n", "no", "cancela", "cancelar"):
                        r.delete(f"hitl:session:{thread_id}")
                        resp = {'type': 'response', 'text': "❌ Ação cancelada.", 'rota': "HITL", 'total_ms': 10, 'action_buttons': [], 'status': 'ok'}
                        yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                        return
                    else:
                        resp = {'type': 'response', 'text': "⚠️ Responda *SIM* para confirmar ou *NÃO* para cancelar.", 'rota': "HITL", 'total_ms': 10, 'action_buttons': [], 'status': 'ok'}
                        yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                        return
                except Exception as e:
                    logger.error("Erro no parse do HITL state no hub: %s", e)
                    r.delete(f"hitl:session:{thread_id}")

            # ── 1. Router ─────────────────────────────────
            yield _sse_step("router", "running", "Classificando intenção…")
            t0 = _t.monotonic()
            from src.router.supervisor import rotear
            decision = await rotear(msg, thread_id, {"role": "admin"})
            yield _sse_step("router", "ok",
                f"→ {decision.rota} ({decision.confianca:.0%})",
                _t.monotonic() - t0,
                {"rota": decision.rota}
            )

            # ── Fast-Paths GREETING / MEDIA_DOWNLOAD / SIGAA ──────
            if decision.rota == "GREETING":
                import random
                answer = random.choice([
                    "Olá! 😊 Sou o Oráculo UEMA. Como posso ajudar?",
                    "Oi! Em que posso ajudá-lo(a) hoje?",
                    "Olá! Pode perguntar sobre calendário, editais, contatos ou suporte. 🎓",
                ])
                # Salva turno de saudação imediata no simulador
                try:
                    from src.memory.container import create_memory_service
                    mem_svc = create_memory_service()
                    mem_svc.persistir_turno(
                        session_id=thread_id,
                        user_id=thread_id,
                        pergunta=msg,
                        resposta=answer,
                        rota=decision.rota
                    )
                except Exception:
                    pass

                yield _sse_step("planner", "skip", "Fast-Path (Inline)")
                yield _sse_step("dispatch", "skip", "Bypass Celery")
                yield _sse_step("synthesis", "ok", "Resposta instantânea")
                
                total_ms = int((_t.monotonic() - t_total) * 1000)
                resp = {'type': 'response', 'text': answer, 'rota': decision.rota, 'total_ms': total_ms, 'action_buttons': [], 'status': 'ok'}
                yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                
                metrics = {'type': 'metrics', 'rota': decision.rota, 'total_ms': total_ms, 'workers': 0, 'confianca': round(decision.confianca, 2)}
                yield f"data: {_json.dumps(metrics, ensure_ascii=False)}\n\n"
                return

            if decision.rota == "MEDIA_DOWNLOAD":
                url = decision.dag_hint.get("url", msg)
                hitl_state = {
                    "action": "media_download",
                    "worker_name": decision.dag_hint["steps"][0],
                    "event": {
                        "plan_id": "fast_media",
                        "session_id": thread_id,
                        "step_id": "s1",
                        "url": url,
                        "hitl_confirmed": True,
                    }
                }
                r.setex(f"hitl:session:{thread_id}", 300, _json.dumps(hitl_state, ensure_ascii=False))

                answer = "🎥 **Mídia detectada!**\n\nIdentifiquei um link suportado.\nDeseja iniciar o download deste arquivo agora?"
                btns = [{"label": "Sim, baixar", "value": "sim"}, {"label": "Não", "value": "nao"}]

                # Salva turno de mídia imediato no simulador
                try:
                    from src.memory.container import create_memory_service
                    mem_svc = create_memory_service()
                    mem_svc.persistir_turno(
                        session_id=thread_id,
                        user_id=thread_id,
                        pergunta=msg,
                        resposta=answer,
                        rota=decision.rota
                    )
                except Exception:
                    pass

                yield _sse_step("planner", "skip", "Fast-Path HITL")
                yield _sse_step("dispatch", "skip", "Bypass Celery")
                yield _sse_step("synthesis", "ok", "Ação Manual Requerida")
                
                total_ms = int((_t.monotonic() - t_total) * 1000)
                resp = {'type': 'response', 'text': answer, 'rota': decision.rota, 'total_ms': total_ms, 'action_buttons': btns, 'status': 'hitl_pending'}
                yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                
                metrics = {'type': 'metrics', 'rota': decision.rota, 'total_ms': total_ms, 'workers': 0, 'confianca': round(decision.confianca, 2)}
                yield f"data: {_json.dumps(metrics, ensure_ascii=False)}\n\n"
                return

            if decision.rota == "SIGAA":
                from src.application.use_cases.sigaa_use_cases import SIGAAUseCase
                uc = SIGAAUseCase()
                fluxo = uc.detectar_fluxo(msg)
                worker = fluxo["worker"] if fluxo else "sigaa_biblioteca"
                args = fluxo["args"] if fluxo else {}
                
                session_key = f"sigaa:session:{thread_id}"
                if r.exists(session_key):
                    yield _sse_step("dispatch", "running", f"Disparando {worker} usando sessão existente...")
                    t0 = _t.monotonic()
                    
                    from src.application.workers.registry import _autodiscover_workers, _REGISTRY
                    _autodiscover_workers()
                    fn = _REGISTRY.get(worker)
                    if fn:
                        plan_id = f"fast_{worker}_{int(_t.time())}"
                        event = {
                            "plan_id": plan_id,
                            "session_id": thread_id,
                            "step_id": "s1",
                            "query": msg,
                            **args
                        }
                        fn.s(event).apply_async()
                        
                        yield _sse_step("dispatch", "ok", "Worker enfileirado", _t.monotonic() - t0)
                        yield _sse_step("synthesis", "running", "Aguardando resposta do SIGAA...")
                        t0 = _t.monotonic()
                        
                        from src.application.runtime.dispatcher import _aguardar_resposta_final
                        final_data = await _aguardar_resposta_final(plan_id, timeout=15.0)
                        synth_ms = _t.monotonic() - t0
                        
                        if final_data is None:
                            answer = "⏳ A sua requisição continua sendo processada em background. Você será notificado quando terminar."
                            action_buttons = []
                            status = "warning"
                            yield _sse_step("synthesis", "warning", "Processamento em background", synth_ms)
                        else:
                            answer = final_data.get("answer", "")
                            action_buttons = final_data.get("action_buttons", [])
                            status = final_data.get("status", "ok")
                            yield _sse_step("synthesis", "ok", f"{len(answer)} chars gerados", synth_ms)
                        
                        total_ms = int((_t.monotonic() - t_total) * 1000)
                        response_payload = {
                            'type': 'response',
                            'text': answer,
                            'rota': decision.rota,
                            'total_ms': total_ms,
                            'action_buttons': action_buttons,
                            'status': status
                        }
                        yield f"data: {_json.dumps(response_payload, ensure_ascii=False)}\n\n"
                        return

                friendly_names = {
                    "sigaa_notas": "Consultar Minhas Notas",
                    "sigaa_indice": "Consultar Índice Acadêmico (CR)",
                    "sigaa_historico": "Emitir Histórico Escolar",
                    "sigaa_estrutura": "Consultar Estrutura Curricular",
                    "sigaa_turmas": "Consultar Turmas do Semestre",
                    "sigaa_calendario": "Consultar Calendário Acadêmico",
                    "sigaa_extensao": "Realizar Inscrição em Evento de Extensão",
                    "sigaa_biblioteca": "Consultar Acervo da Biblioteca",
                    "sigaa_processos": "Consultar Processos Seletivos",
                }
                op_desc = friendly_names.get(worker, "Acessar o Portal SIGAA")
                
                hitl_state = {
                    "action": "sigaa_collect_cpf",
                    "target_action": worker,
                    "description": op_desc,
                    "event": {
                        "plan_id": f"fast_{worker}_{int(_t.time())}",
                        "session_id": thread_id,
                        "step_id": "s1",
                        "query": msg,
                        **args
                    }
                }
                r.setex(f"hitl:session:{thread_id}", 300, _json.dumps(hitl_state, ensure_ascii=False))
                
                yield _sse_step("planner", "skip", "Coleta de Credenciais")
                yield _sse_step("dispatch", "skip", "Bypass Celery")
                yield _sse_step("synthesis", "ok", "Identificação Requerida")
                
                total_ms = int((_t.monotonic() - t_total) * 1000)
                answer = f"⚠️ **Autenticação Requerida**\n\nPara executar a operação **{op_desc}**, preciso que você se autentique no SIGAA.\n\nPor favor, informe seu **CPF** (apenas números, sem pontos ou traços):"
                resp = {
                    'type': 'response',
                    'text': answer,
                    'rota': decision.rota,
                    'total_ms': total_ms,
                    'action_buttons': [],
                    'status': 'hitl_pending'
                }
                yield f"data: {_json.dumps(resp, ensure_ascii=False)}\n\n"
                
                metrics = {
                    'type': 'metrics',
                    'rota': decision.rota,
                    'total_ms': total_ms,
                    'workers': 0,
                    'confianca': round(decision.confianca, 2)
                }
                yield f"data: {_json.dumps(metrics, ensure_ascii=False)}\n\n"
                return

            # ── 2. Planner ────────────────────────────────
            yield _sse_step("planner", "running", "Gerando plano DAG…")
            t0 = _t.monotonic()
            
            # Carrega memória do usuário para planejamento
            from src.memory.container import create_memory_service
            mem_svc = create_memory_service()
            mem_ctx = mem_svc.carregar_contexto(user_id=thread_id, session_id=thread_id, query=msg)

            from src.application.chain.planner import criar_plano
            plan = await criar_plano(
                query=msg, session_id=thread_id, rota=decision.rota,
                dag_hint=decision.dag_hint,
                user_context={"role": "admin", "nome": "Admin"},
                history=mem_ctx.historico.texto_formatado if mem_ctx.historico else "",
                fatos=[f.texto for f in mem_ctx.fatos] if mem_ctx.fatos else [],
            )
            workers_str = " → ".join(s["worker"] for s in plan.steps)
            yield _sse_step("planner", "ok", workers_str,
                _t.monotonic() - t0,
                {"plan_id": plan.plan_id[:8]}
            )

            # ── 3. Dispatch ───────────────────────────────
            yield _sse_step("dispatch", "running", f"Despachando {len(plan.steps)} worker(s)…")
            t0 = _t.monotonic()
            from src.application.runtime.dispatcher import _despachar_workers, _aguardar_resposta_final
            await _despachar_workers(plan)
            yield _sse_step("dispatch", "ok", "Workers enfileirados (Celery)", _t.monotonic() - t0)

            # ── 4. Synthesis ──────────────────────────────
            yield _sse_step("synthesis", "running", "Aguardando resposta dos workers…")
            t0 = _t.monotonic()
            final_data = await _aguardar_resposta_final(plan.plan_id, timeout=15.0)
            synth_ms = _t.monotonic() - t0

            if final_data is None:
                answer = "⏳ A sua requisição continua sendo processada em background. Você será notificado quando terminar."
                action_buttons = []
                status = "warning"
                yield _sse_step("synthesis", "warning", "Processamento em background", synth_ms)
            else:
                answer = final_data.get("answer", "")
                action_buttons = final_data.get("action_buttons", [])
                status = final_data.get("status", "ok")
                yield _sse_step("synthesis", "ok", f"{len(answer)} chars gerados", synth_ms)
                
                # Persiste turno de resposta assíncrona gerada
                if status == "ok" and answer:
                    try:
                        mem_svc.persistir_turno(
                            session_id=thread_id,
                            user_id=thread_id,
                            pergunta=msg,
                            resposta=answer,
                            rota=decision.rota
                        )
                        mem_svc.extrair_fatos_background(user_id=thread_id, session_id=thread_id)
                    except Exception as e:
                        logger.warning("⚠️ Falha ao salvar turno assíncrono no hub: %s", e)

            total_ms = int((_t.monotonic() - t_total) * 1000)

            response_payload = {
                'type': 'response',
                'text': answer,
                'rota': decision.rota,
                'total_ms': total_ms,
                'action_buttons': action_buttons,
                'status': status
            }
            yield f"data: {json.dumps(response_payload, ensure_ascii=False)}\n\n"
            
            metrics_payload = {
                'type': 'metrics',
                'rota': decision.rota,
                'total_ms': total_ms,
                'workers': len(plan.steps),
                'confianca': round(decision.confianca, 2)
            }
            yield f"data: {json.dumps(metrics_payload, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.exception("SSE /chat/stream error: %s", e)
            yield f"data: {json.dumps({'type':'error','msg':str(e)[:200]})}\n\n"
        finally:
            yield f"data: {json.dumps({'type':'done'})}\n\n"

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
            
# Adicionar em src/api/hub.py

from pydantic import BaseModel

class ChunkSimulateRequest(BaseModel):
    text:          str
    chunk_size:    int = 400
    chunk_overlap: int = 60
    strategy:      str = "recursive"  # "recursive" | "markdown" | "semantic"

class ChunkResult(BaseModel):
    index:      int
    text:       str
    start_char: int
    end_char:   int
    length:     int
    is_overlap: bool  # True se este chunk começa dentro do overlap do anterior


@router.post("/api/simulate-chunking")
async def simulate_chunking(
    request: Request,
    body:    ChunkSimulateRequest,
):
    """
    Simula o chunking sem salvar no banco.
    Retorna lista com posições exatas para o frontend pintar os overlaps.
    """
    payload = _verificar_cookie(request)
    if not payload:
        return JSONResponse({"error": "Não autorizado"}, status_code=401)

    if len(body.text) > 50_000:
        return JSONResponse({"error": "Texto muito grande (máx 50.000 chars)"}, status_code=400)

    try:
        from src.rag.ingestion.chunker_factory import ChunkerFactory
        chunker = ChunkerFactory.get(
            body.strategy,
            chunk_size=body.chunk_size,
            overlap=body.chunk_overlap,
        )
        raw_chunks = chunker.chunk(body.text, source="preview", doc_type="geral")

        # Calcula posições reais no texto original para o highlight
        results    = []
        prev_end   = 0

        for i, chunk in enumerate(raw_chunks):
            # Localiza o início do chunk no texto original
            start = body.text.find(chunk.text[:50].strip(), max(0, prev_end - body.chunk_overlap))
            if start == -1:
                start = prev_end   # fallback
            end       = start + len(chunk.text)
            is_overlap= (i > 0) and (start < prev_end)

            results.append({
                "index":      i,
                "text":       chunk.text,
                "start_char": start,
                "end_char":   end,
                "length":     len(chunk.text),
                "is_overlap": is_overlap,
            })
            prev_end = end

        return JSONResponse({
            "chunks":           results,
            "total":            len(results),
            "total_chars":      len(body.text),
            "avg_chunk_size":   int(sum(r["length"] for r in results) / max(len(results), 1)),
            "strategy_used":    body.strategy,
        })

    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("❌ simulate-chunking: %s", e)
        return JSONResponse({"error": "Erro interno"}, status_code=500)
    
    
@router.get("/chunkviz", response_class=HTMLResponse)
async def chunkviz_page(request: Request):
    """Serve a página HTML do Simulador."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="hub/chunkviz.html", # Verifique se o arquivo está nesta pasta
        context={
            "request": request, 
            "username": payload.sub,
            "modelo": settings.GEMINI_MODEL
        },
    )
@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/config.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    """Serve a página do catálogo de agentes (Agent Registry)."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/agents.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/agents/data")
async def agents_data(request: Request):
    """Endpoint REST para alimentar o catálogo de agentes."""
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}

    from src.agents.registry import registry
    from src.capabilities.persistence.agent_config import status_de_todos
    from src.infrastructure.redis_client import get_redis_text
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.agent_catalog_repository import AgentCatalogRepository

    agentes = registry.all()
    status = await status_de_todos(get_redis_text(), [a.name for a in agentes])

    catalogo: dict[str, dict] = {}
    try:
        async with AsyncSessionLocal() as session:
            catalogo = {row["nome"]: row for row in await AgentCatalogRepository(session).listar()}
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler catálogo Postgres de agentes: %s", exc)

    return {
        "agentes": [
            {
                "name": a.name,
                "description": catalogo.get(a.name, {}).get("descricao") or a.description,
                "permissions": a.permissions,
                "enabled": status[a.name],
                "atualizado_em": (
                    catalogo.get(a.name, {}).get("atualizado_em").isoformat()
                    if catalogo.get(a.name, {}).get("atualizado_em") else None
                ),
                "atualizado_por": catalogo.get(a.name, {}).get("atualizado_por"),
            }
            for a in agentes
        ]
    }


class AgentToggleRequest(BaseModel):
    enabled: bool


@router.post("/agents/{name}/toggle")
async def agents_toggle(request: Request, name: str, data: AgentToggleRequest):
    """Liga/desliga um agente (admin:agent:{nome}:enabled no Redis)."""
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}

    from src.agents.registry import registry
    from src.capabilities.persistence.agent_config import set_agent_enabled
    from src.infrastructure.redis_client import get_redis_text

    try:
        registry.resolve(name)
    except KeyError:
        return {"error": f"Agente '{name}' não encontrado."}

    await set_agent_enabled(get_redis_text(), name, data.enabled, admin=payload.sub)
    return {"name": name, "enabled": data.enabled}


class AgentDescricaoRequest(BaseModel):
    descricao: str


@router.post("/agents/{name}/descricao")
async def agents_set_descricao(request: Request, name: str, data: AgentDescricaoRequest):
    """Edita a descrição administrável do agente no catálogo Postgres (Sprint 2, Fase 9)."""
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}

    from src.agents.registry import registry
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.agent_catalog_repository import AgentCatalogRepository

    try:
        registry.resolve(name)
    except KeyError:
        return {"error": f"Agente '{name}' não encontrado."}

    try:
        async with AsyncSessionLocal() as session:
            await AgentCatalogRepository(session).atualizar_descricao(name, data.descricao, admin=payload.sub)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao atualizar descrição de '%s': %s", name, exc)
        return {"error": "Falha ao gravar no Postgres. Tente novamente."}

    return {"name": name, "descricao": data.descricao}


@router.get("/agents/{name}/prompt", response_class=HTMLResponse)
async def agent_prompt_page(request: Request, name: str):
    """Serve a página de edição/histórico de prompt de um agente."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/agent_prompt.html",
        context={"request": request, "username": payload.sub, "agent_name": name},
    )


@router.get("/agents/{name}/prompt/data")
async def agent_prompt_data(request: Request, name: str):
    """Prompt ativo (Postgres/Redis legado/hardcoded) + histórico de versões."""
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}

    from src.agents.registry import registry
    from src.capabilities.persistence.prompt_config import historico, obter_prompt_ativo
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.redis_client import get_redis_text

    try:
        agente = registry.resolve(name)
    except KeyError:
        return {"error": f"Agente '{name}' não encontrado."}

    fallback = "(este agente não tem prompt de LLM próprio)"
    if name == "academic_knowledge":
        from src.agents.academic_knowledge.prompts import SYSTEM_SYNTHESIS
        fallback = SYSTEM_SYNTHESIS
    try:
        async with AsyncSessionLocal() as session:
            prompt_ativo = await obter_prompt_ativo(session, name, fallback=fallback, redis=get_redis_text())
            versoes = await historico(session, name)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler prompt de '%s': %s", name, exc)
        return {"error": "Falha ao ler o Postgres. Tente novamente."}

    return {
        "name": name,
        "prompt_ativo": prompt_ativo,
        "historico": [
            {
                "version": v["version"],
                "active": v["active"],
                "created_by": v["created_by"],
                "created_at": v["created_at"].isoformat() if hasattr(v["created_at"], "isoformat") else v["created_at"],
                "preview": v["prompt_text"][:200],
            }
            for v in versoes
        ],
    }


class AgentPromptRequest(BaseModel):
    prompt: str


@router.post("/agents/{name}/prompt")
async def agent_prompt_publicar(request: Request, name: str, data: AgentPromptRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}

    from src.agents.registry import registry
    from src.capabilities.persistence.prompt_config import publicar_novo_prompt
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        registry.resolve(name)
    except KeyError:
        return {"error": f"Agente '{name}' não encontrado."}

    if len(data.prompt.strip()) < 20:
        return {"error": "Prompt muito curto (mínimo 20 caracteres)."}

    try:
        async with AsyncSessionLocal() as session:
            nova = await publicar_novo_prompt(session, name, data.prompt, created_by=payload.sub)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao publicar prompt de '%s': %s", name, exc)
        return {"error": "Falha ao gravar no Postgres. Tente novamente."}

    return {"name": name, "version": nova.version}


@router.post("/agents/{name}/prompt/reset")
async def agent_prompt_resetar(request: Request, name: str):
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}

    from src.agents.registry import registry
    from src.capabilities.persistence.prompt_config import resetar_para_padrao
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        registry.resolve(name)
    except KeyError:
        return {"error": f"Agente '{name}' não encontrado."}

    try:
        async with AsyncSessionLocal() as session:
            await resetar_para_padrao(session, name, created_by=payload.sub)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao resetar prompt de '%s': %s", name, exc)
        return {"error": "Falha ao gravar no Postgres. Tente novamente."}

    return {"name": name, "reset": True}


@router.get("/capabilities", response_class=HTMLResponse)
async def capabilities_page(request: Request):
    """Serve a página somente-leitura do catálogo de capabilities/tools."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/capabilities.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/capabilities/data")
async def capabilities_data(request: Request):
    """Lista as capabilities/tools registradas (autodiscovery de capabilities/registry.py)."""
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}

    from src.capabilities.registry import available

    # Nenhuma das tools existentes tem consumidor vivo em produção hoje (ver
    # débito técnico documentado em capabilities/tools/__init__.py) — o hub
    # avisa isso explicitamente em vez de sugerir que são operacionais.
    return {
        "tools": [
            {"name": nome, "sem_consumidor_producao": True}
            for nome in available()
        ]
    }


@router.get("/eval", response_class=HTMLResponse)
async def eval_page(request: Request):
    """Serve a página HTML do Dashboard de Avaliação."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/eval.html",
        context={"request": request, "username": payload.sub, "modelo": settings.GEMINI_MODEL},
    )
# ─────────────────────────────────────────────────────────────────────────────
# Endpoints Integrados do ChunkViz (Controller)
# ─────────────────────────────────────────────────────────────────────────────
from src.api.routers.tools.chunkviz_tools import (
    save_temp_file, load_temp_meta, extract_document_pages, simulate_chunks_logic,TEMP_DIR
)
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from typing import Optional
import os
import hashlib



@router.post("/chunkviz/upload")
async def cv_upload(
    request: Request,
    file: UploadFile = File(...),
    parser: str = Form("auto"),
):
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")

    try:
        content = await file.read()
        meta = save_temp_file(file.filename, content, parser)
        pages, full_text = extract_document_pages(meta["path"], meta["ext"], parser)
        
        return {
            "file_id":    meta["file_id"],
            "name":       file.filename,
            "ext":        meta["ext"],
            "size_kb":    meta["size_kb"],
            "page_count": len(pages),
            "pages": [{"index": i, "preview": p[:80], "length": len(p)} for i, p in enumerate(pages)],
            "first_text": pages[0] if pages else full_text[:8000],
            "total_chars": len(full_text),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Upload fail")
        raise HTTPException(500, f"Erro: {str(e)[:200]}")

@router.post("/chunkviz/page")
async def cv_get_page(
    request: Request,
    file_id: str = Form(...),
    page: int = Form(0),
):
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        meta = load_temp_meta(file_id)
        pages, full_text = extract_document_pages(meta["path"], meta["ext"], meta["parser"])
        
        if page == -1:
            return {"page": -1, "text": full_text, "total_pages": len(pages)}
        if page < 0 or page >= len(pages):
            raise HTTPException(400, f"Página {page} inexistente.")
            
        return {"page": page, "text": pages[page], "total_pages": len(pages)}
    except FileNotFoundError:
        raise HTTPException(404, "Arquivo não encontrado")
    except Exception as e:
        raise HTTPException(500, str(e))

class SimReq(BaseModel):
    text:     str
    size:     int  = 400
    overlap:  int  = 60
    strategy: str  = "recursive"
    doc_type: str  = "geral"
    file_id:  Optional[str] = None

@router.post("/chunkviz/simulate")
async def cv_simulate(request: Request, body: SimReq):
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    if not body.text.strip():
        raise HTTPException(400, "Texto vazio")
        
    try:
        result = simulate_chunks_logic(body.text, body.size, body.overlap, body.strategy)
        return result
    except Exception as e:
        logger.exception("simulate error")
        raise HTTPException(500, f"Erro no chunking: {str(e)[:200]}")

class IngestReq(BaseModel):
    file_id:  str
    size:     int  = 400
    overlap:  int  = 60
    strategy: str  = "recursive"
    doc_type: str  = "geral"
    label:    str  = ""
    source:   str  = ""
    parser:   str  = "auto"
    # AQUI está a mágica: pega as tags enviadas pelo JavaScript do ChunkViz
    metadata_override: Dict[str, Any] = Field(default_factory=dict)

@router.post("/chunkviz/ingest")
async def cv_ingest(request: Request, body: IngestReq):
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        from src.api.routers.tools.chunkviz_tools import load_temp_meta
        meta   = load_temp_meta(body.file_id)
        source = body.source or meta.get("name", body.file_id)
        label  = body.label or os.path.splitext(source)[0].upper().replace("-"," ").replace("_"," ")

        from src.application.tasks.ingestion_tasks import processar_documento
        
        # Junta o doc_type básico com a nova Taxonomia (Eixo, Setor, Ano)
        final_metadata = {"doc_type": body.doc_type}
        if body.metadata_override:
            final_metadata.update(body.metadata_override)

        # Usamos os valores dinâmicos do 'body', que vieram do slider do HTML!
        result = processar_documento.apply_async(
            args=[meta["path"]],
            kwargs={
                "strategy_params": {
                    "size":     body.size,     
                    "overlap":  body.overlap,
                    "strategy": body.strategy, 
                    "doc_type": body.doc_type,
                    "label":    label,         
                    "parser":   body.parser or meta.get("parser","auto"),
                    # Repassamos as tags organizadas para o Celery
                    "metadata_override": final_metadata 
                },
                "chat_id": "",
            },
            queue="admin",
        )
        # Retorna sucesso (e a linha solta do 'delay' foi apagada!)
        return {"ok": True, "task_id": result.id, "source": source}
    
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Erro crítico no cv_ingest")
        raise HTTPException(500, f"Erro ao enfileirar: {str(e)[:200]}")

@router.get("/chunkviz/task/{task_id}")
async def cv_task_status(request: Request, task_id: str):
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        from src.infrastructure.celery_app import celery_app
        r = celery_app.AsyncResult(task_id)
        if r.state == "SUCCESS": return {"state":"SUCCESS","result": r.result}
        if r.state == "FAILURE": return {"state":"FAILURE","error":  str(r.info)}
        return {"state": r.state}
    except Exception as e:
        raise HTTPException(500, str(e))
    
# Coloque isso junto com os outros @router.post("/chunkviz/...") no seu hub.py
@router.post("/chunkviz/extract-url")
async def cv_extract_url(
    request: Request,
    url: str = Form(...),
):
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        from src.infrastructure.scraping.implementations.generic_scraper import GenericHTTPScraper
        from src.infrastructure.scraping.base_scraper import ScrapeRequest

        result = await GenericHTTPScraper().scrape(ScrapeRequest(url=url, doc_type="web"))
        if not result.ok or not result.document:
            raise HTTPException(500, f"Scraping falhou: {result.error}")

        doc = result.document
        file_id   = hashlib.md5(url.encode()).hexdigest()[:16]
        file_path = os.path.join(TEMP_DIR, f"{file_id}.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(doc.content)

        meta = {
            "file_id": file_id, "name": url, "ext": ".txt",
            "size_kb": len(doc.content)//1024, "path": file_path, "parser": "txt",
        }
        # Chama a função lá do tools pra salvar o JSON
        save_temp_file(file_id, str(meta).encode(), "txt") # Só pra constar a criação

        return {
            "file_id":    file_id,
            "title":      doc.title,
            "text":       doc.content[:10000],
            "total_chars": len(doc.content),
            "word_count": doc.word_count,
        }
    except Exception as e:
        logger.exception("Scraping fail")
        raise HTTPException(500, f"Erro no scraping: {str(e)[:200]}")
    
    
# ─────────────────────────────────────────────────────────────────────────────
# Endpoints do Eval (Avaliação RAG Interativa)
# Integrados no Hub Controller
# ─────────────────────────────────────────────────────────────────────────────

from src.api.routers.admin.eval_api import (
    EVAL_DATASET, _evaluate_single, _aggregate_results, _persist_eval_result, asdict, AsyncIterator
)
import asyncio
import json

@router.get("/eval/dataset")
async def get_dataset(request: Request):
    """Retorna o dataset de avaliação."""
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    return JSONResponse({"dataset": EVAL_DATASET, "total": len(EVAL_DATASET)})

@router.post("/eval/single")
async def eval_single(request: Request):
    """Avalia uma única pergunta. Rápido para o botão 'Testar'."""
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        body = await request.json()
        question = body.get("question", "").strip()
        if not question:
            return JSONResponse({"error": "question obrigatório"}, status_code=400)

        # Cria item sintético
        item = {
            "id":       "custom",
            "category": "CUSTOM",
            "question": question,
            "keywords": question.split()[:5],
            "expected_source": None,
        }
        result = await _evaluate_single(item, session_id="eval_single")
        return JSONResponse(asdict(result))

    except Exception as e:
        logger.exception("❌ [EVAL] /single falhou: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# Fila global de progresso para SSE da Avaliação
_eval_progress_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
_eval_running = False

@router.post("/eval/run")
async def eval_run(request: Request):
    """
    Inicia avaliação completa em background.
    Progresso disponível via GET /eval/stream (SSE).
    """
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    global _eval_running
    if _eval_running:
        return JSONResponse({"error": "Avaliação já em andamento"}, status_code=409)

    try:
        body = await request.json()
        ids = body.get("ids", None)   # None = todos
    except Exception:
        ids = None

    dataset = EVAL_DATASET
    if ids:
        dataset = [d for d in EVAL_DATASET if d["id"] in ids]

    # Executa em background task
    asyncio.create_task(_run_eval_background(dataset))

    return JSONResponse({
        "ok":    True,
        "total": len(dataset),
        "msg":   "Avaliação iniciada. Acompanhe em /eval/stream"
    })

async def _run_eval_background(dataset: list[dict]) -> None:
    """Função background que processa o EvalRun"""
    global _eval_running
    _eval_running = True
    results = []

    await _eval_progress_queue.put(json.dumps({
        "type": "start", "total": len(dataset)
    }))

    for i, item in enumerate(dataset):
        await _eval_progress_queue.put(json.dumps({
            "type":     "progress",
            "current":  i + 1,
            "total":    len(dataset),
            "question": item["question"][:60],
        }))

        result = await _evaluate_single(item)
        results.append(result)

        await _eval_progress_queue.put(json.dumps({
            "type":       "result",
            "id":         result.id,
            "category":   result.category,
            "question":   result.question,
            "answer":     result.answer,
            "route_detected": result.route_detected,
            "hit_rate":   result.hit_rate,
            "mrr":        result.mrr,
            "crag":       result.crag_score,
            "faithfulness": result.faithfulness,
            "relevancy":  result.answer_relevancy,
            "latency_ms": result.latency_ms,
            "tokens_entrada": result.tokens_entrada,
            "tokens_saida":   result.tokens_saida,
            "tokens_total":   result.tokens_total,
            "cost_usd":       result.cost_usd,
            "memory_mb":      result.memory_mb,
            "worker_name":    result.worker_name,
            "error":      result.error,
        }))

        # Pequena pausa entre perguntas para não saturar a API
        await asyncio.sleep(0.5)

    # Calcula e salva agregado
    run_result = _aggregate_results(results)
    _persist_eval_result(run_result)

    await _eval_progress_queue.put(json.dumps({
        "type":       "done",
        "run_id":     run_result.run_id,
        "avg_hit":    run_result.avg_hit_rate,
        "avg_mrr":    run_result.avg_mrr,
        "avg_crag":   run_result.avg_crag,
        "avg_faith":  run_result.avg_faithfulness,
        "avg_relev":  run_result.avg_relevancy,
        "avg_lat_ms": run_result.avg_latency_ms,
        "avg_tokens_in":  run_result.avg_tokens_entrada,
        "avg_tokens_out": run_result.avg_tokens_saida,
        "avg_tokens_tot": run_result.avg_tokens_total,
        "avg_cost":       run_result.avg_cost_usd,
        "avg_memory":     run_result.avg_memory_mb,
    }))

    _eval_running = False

@router.get("/eval/stream")
async def eval_stream(request: Request):
    """SSE: progresso da avaliação em tempo real."""
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")

    async def generator() -> AsyncIterator[str]:
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(_eval_progress_queue.get(), timeout=15.0)
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.get("/eval/results")
async def eval_results(request: Request):
    """Retorna os últimos resultados de avaliação."""
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        raw = r.lrange("eval:results", 0, 4)
        results = [json.loads(item) for item in raw]
        return JSONResponse({"results": results})
    except Exception as e:
        return JSONResponse({"results": [], "error": str(e)})

@router.post("/eval/query")
async def eval_query(request: Request):
    """
    SSE: executa UMA pergunta e emite eventos de cada step em tempo real.
    Consumido pelo pipeline view do dashboard.
    """
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        body = await request.json()
        pergunta = body.get("pergunta", "").strip()
        session_id = body.get("session_id", "eval_live")
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)

    if not pergunta:
        return JSONResponse({"error": "pergunta obrigatória"}, status_code=400)

    queue: asyncio.Queue = asyncio.Queue()

    async def _run():
        from src.application.runtime.dispatcher import processar
        
        await queue.put(json.dumps({
            "tipo": "step_start", "step": "routing"
        }))

        result = await processar(
            message=pergunta,
            session_id=session_id,
            user_context={"nome": "Admin Live", "role": "admin"},
            history=""
        )

        await queue.put(json.dumps({
            "tipo": "resposta",
            "texto": result.answer,
            "fonte": getattr(result, "rota", "GERAL"),
            "tokens": getattr(result, "tokens_used", 0),
        }))

        await queue.put(json.dumps({"tipo": "done"}))

        step_queue: asyncio.Queue = asyncio.Queue()

        async def forward_steps():
            while True:
                step = await step_queue.get()
                if step.name == "DONE":
                    break
                frontend_id = _STEP_MAP.get(step.name)
                if frontend_id is None:
                    continue

                if step.status == "running":
                    await queue.put(json.dumps({
                        "tipo": "step_start", "step": frontend_id
                    }))
                elif step.status in ("ok", "skip", "error"):
                    badge = "ok" if step.status == "ok" else step.status
                    if step.name == "transform_query" and step.status == "ok":
                        badge = "transformed"
                    await queue.put(json.dumps({
                        "tipo": "step_result", "step": frontend_id,
                        "resultado": step.detail[:100],
                        "badge": badge,
                        "ms": step.latency_ms,
                    }))

                    if step.name == "retrieve" and step.data:
                        for chunk in step.data.get("chunks_preview", []):
                            await queue.put(json.dumps({
                                "tipo": "chunk_rag",
                                "source": chunk.get("source", ""),
                                "score": round(chunk.get("rrf_score", 0), 4),
                                "preview": chunk.get("content", "")[:150],
                            }))

        forwarder = asyncio.create_task(forward_steps())

        result = await chain.invoke(
            message=pergunta,
            session_id=session_id,
            user_context={"nome": "Admin Live", "role": "admin"},
            debug_queue=step_queue,
        )

        await forwarder

        await queue.put(json.dumps({
            "tipo": "resposta",
            "texto": result.answer,
            "fonte": result.route,
            "tokens": result.tokens_used,
        }))

        await queue.put(json.dumps({
            "tipo": "metricas",
            "rota": result.route,
            "crag_score": round(result.crag_score, 3),
            "tokens_total": result.tokens_used,
            "tokens_entrada": 0,
            "tokens_saida": result.tokens_used,
            "latencia_ms": result.total_ms,
        }))

        await queue.put(json.dumps({"tipo": "done"}))

    asyncio.create_task(_run())

    async def generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=45.0)
                yield f"data: {msg}\n\n"
                if '"tipo": "done"' in msg:
                    break
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'tipo': 'ping'})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.get("/eval/eventos")
async def eval_eventos(request: Request):
    """Retorna eventos dos próximos 30 dias para o widget de calendário."""
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    try:
        from src.rag.calendar_parser import buscar_eventos_proximos
        eventos = buscar_eventos_proximos(dias_frente=30)
        return JSONResponse({
            "eventos": [
                {
                    "nome": e.nome,
                    "data_inicio": e.data_inicio.strftime("%d/%m/%Y"),
                    "data_fim": e.data_fim.strftime("%d/%m/%Y") if e.data_fim else None,
                    "dias_restantes": e.dias_restantes,
                    "categoria": e.categoria,
                    "emoji": e.emoji,
                }
                for e in eventos
            ]
        })
    except Exception as e:
        logger.exception("❌ [EVAL] /eventos: %s", e)
        return JSONResponse({"eventos": [], "error": str(e)})

@router.post("/eval/run-full")
async def eval_run_full(request: Request):
    """Alias de /run para compatibilidade com o frontend."""
    payload = _verificar_cookie(request)
    if not payload:
        raise HTTPException(status_code=401, detail="Não autorizado")
    return await eval_run(request)