# src/api/metrics.py
"""
Endpoints de métricas internos — acessíveis apenas pelo Admin autenticado.
Substitui completamente o LangSmith.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from src.infrastructure.redis_client import get_redis_text
from src.infrastructure.observability import obs
import json

router = APIRouter(prefix="/metrics", tags=["Métricas Admin"])


@router.get("/overview")
async def overview():
    """Métricas gerais do sistema."""
    r    = get_redis_text()
    logs = [json.loads(l) for l in r.lrange("monitor:logs", 0, 99)]

    total = len(logs)
    if not total:
        return {"total": 0}

    return {
        "total_mensagens":   total,
        "tokens_medio":      sum(l.get("tokens_total", 0) for l in logs) // total,
        "latencia_media_ms": sum(l.get("latencia_ms", 0) for l in logs) // total,
        "por_rota": _agrupar(logs, "rota"),
        "por_nivel": _agrupar(logs, "nivel"),
        "ultimos_erros": obs.get_recent_errors(10),
    }


@router.get("/grafo")
async def grafo_stats():
    """Estatísticas específicas do LangGraph: nós mais acionados, HITL stats."""
    r    = get_redis_text()
    raw  = r.lrange("metrics:grafo", 0, 199)
    data = [json.loads(d) for d in raw]

    confirmacoes  = [d for d in data if d.get("evento") == "hitl_confirmacao"]
    cancelamentos = [d for d in data if d.get("evento") == "hitl_cancelamento"]

    return {
        "total_confirmacoes":  len(confirmacoes),
        "total_cancelamentos": len(cancelamentos),
        "taxa_confirmacao":    (
            len(confirmacoes) / max(len(confirmacoes) + len(cancelamentos), 1) * 100
        ),
        "nos_mais_acionados": _agrupar(data, "no"),
    }


def _agrupar(lista: list, campo: str) -> dict:
    resultado = {}
    for item in lista:
        v = item.get(campo, "desconhecido")
        resultado[v] = resultado.get(v, 0) + 1
    return dict(sorted(resultado.items(), key=lambda x: x[1], reverse=True))