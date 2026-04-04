from fastapi import APIRouter, Depends, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import get_db_session
from src.infrastructure.cache.redis_client import acquire_lock, release_lock
from src.infrastructure.repositories.postgres_user_repository import PostgresUserRepository

router = APIRouter()

@router.post("/evolution/webhook")
async def evolution_webhook(
    request: Request, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session)
):
    """Recebe as mensagens da Evolution API."""
    
    # 1. Pega o payload (Simplificado para o exemplo)
    payload = await request.json()
    
    # Evolution API envia a mensagem dentro de uma estrutura específica. 
    # Aqui pegamos o número de forma segura (Ajuste conforme o evento exato que você ativou na API)
    try:
        message_data = payload.get("data", {}).get("message", {})
        remote_jid = payload.get("data", {}).get("key", {}).get("remoteJid", "")
        
        # Ignora mensagens de status ou grupos
        if "@g.us" in remote_jid or remote_jid == "status@broadcast":
            return {"status": "ignored"}
            
        phone = remote_jid.split("@")[0]
        text = message_data.get("conversation") or message_data.get("extendedTextMessage", {}).get("text", "")
        
        if not text or not phone:
            return {"status": "ok"}
            
    except Exception:
        # Se o payload não for de mensagem, apenas retorna 200
        return {"status": "ok"}

    # ==========================================
    # 🚀 REGRA 4: LOCKING NO REDIS (Proteção de Latência)
    # ==========================================
    # Tentamos adquirir o lock. Se retornar False, o sistema já está processando
    # uma mensagem deste aluno. Ignoramos silenciosamente e salvamos tokens!
    lock_acquired = await acquire_lock(phone, ttl_seconds=60)
    if not lock_acquired:
        print(f"🔒 [LOCK] Mensagem bloqueada para {phone}: '{text}'")
        return {"status": "locked_ignored"}

    try:
        # ==========================================
        # 🚪 REGRA 2: O PORTEIRO POSTGRESQL
        # ==========================================
        user_repo = PostgresUserRepository(db)
        student = await user_repo.get_by_phone(phone)

        if not student:
            print(f"👤 [ONBOARDING] Novo usuário detectado: {phone}")
            # TODO: Disparar fluxo de primeiro acesso via BackgroundTasks
            # background_tasks.add_task(iniciar_onboarding, phone)
            return {"status": "onboarding_started"}

        if student.status != "Ativo":
            print(f"🚫 [BLOCKED] Usuário {phone} tem status: {student.status}")
            # TODO: Enviar mensagem "Acesso restrito. Procure a secretaria."
            return {"status": "user_inactive"}

        print(f"✅ [ACESSO LIBERADO] Aluno: {student.nome} | Matrícula: {student.matricula}")
        
        # TODO na Fase 3/4: Enviar "Aguarde..." via API do WhatsApp
        # TODO na Fase 3/4: Acionar o LangGraph jogando o processamento pesado numa Task de Background
        # background_tasks.add_task(rodar_grafo_llm, student, text)

    finally:
        # NOTA: O release_lock real acontecerá no final da Task de Background (LangGraph).
        # Por enquanto, como o processamento é instantâneo, liberamos logo.
        await release_lock(phone)

    return {"status": "processed"}