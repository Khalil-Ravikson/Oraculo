import logging
from fastapi import APIRouter, Depends, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio # Não se esqueça de importar o asyncio no topo do ficheiro!
# Imports da nossa arquitetura
from src.infrastructure.database.session import get_db_session
from src.infrastructure.repositories.postgres_user_repository import PostgresUserRepository
from src.infrastructure.adapters.redis_cache_lock import acquire_lock, release_lock
from src.application.use_cases.messages import MSG_CADASTRO_NECESSARIO

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/evolution/webhook")
async def evolution_webhook(
    request:          Request,
    background_tasks: BackgroundTasks,
    db:               AsyncSession = Depends(get_db_session),
):
    payload = await request.json()

    # ── 1. DevGuard: valida payload Evolution API ─────────────────────────────
    from src.api.middleware.dev_guard import DevGuard
    guard = DevGuard(get_redis_text())
    ok, identity = await guard.validar(payload)
    if not ok:
        return {"status": "ignored", "reason": identity}

    # ── 2. Instancia use case com dependências injectadas ─────────────────────
    use_case = ProcessMessageUseCase(
        user_repo = PostgresUserRepository(db),
        gateway   = EvolutionAdapter(),
        lock      = RedisCacheLock(get_redis_text()),
    )

    # ── 3. Executa o pipeline do Porteiro (async, <5ms) ───────────────────────
    # PostgreSQL verifica quem é o utilizador ANTES de gastar tokens.
    # Se for bloqueado/guest/inativo, responde e termina aqui.
    status = await use_case.execute(identity)
    logger.info("📥 Webhook: %s → %s", identity.get("sender_phone", "?")[-8:], status)

    return {"status": "ok"}
async def processar_mensagem_background(phone: str, text: str, user_data: dict):
    """
    Simula o processamento em background (LangGraph).
    Na Fase 3, chamaremos o Celery ou o Grafo aqui.
    """
    try:
        logger.info(f"🧠 [LANGGRAPH] Processando mensagem de {phone}: '{text}'")
        await asyncio.sleep(4)
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
        # 1. Abre a "gaveta" JSON onde guardamos os detalhes do aluno
        contexto = getattr(student, 'llm_context', {}) or {}
        
        # 2. Monta o pacote de dados limpo para enviar ao LangGraph
        student_data = {
            "nome": student.nome,
            "curso": contexto.get("curso", "Não informado"),
            "periodo": contexto.get("periodo", 1)
        }
        
        # 3. Despacha para o cérebro sem travar a requisição do WhatsApp
        background_tasks.add_task(processar_mensagem_background, phone, text, student_data)
    except Exception as e:
            print(f"❌ [WEBHOOK] Ocorreu um erro ao consultar o banco: {e}")
            await release_lock(phone)
            return {"status": "error"}
        
    print("📍 [WEBHOOK] 7. Requisição HTTP encerrada com sucesso.")
    return {"status": "processing_started"}