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


def _nao_autorizado() -> JSONResponse:
    """Resposta padrão para request sem cookie admin válido nas rotas JSON.
    HTTP 401 (não 200) — o front continua tratando via `d.error`, mas agora
    métricas/observabilidade veem o status certo."""
    return JSONResponse({"error": "Não autorizado"}, status_code=401)


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
        from urllib.parse import quote
        return RedirectResponse(
            f"/hub/login?erro={quote(result.erro or 'Falha no login')}",
            status_code=302,
        )

    response = RedirectResponse("/hub/", status_code=302)
    response.set_cookie(
        key="admin_token",
        value=result.access_token,
        max_age=result.expires_in,
        httponly=True,
        samesite="lax",
        secure=not settings.DEV_MODE,  # Secure em produção (HTTPS), off em dev (HTTP)
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

    from src.infrastructure import dynamic_config
    return templates.TemplateResponse(
        request=request,
        name="hub/index.html",
        context={
            "request":  request,
            "username": payload.sub,
            "modelo":   dynamic_config.get_str("GEMINI_MODEL"),  # config dinâmica (Fase 1)
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
        import json as _json
        t_total = _t.monotonic()

        # Lock por sessão — sem isso, um EventSource que reconecta sozinho
        # (browser, em qualquer soluço de rede) reprocessa a mesma mensagem
        # do zero, pagando Router+Orchestrator+Planner de novo. Mesmo padrão
        # de `lock:msg:{phone}` do WhatsApp (process_message_task.py), que
        # este endpoint nunca teve.
        lock_key = f"lock:hub_chat:{thread_id}" if thread_id else None
        r_lock = None
        if lock_key:
            from src.infrastructure.redis_client import get_redis_text
            r_lock = get_redis_text()
            if not r_lock.set(lock_key, "1", nx=True, ex=60):
                yield f"data: {_json.dumps({'type': 'error', 'msg': 'Pergunta anterior ainda em processamento — aguarde a resposta.'})}\n\n"
                yield f"data: {_json.dumps({'type': 'done'})}\n\n"
                return

        try:
            yield _sse_step("pipeline", "running", "Processando via Cognitive OS (LangGraph)…")
            t0 = _t.monotonic()

            from src.application.orchestration.entrypoint import processar as cognitive_processar
            from src.memory.container import create_memory_service

            mem_svc = create_memory_service()
            mem_ctx = mem_svc.carregar_contexto(user_id=thread_id, session_id=thread_id, query=msg)

            user_context = {
                "nome": "Admin", "curso": "Instituição", "role": "admin",
                "chat_id": thread_id, "has_media": False, "media_type": "", "msg_key_id": "",
            }

            result_os = await cognitive_processar(
                message=msg, session_id=thread_id, user_context=user_context,
                history=mem_ctx.historico.texto_formatado if mem_ctx.historico else "",
                fatos=[f.texto for f in mem_ctx.fatos] if mem_ctx.fatos else [],
            )
            yield _sse_step("pipeline", "ok", f"rota={result_os.rota}", _t.monotonic() - t0, {"rota": result_os.rota})

            if result_os.status == "ok" and result_os.answer:
                try:
                    mem_svc.persistir_turno(
                        session_id=thread_id, user_id=thread_id,
                        pergunta=msg, resposta=result_os.answer, rota=result_os.rota,
                    )
                    mem_svc.extrair_fatos_background(user_id=thread_id, session_id=thread_id)
                except Exception as e:
                    logger.warning("⚠️ Falha ao salvar turno no hub/chat: %s", e)

            total_ms = int((_t.monotonic() - t_total) * 1000)
            response_payload = {
                'type': 'response',
                'text': result_os.answer,
                'rota': result_os.rota,
                'total_ms': total_ms,
                'action_buttons': result_os.action_buttons,
                'status': result_os.status,
            }
            yield f"data: {_json.dumps(response_payload, ensure_ascii=False)}\n\n"

            metrics_payload = {
                'type': 'metrics',
                'rota': result_os.rota,
                'total_ms': total_ms,
                'workers': 1,
                'confianca': 1.0,
            }
            yield f"data: {_json.dumps(metrics_payload, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.exception("SSE /chat/stream error: %s", e)
            yield f"data: {_json.dumps({'type':'error','msg':str(e)[:200]})}\n\n"
        finally:
            if r_lock is not None:
                try:
                    r_lock.delete(lock_key)
                except Exception:
                    pass
            yield f"data: {_json.dumps({'type':'done'})}\n\n"

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

    from src.infrastructure import dynamic_config
    return templates.TemplateResponse(
        request=request,
        name="hub/chunkviz.html", # Verifique se o arquivo está nesta pasta
        context={
            "request": request,
            "username": payload.sub,
            "modelo": dynamic_config.get_str("GEMINI_MODEL"),  # config dinâmica (Fase 1)
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


@router.get("/routes", response_class=HTMLResponse)
async def routes_page(request: Request):
    """Registro de rotas (Plano A / Fase 2) — mapa rota→execução editável."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/routes.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/_styleguide", response_class=HTMLResponse)
async def styleguide_page(request: Request):
    """Plano B / Fase 0 — tokens e componentes isolados. NÃO linkada no menu;
    referência de construção, não toca nenhuma página real ainda."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/_styleguide.html",
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
        return _nao_autorizado()

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
                "tools": list(getattr(a, "tools", [])),
                "enabled": status[a.name],
                "llm_provider": catalogo.get(a.name, {}).get("llm_provider"),
                "llm_model": catalogo.get(a.name, {}).get("llm_model"),
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
        return _nao_autorizado()

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
        return _nao_autorizado()

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


class AgentLLMRequest(BaseModel):
    llm_provider: str | None = None  # "gemini" | "deepseek" | "groq" | None (herda global)
    llm_model:    str | None = None


def _providers_registrados() -> tuple[str, ...]:
    """Nomes de provedor válidos agora — builders de código + provedores
    cadastrados pelo painel (`llm_providers` → espelho Redis)."""
    try:
        from src.infrastructure.adapters.llm_provider_registry import registrados
        return registrados() or ("gemini", "deepseek", "groq")
    except Exception:
        return ("gemini", "deepseek", "groq")


@router.post("/agents/{name}/llm")
async def agents_set_llm(request: Request, name: str, data: AgentLLMRequest):
    """Define o override de provider/modelo LLM deste agente (ou limpa,
    enviando null nos dois — volta a herdar o provider global). Grava em
    Postgres (fonte de verdade, `agentes_catalogo`) e no cache Redis que
    `llm_factory.get_llm_provider()` lê no caminho quente (ver
    `infrastructure/adapters/llm_factory.py::_override_do_agente`)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    _validos = _providers_registrados()
    if data.llm_provider and data.llm_provider not in _validos:
        return {"error": f"Provedor inválido: {data.llm_provider}. Use um de {_validos}."}

    from src.agents.registry import registry
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.agent_catalog_repository import AgentCatalogRepository
    from src.infrastructure.repositories.observability_repository import ObservabilityRepository
    from src.infrastructure.redis_client import get_redis_text

    try:
        registry.resolve(name)
    except KeyError:
        return {"error": f"Agente '{name}' não encontrado."}

    try:
        async with AsyncSessionLocal() as session:
            await AgentCatalogRepository(session).set_llm_override(
                name, data.llm_provider, data.llm_model, admin=payload.sub,
            )
            await ObservabilityRepository(session).salvar_audit(
                admin_id=payload.sub, action="agent_llm_override", target=name,
                detalhes={"llm_provider": data.llm_provider, "llm_model": data.llm_model},
            )
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao gravar override LLM de '%s': %s", name, exc)
        return {"error": "Falha ao gravar no Postgres. Tente novamente."}

    # Write-through no cache Redis que o caminho quente lê de verdade —
    # sem isso, o /hub mostraria a config nova mas o pipeline continuaria
    # usando a antiga até o próximo restart (mesma classe de bug que o
    # painel de agentes já teve antes do catálogo Postgres existir).
    try:
        r = get_redis_text()
        chave_provider = f"admin:agent:{name}:llm_provider"
        chave_model    = f"admin:agent:{name}:llm_model"
        if data.llm_provider:
            r.set(chave_provider, data.llm_provider)
        else:
            r.delete(chave_provider)
        if data.llm_model:
            r.set(chave_model, data.llm_model)
        else:
            r.delete(chave_model)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao atualizar cache Redis do override LLM de '%s': %s", name, exc)

    return {"name": name, "llm_provider": data.llm_provider, "llm_model": data.llm_model}


class GlobalLLMProviderRequest(BaseModel):
    provider: str


@router.get("/llm/provider")
async def llm_provider_get(request: Request):
    """Provider global ativo agora (troca em runtime, sem restart)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.adapters.llm_factory import _provider_global_ativo
    return {"provider": _provider_global_ativo(), "opcoes": list(_providers_registrados())}


@router.post("/llm/provider")
async def llm_provider_set(request: Request, data: GlobalLLMProviderRequest):
    """Troca o provider LLM global em runtime (Redis `admin:llm_provider`,
    sem restart) — afeta toda chamada que não tenha override por agente."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    _validos = _providers_registrados()
    if data.provider not in _validos:
        return {"error": f"Provedor inválido: {data.provider}. Use um de {_validos}."}

    from src.infrastructure.adapters.llm_factory import _CHAVE_PROVIDER_GLOBAL
    from src.infrastructure.redis_client import get_redis_text
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.observability_repository import ObservabilityRepository

    try:
        get_redis_text().set(_CHAVE_PROVIDER_GLOBAL, data.provider)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao trocar provider global no Redis: %s", exc)
        return {"error": "Falha ao gravar no Redis. Tente novamente."}

    try:
        async with AsyncSessionLocal() as session:
            await ObservabilityRepository(session).salvar_audit(
                admin_id=payload.sub, action="llm_provider_global_change",
                detalhes={"provider": data.provider},
            )
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao registrar audit_log da troca de provider: %s", exc)

    return {"provider": data.provider}


# ─────────────────────────────────────────────────────────────────────────────
# Provedores de LLM (aba Provedores da Configuração) — Hub v2 Sprint 3a
# `llm_providers` (Postgres) + espelho Redis. Chave de API fica no .env;
# aqui só o NOME da variável de ambiente.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/providers")
async def providers_listar(request: Request):
    """Provedores cadastrados (código + painel), com saúde e modelo. Faz o
    seed dos 3 nativos na primeira chamada."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.adapters import llm_provider_store, llm_provider_registry
    from src.infrastructure.adapters.llm_factory import _provider_global_ativo
    from src.infrastructure.database.session import AsyncSessionLocal

    linhas = []
    try:
        async with AsyncSessionLocal() as session:
            if await llm_provider_store.seed_inicial(session, admin=payload.sub):
                await llm_provider_store.espelhar_redis(session)
                await session.commit()
            linhas = await llm_provider_store.listar(session)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao listar provedores: %s", exc)

    for l in linhas:
        l["saude"] = llm_provider_registry.health_check(l["nome"])

    return {"providers": linhas, "ativo_global": _provider_global_ativo()}


class ProviderCriarRequest(BaseModel):
    nome:           str
    tipo:           str = "openai_compat"
    base_url:       str = ""
    api_key_env:    str = ""
    modelo_default: str = ""
    modelos:        list[str] = []


@router.post("/providers")
async def providers_criar(request: Request, data: ProviderCriarRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.adapters import llm_provider_store
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            registro = await llm_provider_store.criar(
                session, data.nome, data.tipo, base_url=data.base_url,
                api_key_env=data.api_key_env, modelos=data.modelos,
                modelo_default=data.modelo_default, admin=payload.sub,
            )
            await llm_provider_store.espelhar_redis(session)
            await session.commit()
    except (llm_provider_store.NomeDuplicadoError, llm_provider_store.ConfigInvalidaError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao criar provedor '%s': %s", data.nome, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    return {"ok": True, **registro}


class ProviderToggleRequest(BaseModel):
    provider_id: int
    habilitado:  bool


@router.post("/providers/toggle")
async def providers_toggle(request: Request, data: ProviderToggleRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.adapters import llm_provider_store
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            ok = await llm_provider_store.set_habilitado(session, data.provider_id, data.habilitado, admin=payload.sub)
            await llm_provider_store.espelhar_redis(session)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao togglar provedor %s: %s", data.provider_id, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    if not ok:
        return {"error": "Provedor não encontrado."}
    return {"ok": True, "provider_id": data.provider_id, "habilitado": data.habilitado}


class ProviderRemoverRequest(BaseModel):
    provider_id: int


@router.post("/providers/remove")
async def providers_remover(request: Request, data: ProviderRemoverRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.adapters import llm_provider_store
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            ok = await llm_provider_store.remover(session, data.provider_id)
            await llm_provider_store.espelhar_redis(session)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao remover provedor %s: %s", data.provider_id, exc)
        return {"error": "Falha ao remover. Tente novamente."}

    if not ok:
        return {"error": "Provedor não encontrado (ou é de código, não removível)."}
    return {"ok": True, "provider_id": data.provider_id}


class ProviderTestRequest(BaseModel):
    nome:           str | None = None    # provedor já cadastrado
    tipo:           str | None = None    # ou dados soltos p/ testar antes de salvar
    base_url:       str | None = None
    api_key_env:    str | None = None
    modelo_default: str | None = None


@router.post("/providers/test-connection")
async def providers_test_connection(request: Request, data: ProviderTestRequest):
    """Faz uma chamada mínima real ao provedor e devolve ok/erro. Aceita um
    provedor já cadastrado (`nome`) ou dados soltos (para testar antes de
    salvar)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    import os as _os
    from src.infrastructure.adapters import llm_provider_registry

    try:
        if data.nome:
            provider = llm_provider_registry.instanciar(data.nome)
        elif data.tipo == "openai_compat" and data.base_url:
            from src.infrastructure.adapters.openai_compatible_provider import OpenAICompatibleProvider
            chave = _os.getenv(data.api_key_env or "", "")
            if not chave:
                return {"ok": False, "mensagem": f"Variável de ambiente '{data.api_key_env}' está vazia."}
            provider = OpenAICompatibleProvider(
                provider_name=data.nome or "teste", base_url=data.base_url,
                api_key=chave, model=data.modelo_default or "gpt-3.5-turbo",
            )
        else:
            return {"ok": False, "mensagem": "Informe um provedor ou os dados de conexão."}

        resp = await provider.gerar_resposta_async("ping", max_tokens=1, temperatura=0.0)
        if resp.sucesso or resp.conteudo:
            return {"ok": True, "mensagem": f"Respondeu ({provider.model})"}
        return {"ok": False, "mensagem": resp.erro or "Sem resposta"}
    except ValueError as exc:
        return {"ok": False, "mensagem": str(exc)}
    except Exception as exc:
        logger.warning("⚠️  [HUB] test-connection provedor falhou: %s", exc)
        return {"ok": False, "mensagem": str(exc)[:200]}


# ─────────────────────────────────────────────────────────────────────────────
# Canais (aba Integradores da Configuração) — Hub v2 Sprint 3b
# `canais` (Postgres) + espelho Redis. Só "conectar instância existente":
# status / QR / webhook. O hot path de mensagem segue em `settings`.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/channels")
async def channels_listar(request: Request):
    """Canais cadastrados + estado da conexão (via Evolution). Faz o seed da
    instância atual na primeira chamada."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.services import channel_store
    from src.infrastructure.database.session import AsyncSessionLocal

    canais = []
    try:
        async with AsyncSessionLocal() as session:
            if await channel_store.seed_inicial(session, admin=payload.sub):
                await channel_store.espelhar_redis(session)
                await session.commit()
            canais = await channel_store.listar(session)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao listar canais: %s", exc)

    for c in canais:
        if c["habilitado"]:
            c["conexao"] = await channel_store.status_evolution(c)
        else:
            c["conexao"] = {"estado": "desligado"}

    return {"channels": canais}


class CanalCriarRequest(BaseModel):
    nome:        str
    base_url:    str
    api_key_env: str = "EVOLUTION_API_KEY"
    instance:    str
    webhook_url: str = ""


@router.post("/channels")
async def channels_criar(request: Request, data: CanalCriarRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.services import channel_store
    from src.infrastructure.security.ssrf_validator import URLInseguraError
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            registro = await channel_store.criar(
                session, data.nome, base_url=data.base_url, api_key_env=data.api_key_env,
                instance=data.instance, webhook_url=data.webhook_url, admin=payload.sub,
            )
            await channel_store.espelhar_redis(session)
            await session.commit()
    except URLInseguraError as exc:
        return {"error": f"URL rejeitada: {exc}"}
    except (channel_store.NomeDuplicadoError, channel_store.ConfigInvalidaError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao criar canal '%s': %s", data.nome, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    return {"ok": True, **registro}


class CanalToggleRequest(BaseModel):
    canal_id:   int
    habilitado: bool


@router.post("/channels/toggle")
async def channels_toggle(request: Request, data: CanalToggleRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    from src.services import channel_store
    from src.infrastructure.database.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            ok = await channel_store.set_habilitado(session, data.canal_id, data.habilitado, admin=payload.sub)
            await channel_store.espelhar_redis(session)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao togglar canal %s: %s", data.canal_id, exc)
        return {"error": "Falha ao gravar. Tente novamente."}
    return {"ok": True, "canal_id": data.canal_id, "habilitado": data.habilitado} if ok else {"error": "Canal não encontrado."}


class CanalRemoverRequest(BaseModel):
    canal_id: int


@router.post("/channels/remove")
async def channels_remover(request: Request, data: CanalRemoverRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    from src.services import channel_store
    from src.infrastructure.database.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            ok = await channel_store.remover(session, data.canal_id)
            await channel_store.espelhar_redis(session)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao remover canal %s: %s", data.canal_id, exc)
        return {"error": "Falha ao remover. Tente novamente."}
    return {"ok": True, "canal_id": data.canal_id} if ok else {"error": "Canal não encontrado (ou é de código)."}


class CanalIdRequest(BaseModel):
    canal_id: int


@router.post("/channels/reconnect")
async def channels_reconnect(request: Request, data: CanalIdRequest):
    """Dispara o QR de pareamento da instância — devolve o base64 para exibir."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    from src.services import channel_store
    from src.infrastructure.database.session import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        canal = await channel_store.obter(session, data.canal_id)
    if canal is None:
        return {"error": "Canal não encontrado."}
    return await channel_store.qrcode_evolution(canal)


class CanalWebhookRequest(BaseModel):
    canal_id:    int
    webhook_url: str


@router.post("/channels/webhook")
async def channels_webhook(request: Request, data: CanalWebhookRequest):
    """Grava o novo webhook (Postgres + espelho) e tenta aplicá-lo na Evolution."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    from src.services import channel_store
    from src.infrastructure.security.ssrf_validator import URLInseguraError
    from src.infrastructure.database.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            ok = await channel_store.set_webhook(session, data.canal_id, data.webhook_url, admin=payload.sub)
            canal = await channel_store.obter(session, data.canal_id)
            await channel_store.espelhar_redis(session)
            await session.commit()
    except URLInseguraError as exc:
        return {"error": f"URL rejeitada: {exc}"}
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao gravar webhook do canal %s: %s", data.canal_id, exc)
        return {"error": "Falha ao gravar. Tente novamente."}
    if not ok:
        return {"error": "Canal não encontrado."}
    aplicado = await channel_store.set_webhook_evolution(canal, data.webhook_url) if canal else {"ok": False}
    return {"ok": True, "aplicado_na_evolution": aplicado.get("ok", False), "detalhe": aplicado.get("erro", "")}


class BrlRateRequest(BaseModel):
    taxa: float


@router.post("/llm-custo/brl-rate")
async def llm_custo_brl_rate_set(request: Request, data: BrlRateRequest):
    """Edita a taxa USD→BRL usada em `/hub/llm-custo` (Redis
    `admin:usd_brl_rate`, mesmo padrão de `admin:llm_provider` — sem API de
    câmbio externa, decisão deliberada, ver plano de observabilidade)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    if data.taxa <= 0:
        return {"error": "Taxa precisa ser maior que zero."}

    from src.infrastructure.observability.pricing import _CHAVE_BRL_RATE
    from src.infrastructure.redis_client import get_redis_text

    try:
        get_redis_text().set(_CHAVE_BRL_RATE, str(data.taxa))
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao gravar taxa BRL no Redis: %s", exc)
        return {"error": "Falha ao gravar no Redis. Tente novamente."}

    return {"taxa": data.taxa}


@router.post("/llm-custo/brl-rate/auto")
async def llm_custo_brl_rate_usar_auto(request: Request):
    """Remove o override manual — volta a usar a cotação ao vivo (ou o
    fallback fixo, se a task `atualizar_taxa_brl` ainda não rodou)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.observability.pricing import _CHAVE_BRL_RATE, taxa_brl_ativa, taxa_brl_origem
    from src.infrastructure.redis_client import get_redis_text

    try:
        get_redis_text().delete(_CHAVE_BRL_RATE)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao remover override manual de taxa BRL: %s", exc)
        return {"error": "Falha ao gravar no Redis. Tente novamente."}

    return {"taxa": taxa_brl_ativa(), "origem": taxa_brl_origem()}


@router.get("/llm-custo", response_class=HTMLResponse)
async def llm_custo_page(request: Request):
    """Página de custo/telemetria real dos providers LLM (Postgres
    `metricas_llm`, ver analise_custo_real_llm.md)."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/llm_custo.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/llm-custo/data")
async def llm_custo_data(request: Request, horas: int = 24):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.observability_repository import ObservabilityRepository
    from src.infrastructure.adapters.llm_factory import _provider_global_ativo
    from src.infrastructure.observability.pricing import taxa_brl_ativa, taxa_brl_origem

    try:
        async with AsyncSessionLocal() as session:
            repo = ObservabilityRepository(session)
            resumo     = await repo.get_metricas_dashboard(horas)
            por_rota   = await repo.get_metricas_por_rota(horas)
            por_provider = await repo.get_metricas_por_provider(horas)
            serie      = await repo.get_serie_horaria(horas)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler telemetria de custo: %s", exc)
        return {"error": "Falha ao consultar métricas."}

    taxa_brl = taxa_brl_ativa()
    custo_usd_total = float(resumo.get("custo_usd") or 0)

    try:
        from src.infrastructure.semantic_cache import cache_stats, TTL_POR_ROTA, THRESHOLD_POR_ROTA
        cache = cache_stats()
        cache["ttl_por_rota"] = TTL_POR_ROTA
        cache["threshold_por_rota"] = THRESHOLD_POR_ROTA
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler cache_stats: %s", exc)
        cache = {"total_entradas": 0, "por_rota": {}, "ttl_por_rota": {}, "threshold_por_rota": {}}

    # Plano A / Fase 3: Provider Registry + circuit breaker (§O/§S)
    try:
        from src.infrastructure.adapters import llm_circuit_breaker, llm_provider_registry
        provider_registry = llm_provider_registry.status()
        circuit_breaker = llm_circuit_breaker.status()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler provider registry/CB: %s", exc)
        provider_registry, circuit_breaker = [], []

    return {
        "provider_global_ativo": _provider_global_ativo(),
        "provedores_opcoes": list(_providers_registrados()),
        "resumo": resumo,
        "serie": serie,
        "por_rota": por_rota,
        "por_provider": por_provider,
        "cache": cache,
        "horas": horas,
        "taxa_brl": taxa_brl,
        "taxa_brl_origem": taxa_brl_origem(),
        "custo_brl_total": round(custo_usd_total * taxa_brl, 4),
        "provider_registry": provider_registry,
        "circuit_breaker": circuit_breaker,
    }


class CircuitResetRequest(BaseModel):
    provider: str


@router.post("/llm/circuit/reset")
async def llm_circuit_reset(request: Request, data: CircuitResetRequest):
    """Zera o disjuntor de falhas de um provedor (fecha o circuito)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    try:
        from src.infrastructure.adapters import llm_circuit_breaker
        llm_circuit_breaker.registrar_sucesso(data.provider)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao resetar circuito de '%s': %s", data.provider, exc)
        return {"error": "Falha ao resetar."}
    return {"ok": True, "provider": data.provider}


@router.get("/llm-pricing/data")
async def llm_pricing_data(request: Request):
    """Tabela de preços editável (`llm_pricing`, migration 008)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.llm_pricing_repository import LlmPricingRepository

    try:
        async with AsyncSessionLocal() as session:
            precos = await LlmPricingRepository(session).listar()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler llm_pricing: %s", exc)
        return {"error": "Falha ao consultar preços."}

    return {"precos": precos}


class LlmPricingRequest(BaseModel):
    provider:       str
    modelo:         str
    input_por_1m:   float
    output_por_1m:  float
    cache_por_1m:   float | None = None


@router.post("/llm-pricing")
async def llm_pricing_set(request: Request, data: LlmPricingRequest):
    """Edita o preço/1M tokens de um provider+modelo. Grava em Postgres
    (fonte de verdade, `llm_pricing`) e faz write-through no cache Redis que
    `pricing.calcular_custo_usd` lê no caminho quente — mesmo padrão do
    override de LLM por agente (`agents_set_llm` acima)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.llm_pricing_repository import LlmPricingRepository
    from src.infrastructure.observability.pricing import chave_redis_preco
    from src.infrastructure.redis_client import get_redis_text

    try:
        async with AsyncSessionLocal() as session:
            await LlmPricingRepository(session).upsert(
                data.provider, data.modelo,
                data.input_por_1m, data.output_por_1m, data.cache_por_1m,
                admin=payload.sub,
            )
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao gravar llm_pricing (%s/%s): %s", data.provider, data.modelo, exc)
        return {"error": "Falha ao gravar no Postgres. Tente novamente."}

    try:
        r = get_redis_text()
        r.set(chave_redis_preco(data.provider, data.modelo), json.dumps({
            "input_por_1m": data.input_por_1m,
            "output_por_1m": data.output_por_1m,
            "cache_por_1m": data.cache_por_1m,
        }))
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao atualizar cache Redis de preço (%s/%s): %s", data.provider, data.modelo, exc)

    return {"ok": True, "provider": data.provider, "modelo": data.modelo}


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
        return _nao_autorizado()

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
        return _nao_autorizado()

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
        return _nao_autorizado()

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
    """Ferramentas disponíveis (código + painel) + vínculo agente↔ferramenta
    (`agente_tools`) + servidores MCP cadastrados (para o modal de criação)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.capabilities.registry import manifestos
    from src.capabilities import agent_tools, tool_catalog
    from src.graph_studio import mcp_server_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    bindings, painel_tools, mcp_servers_lista = [], [], []
    try:
        async with AsyncSessionLocal() as session:
            bindings = await agent_tools.listar(session)
            painel_tools = await tool_catalog.listar(session)
            mcp_servers_lista = [
                s["name"] for s in await mcp_server_registry.listar(session) if s["habilitado"]
            ]
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler catálogo de capabilities: %s", exc)

    return {
        "capabilities": tool_catalog.mesclar_com_codigo(manifestos(), painel_tools),
        "bindings": [
            {**b, "atualizado_em": b["atualizado_em"].isoformat() if b["atualizado_em"] else None}
            for b in bindings
        ],
        "mcp_servers": mcp_servers_lista,
    }


class ToolCriarRequest(BaseModel):
    nome:        str
    tipo:        str                       # "http" | "mcp"
    descricao:   str = ""
    config:      Dict[str, Any] = {}
    permissoes:  list[str] = []
    confirmacao: bool = False


@router.post("/tools")
async def tools_criar(request: Request, data: ToolCriarRequest):
    """Cadastra uma ferramenta pelo painel (`tools_catalogo`, migration 016).
    URL de ferramenta HTTP passa por validação SSRF antes de gravar."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.capabilities import tool_catalog
    from src.infrastructure.security.ssrf_validator import URLInseguraError
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            registro = await tool_catalog.criar(
                session, data.nome, data.tipo, data.config,
                descricao=data.descricao, permissoes=data.permissoes,
                confirmacao=data.confirmacao, admin=payload.sub,
            )
            await session.commit()
    except URLInseguraError as exc:
        return {"error": f"URL rejeitada: {exc}"}
    except (tool_catalog.NomeDuplicadoError, tool_catalog.ConfigInvalidaError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao criar ferramenta '%s': %s", data.nome, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    return {"ok": True, **registro}


class ToolToggleRequest(BaseModel):
    tool_id:    int
    habilitado: bool


@router.post("/tools/toggle")
async def tools_toggle(request: Request, data: ToolToggleRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.capabilities import tool_catalog
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            ok = await tool_catalog.set_habilitado(session, data.tool_id, data.habilitado, admin=payload.sub)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao togglar ferramenta %s: %s", data.tool_id, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    if not ok:
        return {"error": "Ferramenta não encontrada."}
    return {"ok": True, "tool_id": data.tool_id, "habilitado": data.habilitado}


class ToolRemoverRequest(BaseModel):
    tool_id: int


@router.post("/tools/remove")
async def tools_remover(request: Request, data: ToolRemoverRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.capabilities import tool_catalog
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            ok = await tool_catalog.remover(session, data.tool_id)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao remover ferramenta %s: %s", data.tool_id, exc)
        return {"error": "Falha ao remover. Tente novamente."}

    if not ok:
        return {"error": "Ferramenta não encontrada."}
    return {"ok": True, "tool_id": data.tool_id}


class ToolTestRequest(BaseModel):
    tool_id: int
    args:    Dict[str, Any] = {}


@router.post("/tools/test")
async def tools_test(request: Request, data: ToolTestRequest):
    """Executa a ferramenta uma vez com `args` de teste e devolve o resultado
    cru — nunca lança (o executor sempre retorna `{ok: bool}`)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.capabilities import tool_catalog, dynamic_tool_executor
    from src.infrastructure.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        tool = await tool_catalog.obter(session, data.tool_id)
    if tool is None:
        return {"error": "Ferramenta não encontrada."}

    try:
        resultado = await dynamic_tool_executor.executar(tool["nome"], data.args)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"ok": bool(resultado.get("ok")), "resultado": resultado}


class CapabilityToggleRequest(BaseModel):
    agente:     str
    tool:       str
    habilitado: bool


@router.post("/capabilities/toggle")
async def capabilities_toggle(request: Request, data: CapabilityToggleRequest):
    """Liga/desliga um vínculo agente↔capability."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.capabilities import agent_tools
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            ok = await agent_tools.set_habilitado(
                session, data.agente, data.tool, data.habilitado, admin=payload.sub,
            )
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao togglar capability %s/%s: %s", data.agente, data.tool, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    if not ok:
        return {"error": f"Vínculo {data.agente}↔{data.tool} não existe."}
    return {"ok": True, "agente": data.agente, "tool": data.tool, "habilitado": data.habilitado}


@router.get("/graph-nodes", response_class=HTMLResponse)
async def graph_nodes_page(request: Request):
    """Serve a página somente-leitura do NodeRegistry (Camada 1 / Fase 6)."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/graph-nodes.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/graph-nodes/data")
async def graph_nodes_data(request: Request):
    """Nós registrados no NodeRegistry (BaseNode: LLM/STT/TTS/Embeddings/
    Parser/Tool/Channel/MCP/REST, Camada 1 — ver
    docs/decision_camada1_nodes.md), mesclados com o toggle habilitado/
    desabilitado de `graph_node_config` (migration 013)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio.node_registry import get_registry
    from src.graph_studio import node_config, node_health
    from src.infrastructure.database.session import AsyncSessionLocal

    nos = get_registry().list_nodes()

    # Saúde barata (sem chamada de rede) — preenche o `health` que o registry
    # deixa `null` por padrão. Só `llm_provider` tem probe real hoje.
    for no in nos:
        saude = node_health.resolver(no["type"])
        if saude is not None:
            no["health"] = {
                "is_healthy": saude["is_healthy"],
                "error": saude.get("error"),
                "detail": saude.get("detail"),
            }

    config_rows = []
    try:
        async with AsyncSessionLocal() as session:
            config_rows = await node_config.listar(session)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler graph_node_config: %s", exc)

    return {"nodes": node_config.mesclar_com_registry(nos, config_rows)}


class GraphNodeToggleRequest(BaseModel):
    node_id:    str
    habilitado: bool


@router.post("/graph-nodes/toggle")
async def graph_nodes_toggle(request: Request, data: GraphNodeToggleRequest):
    """Liga/desliga um nó do registry (não remove/recria — só a Configuration
    Layer, o nó continua existindo em código)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio.node_registry import get_registry

    if get_registry().get(data.node_id) is None:
        return {"error": f"Nó '{data.node_id}' não existe no registry."}

    from src.graph_studio import node_config
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await node_config.set_habilitado(session, data.node_id, data.habilitado, admin=payload.sub)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao togglar nó '%s': %s", data.node_id, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    return {"ok": True, "node_id": data.node_id, "habilitado": data.habilitado}


@router.get("/mcp-servers", response_class=HTMLResponse)
async def mcp_servers_page(request: Request):
    """Serve a página de cadastro de servidores MCP (Fase 8, Camada 2 de nós)."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/mcp-servers.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/mcp-servers/data")
async def mcp_servers_data(request: Request):
    """Servidores MCP cadastrados (`mcp_servers`, migration 014)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import mcp_server_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    servidores = []
    try:
        async with AsyncSessionLocal() as session:
            servidores = await mcp_server_registry.listar(session)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler mcp_servers: %s", exc)

    return {
        "servers": [
            {**s, "atualizado_em": s["atualizado_em"].isoformat() if s["atualizado_em"] else None}
            for s in servidores
        ]
    }


class McpServerRegisterRequest(BaseModel):
    name:        str
    url:         str
    description: str = ""
    auth_tipo:   str = "none"     # none | bearer | api_key
    auth_env:    str = ""         # nome da variável de ambiente com o segredo


@router.post("/mcp-servers/register")
async def mcp_servers_register(request: Request, data: McpServerRegisterRequest):
    """Cadastra um novo servidor MCP — a URL passa por validação SSRF
    obrigatória antes de qualquer escrita. A autenticação guarda só o NOME
    da variável de ambiente do segredo (nunca o valor)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import mcp_server_registry
    from src.infrastructure.security.ssrf_validator import URLInseguraError
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            registro = await mcp_server_registry.registrar(
                session, data.name, data.url, data.description,
                auth_tipo=data.auth_tipo, auth_env=data.auth_env, admin=payload.sub,
            )
            await session.commit()
    except URLInseguraError as exc:
        return {"error": f"URL rejeitada: {exc}"}
    except mcp_server_registry.NomeDuplicadoError as exc:
        return {"error": str(exc)}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao registrar servidor MCP '%s': %s", data.name, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    return {"ok": True, **{k: v for k, v in registro.items() if k != "atualizado_em"}}


class McpServerNameRequest(BaseModel):
    name: str


@router.post("/mcp-servers/test")
async def mcp_servers_test(request: Request, data: McpServerNameRequest):
    """Conecta no servidor, mede latência e lista as ferramentas expostas."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import mcp_server_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            resultado = await mcp_server_registry.testar_conexao(session, data.name)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao testar servidor MCP '%s': %s", data.name, exc)
        return {"ok": False, "erro": "Falha ao testar."}
    return resultado


@router.post("/mcp-servers/sync")
async def mcp_servers_sync(request: Request, data: McpServerNameRequest):
    """Sincroniza as ferramentas do servidor para `tools_catalogo` (tipo mcp)
    — ficam disponíveis para vincular a um agente em /hub/capabilities."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import mcp_server_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            resultado = await mcp_server_registry.sincronizar_ferramentas(session, data.name)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao sincronizar ferramentas de '%s': %s", data.name, exc)
        return {"ok": False, "erro": "Falha ao sincronizar."}
    return resultado


class McpServerToggleRequest(BaseModel):
    name:       str
    habilitado: bool


@router.post("/mcp-servers/toggle")
async def mcp_servers_toggle(request: Request, data: McpServerToggleRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import mcp_server_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            ok = await mcp_server_registry.set_habilitado(session, data.name, data.habilitado, admin=payload.sub)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao togglar servidor MCP '%s': %s", data.name, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    if not ok:
        return {"error": f"Servidor '{data.name}' não existe."}
    return {"ok": True, "name": data.name, "habilitado": data.habilitado}


class McpServerRemoveRequest(BaseModel):
    name: str


@router.post("/mcp-servers/remove")
async def mcp_servers_remove(request: Request, data: McpServerRemoveRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import mcp_server_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            ok = await mcp_server_registry.remover(session, data.name)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao remover servidor MCP '%s': %s", data.name, exc)
        return {"error": "Falha ao remover. Tente novamente."}

    if not ok:
        return {"error": f"Servidor '{data.name}' não existe."}
    return {"ok": True, "name": data.name}


@router.get("/graph-studio", response_class=HTMLResponse)
async def graph_studio_page(request: Request):
    """Serve o canvas de composição visual de grafo (adendo de nós
    declarativos, Camada 3)."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/graph-studio.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/graph-studio/nodes")
async def graph_studio_nodes(request: Request):
    """Nós disponíveis pra arrastar no canvas — mesmo registry de
    /hub/graph-nodes, formato reduzido (só o que o canvas precisa)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio.node_registry import get_registry

    return {"nodes": get_registry().list_nodes()}


@router.get("/graph-studio/reference")
async def graph_studio_reference(request: Request):
    """Diagramas (somente leitura) do roteamento que já existe — reflete
    `supervisor.py` + `route_registry`, não é editável nem executável."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    from src.graph_studio.reference_flows import como_json
    return {"fluxos": como_json()}


@router.get("/graph-studio/topologies")
async def graph_studio_topologies(request: Request):
    """Topologias salvas (`graph_topology`, migration 015)."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import topology_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    topologias = []
    try:
        async with AsyncSessionLocal() as session:
            topologias = await topology_registry.listar(session)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao ler graph_topology: %s", exc)

    return {
        "topologies": [
            {**t, "atualizado_em": t["atualizado_em"].isoformat() if t["atualizado_em"] else None}
            for t in topologias
        ]
    }


class GraphTopologySaveRequest(BaseModel):
    name:          str
    topology_json: dict
    description:   str = ""
    status:        str = "draft"
    gatilho:       str | None = None


@router.post("/graph-studio/save")
async def graph_studio_save(request: Request, data: GraphTopologySaveRequest):
    """Valida (tipos de porta + DAG) e persiste uma topologia. Retorna os
    erros de validação sem gravar nada se a topologia for inválida."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import topology_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            resultado = await topology_registry.salvar(
                session, data.name, data.topology_json,
                data.description, data.status, admin=payload.sub,
                gatilho=data.gatilho,
            )
            await session.commit()
    except topology_registry.TopologiaInvalidaError as exc:
        return {"error": "Topologia inválida.", "detalhes": exc.erros}
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao salvar topologia '%s': %s", data.name, exc)
        return {"error": "Falha ao gravar. Tente novamente."}

    return {
        "ok": True,
        **{k: v for k, v in resultado.items() if k != "atualizado_em"},
        "atualizado_em": resultado["atualizado_em"].isoformat() if resultado["atualizado_em"] else None,
    }


class GraphTopologyRemoveRequest(BaseModel):
    name: str


@router.post("/graph-studio/remove")
async def graph_studio_remove(request: Request, data: GraphTopologyRemoveRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio import topology_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            ok = await topology_registry.remover(session, data.name)
            await session.commit()
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao remover topologia '%s': %s", data.name, exc)
        return {"error": "Falha ao remover. Tente novamente."}

    if not ok:
        return {"error": f"Topologia '{data.name}' não existe."}
    return {"ok": True, "name": data.name}


class GraphTestRequest(BaseModel):
    name:           str
    dry_run:        bool = True
    modo:           str = "caminho"   # "caminho" (dry-run) | "sandbox" (real isolado)
    mensagem_teste: str = ""


@router.post("/graph-studio/test")
async def graph_studio_test(request: Request, data: GraphTestRequest):
    """Executa uma topologia salva.

    - `modo="caminho"` (padrão): dry-run — só calcula a ordem e emite os
      eventos, não chama nenhum componente.
    - `modo="sandbox"`: teste manual — roda os componentes DE VERDADE, mas
      isolado (`tenant_id=None`, sem persistência, limite de nós + timeout).
      Consome tokens do provedor ativo. Só dispara neste clique.

    Execução ligada ao pipeline de produção (`modo="producao"`) continuaria
    exigindo `FEATURE_GRAPH_EXECUTOR_PILOTO` — não implementada aqui."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.graph_studio.graph_executor import (
        executar_topologia_salva, executar_topologia_sandbox,
    )

    if data.modo == "sandbox":
        try:
            resultado = await executar_topologia_sandbox(data.name, data.mensagem_teste)
        except Exception as exc:
            logger.warning("⚠️  [HUB] Falha no teste sandbox de '%s': %s", data.name, exc)
            return {"error": "Falha na execução."}
        try:
            from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
            await RedisAuditLog().registar(
                admin_id=payload.sub, action="graph_sandbox_test", target=data.name,
                payload={"mensagem": data.mensagem_teste[:120], "ok": resultado.ok},
                resultado="ok" if resultado.ok else "erro",
            )
        except Exception:
            pass
        return resultado.to_dict()

    try:
        resultado = await executar_topologia_salva(data.name, dry_run=True)
    except Exception as exc:
        logger.warning("⚠️  [HUB] Falha ao executar topologia '%s': %s", data.name, exc)
        return {"error": "Falha na execução."}
    return resultado.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Infraestrutura — Armazenamento & Cache (Hub v2 Sprint 5)
# "RedisInsight light": só o que o Oráculo usa. Ações destrutivas com dry-run.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/infra/storage", response_class=HTMLResponse)
async def infra_storage_page(request: Request):
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/infra-storage.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/infra/storage/data")
async def infra_storage_data(request: Request):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.observability import storage_health

    return {
        "redis": storage_health.redis_overview(),
        "modulos": storage_health.redis_modules(),
        "persistencia": storage_health.redis_persistencia(),
        "config": storage_health.redis_config(),
        "slowlog": storage_health.redis_slowlog(15),
        "postgres": await storage_health.postgres_overview(),
    }


@router.get("/infra/health", response_class=HTMLResponse)
async def infra_health_page(request: Request):
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/infra-health.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/health")
async def infra_health_data(request: Request):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    from src.infrastructure.observability import system_health
    return await system_health.coletar()


@router.post("/infra/redis/recriar-indices")
async def infra_redis_recriar_indices(request: Request):
    """Recria a estrutura dos índices de busca (idempotente) — útil se algum
    sumiu. NÃO apaga nem restaura dados. Ação segura."""
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()

    from src.infrastructure.observability import storage_health

    try:
        indices = await storage_health.recriar_indices()
    except Exception as exc:
        logger.warning("⚠️  [HUB] recriar índices falhou: %s", exc)
        return {"error": "Falha ao recriar índices."}
    return {"ok": True, "indices": indices}


# ─────────────────────────────────────────────────────────────────────────────
# Infraestrutura — Busca & Índices (Hub v2 Sprint 6a)
# Leitura dos índices RediSearch + teste de busca híbrida. Não muda índice.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/infra/search", response_class=HTMLResponse)
async def infra_search_page(request: Request):
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    return templates.TemplateResponse(
        request=request, name="hub/infra-search.html",
        context={"request": request, "username": payload.sub},
    )


@router.get("/infra/search/data")
async def infra_search_data(request: Request):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    from src.infrastructure.observability import search_health
    return {"indices": search_health.listar_indices()}


class BuscaTesteRequest(BaseModel):
    query: str
    k:     int = 6


@router.post("/infra/search/test")
async def infra_search_test(request: Request, data: BuscaTesteRequest):
    payload = _verificar_cookie(request)
    if not payload:
        return _nao_autorizado()
    from src.infrastructure.observability import search_health
    return await search_health.testar_busca(data.query, min(max(data.k, 1), 20))


@router.get("/eval", response_class=HTMLResponse)
async def eval_page(request: Request):
    """Serve a página HTML do Dashboard de Avaliação."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)
    from src.infrastructure import dynamic_config
    return templates.TemplateResponse(
        request=request, name="hub/eval.html",
        context={"request": request, "username": payload.sub,
                 "modelo": dynamic_config.get_str("GEMINI_MODEL")},  # config dinâmica (Fase 1)
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
        from src.infrastructure.scraping.implementations.dokuwiki import DokuWikiScraper
        from src.infrastructure.scraping.base_scraper import ScrapeRequest

        # Roteia pelo domínio, igual ScrapingService._resolve(): wiki CTIC usa
        # o scraper especializado (do=export_raw), qualquer outra URL cai no
        # scraper genérico (fallback).
        scraper = DokuWikiScraper() if "ctic.uema.br" in url else GenericHTTPScraper()
        doc_type = "wiki_ctic" if isinstance(scraper, DokuWikiScraper) else "web"

        result = await scraper.scrape(ScrapeRequest(url=url, doc_type=doc_type))
        if not result.ok or not result.document:
            raise HTTPException(500, f"Scraping falhou: {result.error}")

        doc = result.document
        # save_temp_file gera o próprio file_id (hash do conteúdo) e persiste
        # o .json de metadados — não escrever o arquivo/hash manualmente aqui
        # de novo (bug antigo: chamava save_temp_file(file_id, ...) sem
        # extensão, o que sempre estourava "Formato '' não suportado").
        meta = save_temp_file(f"{doc.title or 'pagina'}.txt", doc.content.encode("utf-8"), "txt")
        if doc_type == "wiki_ctic":
            meta["wiki_metadata"] = doc.metadata
            with open(os.path.join(TEMP_DIR, f"{meta['file_id']}.json"), "w") as f:
                json.dump(meta, f)

        return {
            "file_id":    meta["file_id"],
            "title":      doc.title,
            "text":       doc.content[:10000],
            "total_chars": len(doc.content),
            "word_count": doc.word_count,
            "wiki_metadata": doc.metadata if doc_type == "wiki_ctic" else None,
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
        from src.application.orchestration.entrypoint import processar
        
        await queue.put(json.dumps({
            "tipo": "step_start", "step": "routing"
        }))

        result = await processar(
            message=pergunta,
            session_id=session_id,
            user_context={"nome": "Admin Live", "role": "admin"},
            history=""
        )

        crag = getattr(result, "crag_score", None)
        await queue.put(json.dumps({
            "tipo": "metricas",
            "rota": getattr(result, "rota", "GERAL"),
            "crag_score": round(crag, 3) if isinstance(crag, (int, float)) else None,
            "tokens_total": getattr(result, "tokens_used", 0),
            "tokens_entrada": 0,
            "tokens_saida": getattr(result, "tokens_used", 0),
            "latencia_ms": getattr(result, "total_ms", 0),
        }))

        await queue.put(json.dumps({
            "tipo": "resposta",
            "texto": result.answer,
            "fonte": getattr(result, "rota", "GERAL"),
            "tokens": getattr(result, "tokens_used", 0),
        }))

        await queue.put(json.dumps({"tipo": "done"}))

    async def _run_guarded():
        try:
            await _run()
        except Exception as exc:
            logger.exception("❌ [EVAL] /query _run falhou: %s", exc)
            try:
                await queue.put(json.dumps({"tipo": "error", "msg": str(exc)[:200]}))
                await queue.put(json.dumps({"tipo": "done"}))
            except Exception:
                pass

    asyncio.create_task(_run_guarded())

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
