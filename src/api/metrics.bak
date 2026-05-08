"""
Endpoints de métricas internos — acessíveis apenas pelo Admin autenticado.
Substitui completamente o LangSmith e agora exporta para VictoriaMetrics/Prometheus.
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Gauge, Counter
from src.infrastructure.redis_client import get_redis_text
from src.infrastructure.observability import obs
import json

router = APIRouter(prefix="/metrics", tags=["Métricas Admin"])

# ==========================================
# 1. DEFINIÇÃO DAS MÉTRICAS DO PROMETHEUS
# ==========================================

# Gauges: Variam para cima e para baixo (lidos do seu buffer do Redis)
TOTAL_MENSAGENS_GAUGE = Gauge('oraculo_mensagens_buffer', 'Total de mensagens no buffer recente do Redis')
TOKENS_MEDIO_GAUGE = Gauge('oraculo_tokens_medio', 'Média de tokens das últimas mensagens')
LATENCIA_MEDIA_GAUGE = Gauge('oraculo_latencia_media_ms', 'Latência média em ms das últimas mensagens')

# Counters: Sempre crescem. Usados para alimentar o Grafana JSON que você importou.
# (Estes devem ser importados e incrementados no seu novo comandos_Tools_admin.py ou nas rotas da LLM)
TOKEN_USAGE = Counter(
    'gemini_cli_token_usage', 
    'Quantidade de tokens utilizados', 
    ['model', 'department', 'team_id', 'user_email', 'service_name', 'type']
)

TOOL_CALL_COUNT = Counter(
    'gemini_cli_tool_call_count', 
    'Contagem de chamadas de tools da LLM', 
    ['department', 'team_id', 'user_email', 'service_name', 'function_name', 'success', 'decision']
)


# ==========================================
# 2. NOVO ENDPOINT DE SCRAPE (VICTORIAMETRICS)
# ==========================================
@router.get("/prometheus")
async def metrics_prometheus():
    """
    Endpoint consumido pelo Prometheus/VictoriaMetrics.
    Sincroniza os dados atuais do Redis com os Gauges antes de exportar.
    """
    r = get_redis_text()
    logs = [json.loads(l) for l in r.lrange("monitor:logs", 0, 99)]
    total = len(logs)

    # Atualiza os medidores (Gauges) com o estado exato deste momento
    if total > 0:
        TOTAL_MENSAGENS_GAUGE.set(total)
        TOKENS_MEDIO_GAUGE.set(sum(l.get("tokens_total", 0) for l in logs) // total)
        LATENCIA_MEDIA_GAUGE.set(sum(l.get("latencia_ms", 0) for l in logs) // total)
    else:
        TOTAL_MENSAGENS_GAUGE.set(0)
        TOKENS_MEDIO_GAUGE.set(0)
        LATENCIA_MEDIA_GAUGE.set(0)

    # Retorna todas as métricas no formato de texto que o Prometheus entende
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ==========================================
# 3. SEUS ENDPOINTS JSON ORIGINAIS (MANTIDOS)
# ==========================================
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