"""
SSE endpoint para streaming de steps da OracleChain ao frontend.
GET /api/chain/stream?session_id=xxx&message=yyy
"""
from __future__ import annotations
import asyncio
import json
import logging
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from src.application.chain.oracle_chain import StepResult, get_oracle_chain

logger = logging.getLogger(__name__)
sse_router = APIRouter(prefix="/api/chain", tags=["chain-sse"])


@sse_router.get("/stream")
async def stream_chain(
    message: str = Query(...),
    session_id: str = Query(...),
):
    """
    Retorna Server-Sent Events com cada step da chain.
    
    Frontend consome:
      const es = new EventSource(`/api/chain/stream?message=...&session_id=...`);
      es.onmessage = (e) => {
        const step = JSON.parse(e.data);
        // step: {name, status, detail, latency_ms}
        if (step.name === "DONE") { es.close(); }
      };
    """
    queue: asyncio.Queue[StepResult] = asyncio.Queue()
    chain = get_oracle_chain()

    # Contexto mínimo para streaming público — contexto rico vem do Redis
    user_context = {"role": "student"}

    async def event_generator():
        # Inicia a chain em background
        task = asyncio.create_task(
            chain.invoke(
                message=message,
                session_id=session_id,
                user_context=user_context,
                debug_queue=queue,
            )
        )
        try:
            while True:
                try:
                    step = await asyncio.wait_for(queue.get(), timeout=30.0)
                    payload = json.dumps({
                        "name":       step.name,
                        "status":     step.status,
                        "detail":     step.detail,
                        "latency_ms": step.latency_ms,
                        "data":       step.data,
                    }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

                    if step.name == "DONE":
                        break
                except asyncio.TimeoutError:
                    yield "data: {\"name\": \"timeout\", \"status\": \"error\"}\n\n"
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx: desativa buffering
        },
    )