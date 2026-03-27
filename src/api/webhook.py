# src/api/webhook.py
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.middleware.dev_guard import DevGuard
from src.application.use_cases.process_message import ProcessMessageUseCase
from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
from src.infrastructure.adapters.redis_cache_lock import RedisCacheLock
from src.infrastructure.repositories.postgres_user_repository import (
    PostgresUserRepository,
)
from src.infrastructure.database.connection import AsyncSessionLocal

router = APIRouter()

_gateway = EvolutionAdapter()
_lock    = RedisCacheLock()

@router.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()

    # DevGuard existente — não muda
    from src.infrastructure.redis_client import get_redis_text
    guard = DevGuard(get_redis_text())
    is_valid, identity = await guard.validar(payload)

    if not is_valid:
        return JSONResponse({"status": "ok"})

    # Use Case com injeção de dependência
    async with AsyncSessionLocal() as session:
        repo    = PostgresUserRepository(session)
        use_case = ProcessMessageUseCase(
            user_repo=repo,
            gateway=_gateway,
            lock=_lock,
        )
        await use_case.execute(identity)

    return JSONResponse({"status": "ok"})