"""
api/monitor.py — Router exclusivo do Dashboard de Monitoramento
================================================================

Montado em main.py com prefix="/monitor" (ou /hub/monitor dependendo da tua montagem):
  GET  /monitor            → dashboard HTML (Jinja2)
  GET  /monitor/stream     → SSE (Server-Sent Events) para métricas em tempo real
  GET  /monitor/{user_id}  → dados de um utilizador específico (legado/suporte)
  POST /monitor/reset      → limpa logs de monitoramento (admin)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from src.infrastructure.redis_client import get_redis_text
from src.infrastructure.settings import settings
from src.api.hub import _verificar_cookie # Import necessário para SSE# Import necessário para SSE
from src.application.use_cases.get_live_metrics import GetLiveMetricsUseCase

logger    = logging.getLogger(__name__)

# Definimos o router apenas UMA vez
router    = APIRouter(tags=["Monitor"])

# Usamos a pasta de templates principal
templates = Jinja2Templates(directory="templates")


# =============================================================================
# Dashboard HTML
# =============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """
    Dashboard principal — renderiza templates/monitor/dashboard.html via Jinja2.
    Os dados reais vêm do endpoint /monitor/stream via SSE no JS.
    """
    contexto = {
        "request":    request,
        "modelo":     settings.GEMINI_MODEL,
        "redis_url":  settings.REDIS_URL,
        "dev_mode":   settings.DEV_MODE,
        "version":    "5.0",
        "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    return templates.TemplateResponse(
        request=request,
        name="monitor/dashboard.html",
        context=contexto
    )


# =============================================================================
# Stream API — SSE (Server-Sent Events) para o Live Dashboard
# =============================================================================

@router.get("/stream")
async def monitor_stream(request: Request):
    """SSE: Envia métricas a cada 2 segundos para o FrontEnd."""
    payload = _verificar_cookie(request)
    if not payload:
        # Retorna um JSON de erro formatado para o SSE se não estiver autenticado
        return StreamingResponse(
            iter([f"data: {json.dumps({'erro': 'Não autorizado'})}\n\n"]),
            media_type="text/event-stream"
        )

    use_case = GetLiveMetricsUseCase()

    async def event_generator():
        while True:
            # Se o cliente desconectar (fechar a aba), o loop para
            if await request.is_disconnected():
                break
            
            metricas = use_case.executar()
            yield f"data: {json.dumps(metricas)}\n\n"
            
            await asyncio.sleep(2) # Atualiza a cada 2 segundos

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# =============================================================================
# Endpoints Auxiliares (Legado / Utilitários)
# =============================================================================

@router.get("/{user_id}")
async def monitor_usuario(user_id: str):
    """Dados de monitoramento de um utilizador específico."""
    r = get_redis_text()
    try:
        dados = r.hgetall(f"monitor:user:{user_id}")
    except Exception:
        dados = {}

    return {
        "user_id":       user_id,
        "total_msgs":    int(dados.get("total_msgs", 0)),
        "total_tokens":  int(dados.get("total_tokens", 0)),
        "avg_latencia":  (
            int(dados.get("total_latencia", 0)) //
            max(int(dados.get("total_msgs", 1)), 1)
        ),
        "ultima_msg":    dados.get("ultima_msg", ""),
        "nivel":         dados.get("nivel", "GUEST"),
    }


@router.post("/reset")
async def reset_logs(x_admin_key: str = Header(None, alias="X-Admin-Key")):
    """Limpa todos os logs de monitoramento (apenas admin)."""
    if not settings.ADMIN_API_KEY or x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    r = get_redis_text()
    r.delete("monitor:logs")
    return {"status": "ok", "mensagem": "Logs de monitoramento limpos."}