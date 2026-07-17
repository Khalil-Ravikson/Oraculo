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


async def atualizar_email_por_telefone(telefone: str, novo_email: str) -> bool:
    """Escopo por telefone (identidade do próprio remetente) — evita que
    qualquer usuário atualize o e-mail de outra matrícula só por adivinhar o número."""
    from src.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("UPDATE pessoas SET email=:e WHERE telefone=:t"),
            {"e": novo_email, "t": telefone}
        )
        await db.commit()
        return result.rowcount > 0
