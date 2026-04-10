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

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.middleware.auth_middleware import TokenPayload
from src.application.use_cases.admin_auth import get_admin_auth
from src.infrastructure.settings import settings
from pydantic import BaseModel
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
        name="hub/dashboard.html",
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

    

class WebChatRequest(BaseModel):
    message: str

@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    """Página do Simulador de Chat Web."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="hub/chat.html",
        context={"request": request, "username": payload.sub},
    )

@router.post("/chat/send")
async def chat_send(request: Request, data: WebChatRequest):
    """Endpoint REST que o JS do frontend vai chamar."""
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}
    
    from src.application.use_cases.simulate_web_chat import SimulateWebChatUseCase
    
    # ID da sessão único para este admin no simulador web
    session_id = f"web_session_{payload.sub}" 
    
    use_case = SimulateWebChatUseCase()
    resposta = await use_case.executar(
        session_id=session_id, 
        mensagem=data.message, 
        admin_name=payload.sub
    )
    
    return {"response": resposta}

import asyncio
import json
from fastapi import Request
from fastapi.responses import StreamingResponse, JSONResponse, RedirectResponse



@router.get("/chat/stream")
async def chat_stream(request: Request, msg: str = "", thread_id: str = ""):
    """SSE: executa o grafo e streama o resultado passo a passo."""
    payload = _verificar_cookie(request)
    if not payload:
        return RedirectResponse("/hub/login", status_code=302)

    if not msg or not thread_id:
        return JSONResponse({"erro": "msg e thread_id obrigatórios"}, status_code=400)

    async def _generator():
        from src.application.graph.builder import get_compiled_graph, get_graph_config
        from src.application.graph.state import OracleState

        graph  = get_compiled_graph()
        config = get_graph_config(thread_id=thread_id)

        # Verifica se existe estado HITL pendente para este thread
        try:
            snapshot = await graph.aget_state(config)
            has_pending = (
                snapshot.values.get("pending_confirmation") is not None
                and snapshot.values.get("confirmation_result") not in ("confirmed", "cancelled")
            )
        except Exception:
            has_pending = False
            snapshot = None

        # Monta o input correcto
        if has_pending:
            # Retomada HITL: injeta a resposta do utilizador no estado existente
            input_state = {"current_input": msg, "messages": []}
        else:
            # Nova conversa ou novo turno
            input_state = OracleState.from_identity({
                "user_id":   f"sim_{thread_id}",
                "chat_id":   f"sim_{thread_id}@sim",
                "nome":      "Simulador Admin",
                "role":      "admin",
                "status":    "ativo",
                "is_admin":  True,
                "body":      msg,
                "has_media": False,
            })

        yield f"data: {json.dumps({'type': 'start', 'hitl': has_pending})}\n\n"

        try:
            async for chunk in graph.astream(input_state, config=config, stream_mode="values"):
                # Streama cada mudança de estado relevante
                route = chunk.get("route", "")
                if route:
                    yield f"data: {json.dumps({'type': 'route', 'route': route})}\n\n"

                pending = chunk.get("pending_confirmation")
                if pending:
                    yield f"data: {json.dumps({'type': 'hitl', 'question': pending})}\n\n"

                response = chunk.get("final_response")
                if response:
                    yield f"data: {json.dumps({'type': 'response', 'text': response, 'crag': chunk.get('crag_score', 0)})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'msg': str(e)[:200]})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/audit/data")
async def audit_data(request: Request):
    """Endpoint REST para alimentar a tabela de Auditoria."""
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}
        
    from src.application.use_cases.get_audit_logs import GetAuditLogsUseCase
    use_case = GetAuditLogsUseCase()
    # CORRECT: Added await
    logs = await use_case.executar() 
    return {"logs": logs}


@router.get("/users/data")
async def users_data(request: Request, role: str = ""):
    """Endpoint REST para alimentar a tabela de Utilizadores."""
    payload = _verificar_cookie(request)
    if not payload:
        return {"error": "Não autorizado"}
        
    from src.application.use_cases.get_users_list import GetUsersListUseCase
    users = await GetUsersListUseCase().executar(role)
    return {"users": users}


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