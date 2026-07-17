"""
src/application/workers/worker_greeting.py
===========================================
Worker focado exclusivamente em saudações e interações sociais rápidas.
Não aciona RAG e devolve a resposta quase instantaneamente (Fast-Path).
"""
from __future__ import annotations

import logging
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)

@register("greeting")
@celery_app.task(name="worker_greeting", bind=True, max_retries=2, queue="celery")
def worker_greeting_task(self, event: dict) -> dict:
    """
    Gera uma resposta acolhedora baseada no perfil do utilizador.
    """
    session_id = event.get("session_id", "unknown")
    logger.info("👋 [WORKER GREETING] Iniciando saudação para a sessão: %s", session_id)
    
    # Recupera o contexto do utilizador (injetado pelo orquestrador)
    plan_context = event.get("plan_context", {})
    user_context = event.get("user_context", plan_context.get("user_context", {}))
    
    nome = user_context.get("nome", "").strip()
    
    # ── Lógica de Resposta Rápida ──
    _FERRAMENTAS = (
        "\n\n🔧 *Ferramentas do usuário* (demonstração):\n"
        "• !ytb — baixar vídeo do YouTube\n"
        "• !sticker — criar figurinha"
    )

    if nome and nome.lower() not in ["estudante", "guest", "unknown", "admin"]:
        resposta = (
            f"Olá, {nome}! 👋 Sou o Oráculo UEMA.\n\n"
            "Estou aqui para te ajudar com calendários, editais, sigaa, contatos e outras dúvidas institucionais.\n"
            "Como te posso ajudar hoje?"
        ) + _FERRAMENTAS
    else:
        resposta = (
            "Olá! 👋 Sou o Oráculo UEMA.\n\n"
            "Estou aqui para te ajudar com calendários, editais, sigaa, contatos e outras dúvidas institucionais.\n"
            "Como te posso ajudar hoje?"
        ) + _FERRAMENTAS

    logger.info("✅ [WORKER GREETING] Saudação gerada com sucesso.")
    
    # Retorna no formato padrão esperado pelo Cognitive OS
    return {
        "status": "success",
        "worker": "greeting",
        "answer": resposta,
        "error": None
    }