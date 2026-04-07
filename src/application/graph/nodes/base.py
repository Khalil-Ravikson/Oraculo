# src/application/graph/nodes/base.py
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

async def node_greeting(state: "OracleState") -> dict:
    """Resposta de saudação personalizada — 0 tokens de RAG."""
    from src.application.use_cases.messages import MSG_BOAS_VINDAS_USUARIO
    nome = (state.get("user_name") or "").split()[0] or "Olá"
    return {
        "final_response": MSG_BOAS_VINDAS_USUARIO.format(nome=nome),
    }

async def node_respond(state: "OracleState") -> dict:
    """Nó terminal — registra métricas no Redis e retorna sem alteração de estado."""
    try:
        import json, datetime
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        entrada = json.dumps({
            "ts":          datetime.datetime.now().isoformat(),
            "user_id":     state.get("user_id", ""),
            "role":        state.get("user_role", ""),
            "route":       state.get("route", ""),
            "crag_score":  state.get("crag_score", 0.0),
            "pergunta":    (state.get("current_input") or "")[:100],
            "resposta":    (state.get("final_response") or "")[:200],
        }, ensure_ascii=False)
        r.lpush("monitor:logs", entrada)
        r.ltrim("monitor:logs", 0, 499)
    except Exception:
        pass

    return {}