"""
src/capabilities/persistence/ticket_repository.py
====================================================
Ex SQL cru embutido em `application/workers/worker_action.py` (Fase 6 do
PLANO_REFATORACAO_SUPERVISOR.md, seção 2.6). Função async pura, sem decisão
de negócio.
"""
from __future__ import annotations


async def atualizar_email_por_matricula(matricula: str, novo_email: str) -> None:
    from src.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE pessoas SET email=:e WHERE matricula=:m"),
            {"e": novo_email, "m": matricula}
        )
        await db.commit()
