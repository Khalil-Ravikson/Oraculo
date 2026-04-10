# src/api/routers/webhook.py
from fastapi import APIRouter, BackgroundTasks, Request, status
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/webhook", tags=["Webhook"])

@router.post("/evolution", status_code=status.HTTP_200_OK)
async def evolution_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """
    Fluxo em 8 etapas:
    1. Parse JSON bruto
    2. DevGuard (dedup, filtros de mídia/grupo, resolução @lid)
    3. Lock Redis por phone (bloqueia duplicatas)
    4. PostgreSQL Porteiro (verifica cadastro e status)
    5. Monta IdentidadeRica com contexto do aluno
    6. XADD no Redis Stream (durabilidade)
    7. Celery .apply_async (processa o grafo em background)
    8. Retorna 200 imediatamente (a Evolution API não espera resposta)
    """
    # ── 1. Parse ──────────────────────────────────────────────────────────────
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "invalid_json"}, status_code=400)

    # ── 2. DevGuard ───────────────────────────────────────────────────────────
    from src.api.middleware.dev_guard import DevGuard
    from src.infrastructure.redis_client import get_redis_text

    r = get_redis_text()
    guard = DevGuard(r)
    ok, resultado = await guard.validar(payload)

    if not ok:
        return JSONResponse({"status": "blocked", "reason": resultado})

    identity = resultado   # dict: chat_id, sender_phone, body, push_name, ...

    # ── 3. Lock Redis (anti-spam e processamento duplo) ───────────────────────
    phone    = identity["sender_phone"]
    lock_key = f"lock:msg:{phone}"

    if r.get(lock_key):
        # Mensagens filler (ok, aguardando...) são silenciosamente descartadas
        import re
        FILLER = re.compile(
            r"^(ok|certo|tá|ta|blz|aguardando|no aguardo|👍|✅|sim|não)\s*[.!]?$",
            re.IGNORECASE,
        )
        if FILLER.match(identity.get("body", "").strip()):
            return JSONResponse({"status": "ignored_filler"})
        return JSONResponse({"status": "locked"})

    # ── 4. PostgreSQL — O PORTEIRO ────────────────────────────────────────────
    # Esta etapa é o "short-circuit" que evita gastar tokens com não-cadastrados.
    from src.infrastructure.database import AsyncSessionLocal
    from src.infrastructure.repositories.postgres_user_repository import PostgresUserRepository

    async with AsyncSessionLocal() as session:
        repo  = PostgresUserRepository(session)
        aluno = await repo.get_by_phone(phone)

    # Administrador: bypass total do banco (hardcoded no .env)
    from src.infrastructure.settings import settings
    import re as _re
    admin_numbers = {_re.sub(r"\D", "", n) for n in (settings.ADMIN_NUMBERS or "").split(",") if n.strip()}
    eh_admin = _re.sub(r"\D", "", phone) in admin_numbers

    if not eh_admin:
        if not aluno:
            # Inicia fluxo de registo conversacional (não gasta tokens)
            background_tasks.add_task(_iniciar_registro, identity)
            return JSONResponse({"status": "onboarding_started"})

        if aluno.status == "banido":
            return JSONResponse({"status": "banned"})

        if aluno.status == "pendente":
            background_tasks.add_task(
                _enviar_msg, identity["chat_id"],
                "⏳ Seu cadastro está em análise. Em breve receberá a confirmação!"
            )
            return JSONResponse({"status": "pending"})

        if aluno.status == "inativo":
            background_tasks.add_task(
                _enviar_msg, identity["chat_id"],
                "❌ Vínculo inativo. Contacte a secretaria do seu curso."
            )
            return JSONResponse({"status": "inactive"})

    # ── 5. Monta IdentidadeRica ───────────────────────────────────────────────
    ctx = getattr(aluno, "llm_context", {}) or {} if aluno else {}
    identity_rica = {
        **identity,
        "user_id":   str(getattr(aluno, "id", phone)) if aluno else phone,
        "nome":      getattr(aluno, "nome", "Admin") if aluno else "Admin",
        "role":      "admin" if eh_admin else getattr(aluno, "role", "estudante"),
        "status":    "ativo" if eh_admin else getattr(aluno, "status", "ativo"),
        "is_admin":  eh_admin,
        "curso":     ctx.get("curso") or getattr(aluno, "curso", None),
        "periodo":   ctx.get("periodo") or getattr(aluno, "semestre_ingresso", None),
        "matricula": getattr(aluno, "matricula", None),
        "centro":    str(aluno.centro.value) if getattr(aluno, "centro", None) else None,
    }

    # ── 6. XADD — Durabilidade (Redis Stream) ────────────────────────────────
    from src.infrastructure.message_stream import get_message_stream
    stream_id = get_message_stream().publish(identity_rica)

    # ── 7. Celery — Processamento Assíncrono ──────────────────────────────────
    from src.application.tasks.process_message_task import processar_mensagem_task
    processar_mensagem_task.apply_async(
        args  = [identity_rica, stream_id],
        queue = "admin" if eh_admin else "default",
    )

    # ── 8. Retorno imediato (Evolution API não espera) ────────────────────────
    return JSONResponse({"status": "accepted"})


async def _iniciar_registro(identity: dict) -> None:
    from src.application.use_cases.handle_registration import HandleRegistrationUseCase
    from src.services.evolution_service import EvolutionService
    await HandleRegistrationUseCase(EvolutionService()).execute(
        chat_id=identity["chat_id"],
        phone  =identity["sender_phone"],
        body   =identity.get("body", ""),
    )


async def _enviar_msg(chat_id: str, texto: str) -> None:
    from src.services.evolution_service import EvolutionService
    await EvolutionService().enviar_mensagem(chat_id, texto)