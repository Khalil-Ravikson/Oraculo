"""
src/capabilities/tools/tool_update_student_telefone.py
=========================================================
Ex `capabilities/registry.py` (Fase 6 do PLANO_REFATORACAO_SUPERVISOR.md).
Ver débito técnico documentado em `capabilities/tools/__init__.py`.
"""
from __future__ import annotations

from src.capabilities.registry import tool


@tool("update_student_telefone")
async def update_telefone(user_id: str, novo_valor: str) -> dict:
    """Atualiza telefone do aluno — mantém consistência com o WhatsApp."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.postgres_user_repository import PostgresUserRepository

    async with AsyncSessionLocal() as session:
        repo  = PostgresUserRepository(session)
        aluno = await repo.get_by_id(user_id)
        if not aluno:
            raise ValueError("Usuário não encontrado.")
        await repo.update(aluno, {"telefone": novo_valor})
        await session.commit()

    return {"mensagem": f"Telefone atualizado para *{novo_valor}*!"}
