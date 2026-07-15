"""
SSE endpoint para streaming de steps da OracleChain ao frontend.
Adaptado para o novo formato do Cognitive OS (Multi-Agente).
GET /api/chain/stream?session_id=xxx&message=yyy
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

# 🔥 Importamos o novo orquestrador do sistema
from src.application.runtime.dispatcher import processar

logger = logging.getLogger(__name__)
sse_router = APIRouter(prefix="/api/chain", tags=["chain-sse"])


@sse_router.get("/stream")
async def stream_chain(
    message: str = Query(...),
    session_id: str = Query(...),
):
    """
    Retorna Server-Sent Events com o resultado do Cognitive OS.
    Mantém o contrato de dados que o frontend (JavaScript) espera.
    """
    # Contexto mínimo para streaming público
    user_context = {"role": "student"}

    async def event_generator():
        t0 = time.monotonic()
        
        # 1. Evento de Início (Frontend mostra "Processando...")
        yield f"data: {json.dumps({'name': 'start', 'status': 'ok', 'detail': 'Iniciando Cognitive OS', 'latency_ms': 0})}\n\n"
        
        try:
            # 2. Executa a inteligência
            result = await processar(
                message=message,
                session_id=session_id,
                user_context=user_context,
                history=""
            )
            
            latencia = int((time.monotonic() - t0) * 1000)
            
            # 3. Evento de Resposta Pronta
            if getattr(result, "error", None):
                payload = {
                    "name": "generate", 
                    "status": "error", 
                    "detail": result.error, 
                    "latency_ms": latencia
                }
            else:
                payload = {
                    "name": "generate", 
                    "status": "ok", 
                    "detail": result.answer, 
                    "latency_ms": latencia,
                    "data": {"route": getattr(result, "rota", "GERAL")}
                }
            
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                
        except Exception as e:
            logger.error(f"Erro no SSE Stream: {e}")
            yield f"data: {json.dumps({'name': 'error', 'status': 'error', 'detail': str(e), 'latency_ms': 0})}\n\n"
            
        # 4. Evento Finalizador (Frontend fecha a conexão SSE)
        yield f"data: {json.dumps({'name': 'DONE', 'status': 'ok', 'detail': '', 'latency_ms': 0})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx: desativa buffering
        },
    )