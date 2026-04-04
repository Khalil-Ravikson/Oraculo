from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database.session import AsyncSessionLocal

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Gera uma sessão assíncrona do banco e garante o fechamento no final."""
    async with AsyncSessionLocal() as session:
        yield session