from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Mudamos de 'config' para 'settings'
from src.infrastructure.settings import settings

# Certifique-se de que o nome da variável de URL bate com o que está no seu settings.py
# Pode ser settings.database_url ou settings.DATABASE_URL (maiusculo)
DATABASE_URL = getattr(settings, "database_url", getattr(settings, "DATABASE_URL", None))

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session