import os
import sys
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
# Mudamos de 'config' para 'settings'
from src.infrastructure.settings import settings

# Certifique-se de que o nome da variável de URL bate com o que está no seu settings.py
# Pode ser settings.database_url ou settings.DATABASE_URL (maiusculo)
DATABASE_URL = getattr(settings, "database_url", getattr(settings, "DATABASE_URL", None))

# ── Escolha do pool ───────────────────────────────────────────────────────────
# Celery (prefork) + engine async + pool persistente = conexões compartilhadas
# entre processos filhos → "another operation in progress" / erros de SSL.
# NullPool (1 conexão por sessão, fechada no fim) evita isso — é o que o
# worker precisa. A API FastAPI roda num processo único e long-lived: aí um
# NullPool só paga handshake TCP a cada request (o hub faz polling + SSE em
# várias abas). Então: NullPool só sob Celery, QueuePool normal na API.
_prog = os.path.basename((sys.argv[0] or "")).lower()
_IS_CELERY = "celery" in _prog

_engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if _IS_CELERY:
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(pool_size=10, max_overflow=5, pool_recycle=1800)

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
