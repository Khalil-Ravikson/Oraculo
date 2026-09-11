"""
src/application/tasks/pricing_tasks.py
======================================
Atualização periódica do catálogo de preços do OpenRouter.

Esta é a **única** parte da telemetria de custo que fala com a internet. O
resolvedor de preço, que roda no caminho de toda resposta de LLM, só lê o
cache que esta tarefa preenche.

Frequência: a cada `OPENROUTER_CATALOG_TTL_H` horas (12 por padrão). Preço de
modelo muda em escala de semanas, não de minutos — atualizar mais vezes só
gastaria requisição contra um serviço de terceiro sem ganho nenhum.

Falha aqui não quebra nada: o catálogo anterior continua no Redis com TTL de
folga (o dobro do intervalo), e se ele expirar o resolvedor simplesmente pula
essa camada e usa o preço oficial.
"""
from __future__ import annotations

import logging

from src.infrastructure.celery_app import celery_app, run_in_worker_loop

logger = logging.getLogger(__name__)


@celery_app.task(name="atualizar_catalogo_precos")
def atualizar_catalogo_precos_task() -> dict:
    """Baixa e normaliza o catálogo do OpenRouter."""
    from src.infrastructure.observability import openrouter_catalog

    try:
        total = run_in_worker_loop(openrouter_catalog.atualizar())
    except Exception as exc:  # noqa: BLE001 — catálogo nunca derruba o worker
        logger.warning("⚠️ [PRICING] Atualização do catálogo falhou: %s", exc)
        return {"ok": False, "modelos": 0, "erro": str(exc)[:200]}

    return {"ok": total > 0, "modelos": total}
