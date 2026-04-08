import json
import logging
from src.infrastructure.redis_client import get_redis_text

logger = logging.getLogger(__name__)

class GetAuditLogsUseCase:
    """
    Recupera os registos de auditoria guardados no Redis.
    Retorna uma lista formatada para o painel de Audit Log.
    """
    async def executar(self, limit: int = 100) -> list:
        try:
            r = get_redis_text()
            # 1. Ajustado a chave para combinar com o admin_commands.py
            raw_logs = r.lrange("audit:log", 0, limit - 1) 
            
            logs_formatados = []
            for item in raw_logs:
                try:
                    log = json.loads(item)
                    # 2. Mapeamento das chaves do JSON para combinar com o HTML
                    logs_formatados.append({
                        "ts": log.get("ts", "--"),
                        "admin_id": log.get("admin", "--"),
                        "action": log.get("action", "--"),
                        "target": "N/A", # O admin_commands.py não guarda target separado
                        "resultado": log.get("result", "--")
                    })
                except Exception:
                    continue
                    
            return logs_formatados
        except Exception as e:
            logger.error(f"❌ Erro ao buscar Audit Logs: {e}")
            return []