"""
src/capabilities/tools/tool_update_student_email.py
======================================================
Ex `capabilities/registry.py` (Fase 6 do PLANO_REFATORACAO_SUPERVISOR.md).
Ver débito técnico documentado em `capabilities/tools/__init__.py`.
"""
from __future__ import annotations

import logging

from src.capabilities.registry import tool

logger = logging.getLogger(__name__)


@tool("update_student_email")
async def update_email(user_id: str, novo_valor: str) -> dict:
    """Atualiza e-mail do aluno autenticado."""
    import re
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.postgres_user_repository import PostgresUserRepository

    if not re.match(r"^[\w.+-]+@[\w-]+\.[a-z]{2,}$", novo_valor, re.IGNORECASE):
        raise ValueError("E-mail inválido.")

    async with AsyncSessionLocal() as session:
        repo  = PostgresUserRepository(session)
        aluno = await repo.get_by_id(user_id)
        if not aluno:
            raise ValueError("Usuário não encontrado.")
        await repo.update(aluno, {"email": novo_valor})
        await session.commit()

    logger.info("✅ E-mail atualizado: user=%s", user_id)
    return {"mensagem": f"E-mail atualizado para *{novo_valor}* com sucesso!"}
