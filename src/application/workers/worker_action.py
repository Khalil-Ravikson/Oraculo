@celery_app.task(name="worker_action", bind=True, max_retries=2, queue="action")
def worker_action_task(self, event: dict) -> dict:
    """Executa ações críticas pós-confirmação HITL."""
    action = event.get("action")
    args   = event.get("args", {})
    plan_id = event.get("plan_id", "")
    
    HANDLERS = {
        "update_student_email":    _crud_update_email,
        "abrir_chamado_glpi":      _glpi_abrir_chamado,
        "enviar_email":            _email_enviar,
    }
    handler = HANDLERS.get(action)
    if not handler:
        return {"status": "error", "error": f"Action desconhecida: {action}"}
    
    return asyncio.run(handler(args, plan_id))