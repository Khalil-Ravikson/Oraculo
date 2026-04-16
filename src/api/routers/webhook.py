from fastapi import APIRouter, Depends, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from src.api.dependencies import get_db_session
from src.infrastructure.redis_client import acquire_lock, release_lock
from src.infrastructure.repositories.pessoa_repository import PessoaRepository

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/evolution/webhook")
async def evolution_webhook(
    request: Request, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session)
):
    """Recebe as mensagens da Evolution API."""
    
    # 1. Pega o payload
    payload = await request.json()
    
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
    lock_acquired = await acquire_lock(phone, ttl_seconds=60)
    if not lock_acquired:
        logger.warning(f"🔒 [LOCK] Mensagem bloqueada para {phone}: '{text}'")
        return {"status": "locked_ignored"}

    try:
        # ==========================================
        # 🚪 REGRA 2: O PORTEIRO POSTGRESQL (Atualizado v4)
        # ==========================================
        user_repo = PessoaRepository(db)
        
        # Passamos o phone e o remote_jid(chat_id) para montar a IdentidadeRica
        pessoa = await user_repo.obter_identidade_por_telefone(phone, remote_jid)

        if not pessoa:
            logger.info(f"👤 [ONBOARDING] Novo usuário detectado: {phone}")
            # TODO: Disparar fluxo de primeiro acesso via BackgroundTasks
            return {"status": "onboarding_started"}

        # Verificando status usando o novo Enum (agora em minúsculo: 'ativo', 'inativo')
        if pessoa.status != "ativo":
            logger.warning(f"🚫 [BLOCKED] Usuário {phone} tem status: {pessoa.status}")
            # TODO: Enviar mensagem "Acesso restrito. Procure a secretaria."
            return {"status": "user_inactive"}

        logger.info(f"✅ [ACESSO LIBERADO] Pessoa: {pessoa.nome} | Role: {pessoa.role}")
        
        # TODO: Acionar o LangGraph jogando o processamento pesado numa Task de Background
        # background_tasks.add_task(rodar_grafo_llm, pessoa, text)

    finally:
        # Libera a tranca do Redis
        await release_lock(phone)

    return {"status": "processed"}