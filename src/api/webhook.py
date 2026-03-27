import logging
from fastapi import APIRouter, Depends, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

# Imports da nossa arquitetura
from src.infrastructure.database.session import get_db_session
from src.infrastructure.repositories.postgres_user_repository import PostgresUserRepository
from src.infrastructure.adapters.redis_cache_lock import acquire_lock, release_lock
from src.application.use_cases.messages import MSG_CADASTRO_NECESSARIO

logger = logging.getLogger(__name__)
router = APIRouter()

async def processar_mensagem_background(phone: str, text: str, user_data: dict):
    """
    Simula o processamento em background (LangGraph).
    Na Fase 3, chamaremos o Celery ou o Grafo aqui.
    """
    try:
        logger.info(f"🧠 [LANGGRAPH] Processando mensagem de {phone}: '{text}'")
        # Aqui entrará: app_graph.ainvoke({"user_phone": phone, "current_input": text, ...})
    finally:
        # Só liberamos o lock quando o bot terminar de processar e responder
        await release_lock(phone)
        logger.info(f"🔓 [LOCK] Trava liberada para {phone}")

@router.post("/evolution/webhook")
async def evolution_webhook(
    request: Request, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session)
):
    print("📍 [WEBHOOK] 1. Requisição chegou na rota (Banco Conectado com sucesso)!")
    
    try:
        payload = await request.json()
        data = payload.get("data", {})
        remote_jid = data.get("key", {}).get("remoteJid", "")
        phone = remote_jid.split("@")[0]
        text = data.get("message", {}).get("conversation", "")
        print(f"📍 [WEBHOOK] 2. Payload extraído: Telefone {phone} | Msg: '{text}'")
    except Exception as e:
        print(f"❌ Erro no payload: {e}")
        return {"status": "ok"}

    print("📍 [WEBHOOK] 3. Tentando adquirir Lock no Redis...")
    try:
        lock_acquired = await acquire_lock(phone, ttl_seconds=60)
        print(f"📍 [WEBHOOK] 4. Lock adquirido? {lock_acquired}")
        if not lock_acquired:
            return {"status": "locked_ignored"}
    except Exception as e:
        print(f"❌ [WEBHOOK] O Redis travou: {e}")
        return {"status": "error"}

    try:
        print("📍 [WEBHOOK] 5. Indo buscar no PostgreSQL...")
        user_repo = PostgresUserRepository(db)
        student = await user_repo.get_by_phone(phone)

        if not student:
            print("📍 [WEBHOOK] 6. [GUEST] Número não cadastrado!")
            await release_lock(phone)
            return {"status": "onboarding_started"}

        print(f"📍 [WEBHOOK] 6. [ACESSO LIBERADO] Aluno: {student.nome}")
        background_tasks.add_task(processar_mensagem_background, phone, text, {"curso": student.curso})

    except Exception as e:
        print(f"❌ [WEBHOOK] Ocorreu um erro ao consultar o banco: {e}")
        await release_lock(phone)
        return {"status": "error"}

    print("📍 [WEBHOOK] 7. Requisição HTTP encerrada com sucesso.")
    return {"status": "processing_started"}