import asyncio
import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# 1. Força o Python a enxergar a pasta src/
sys.path.append(str(Path(__file__).resolve().parent.parent))

# 2. Importa nossa configuração blindada e nossos modelos
from src.infrastructure.settings import config as app_config
from src.infrastructure.database.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# --- A BLINDAGEM MÁXIMA ---
# Lemos a URL diretamente do nosso sistema de settings (pydantic),
# ignorando completamente o que estiver escrito no alembic.ini
DATABASE_URL = app_config.database_url

def run_migrations_offline() -> None:
    # Usa a nossa URL blindada
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    ini_section = config.get_section(config.config_ini_section, {})
    
    # Injeta a nossa URL blindada no dicionário do Alembic ANTES dele tentar conectar
    ini_section["sqlalchemy.url"] = DATABASE_URL

    connectable = async_engine_from_config(
        ini_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()