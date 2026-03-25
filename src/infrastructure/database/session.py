from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
# Em produção, isso virá do seu .env via pydantic-settings
DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/oraculo"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)