# src/application/tasks/__init__.py
from src.infrastructure.celery_app import celery_app

# Re-exporta para compatibilidade com código existente
from .process_message_task import processar_mensagem_task
from .admin_tasks import ingerir_documento_task, executar_comando_admin_task
from .notification_tasks import verificar_e_notificar_prazos

__all__ = [
    "celery_app",
    "processar_mensagem_task",
    "ingerir_documento_task",
    "executar_comando_admin_task",
    "verificar_e_notificar_prazos",
]