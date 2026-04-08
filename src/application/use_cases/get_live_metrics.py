import json
import logging
from src.infrastructure.redis_client import get_redis_text

logger = logging.getLogger(__name__)

class GetLiveMetricsUseCase:
    """
    Agrega os logs salvos pelo `node_respond` no Redis para gerar
    métricas em tempo real para o Dashboard.
    """
    
    def executar(self) -> dict:
        try:
            r = get_redis_text()
            # Puxa os últimos 50 logs do Oráculo
            logs_raw = r.lrange("monitor:logs", 0, 49)
            
            total_mensagens = len(logs_raw)
            rotas_count = {}
            soma_crag = 0.0
            logs_formatados = []
            
            for log_bytes in logs_raw:
                try:
                    log = json.loads(log_bytes)
                    rota = log.get("route", "desconhecida")
                    crag = log.get("crag_score", 0.0)
                    
                    # Contagem de Rotas
                    rotas_count[rota] = rotas_count.get(rota, 0) + 1
                    soma_crag += crag
                    
                    # Guarda os 5 mais recentes para a tabela do painel
                    if len(logs_formatados) < 5:
                        logs_formatados.append({
                            "ts": log.get("ts", "")[11:19], # Extrai só a hora HH:MM:SS
                            "user": log.get("user_id", "Anônimo"),
                            "rota": rota.upper(),
                            "crag": round(crag, 2)
                        })
                except Exception:
                    continue
            
            media_crag = (soma_crag / total_mensagens) if total_mensagens > 0 else 0.0

            return {
                "total_interacoes": total_mensagens,
                "media_crag": round(media_crag, 2),
                "rotas": rotas_count,
                "recent_logs": logs_formatados
            }

        except Exception as e:
            logger.error(f"Erro ao gerar métricas: {e}")
            return {"erro": str(e)}