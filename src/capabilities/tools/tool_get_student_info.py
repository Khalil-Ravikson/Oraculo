"""
src/capabilities/tools/tool_get_student_info.py
==================================================
Ex `capabilities/registry.py` (Fase 6 do PLANO_REFATORACAO_SUPERVISOR.md).
Ver débito técnico documentado em `capabilities/tools/__init__.py`.
"""
from __future__ import annotations

from src.capabilities.registry import tool


@tool("get_student_info")
async def get_info(user_id: str) -> dict:
    """Retorna dados cadastrais do aluno (leitura — sem confirmação necessária)."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.postgres_user_repository import PostgresUserRepository

    async with AsyncSessionLocal() as session:
        repo  = PostgresUserRepository(session)
        aluno = await repo.get_by_id(user_id)
        if not aluno:
            raise ValueError("Usuário não encontrado.")

    return {
        "mensagem": (
            f"📋 *Seus dados cadastrais:*\n\n"
            f"👤 Nome: {aluno.nome}\n"
            f"📧 E-mail: {aluno.email}\n"
            f"📚 Curso: {aluno.curso or 'Não informado'}\n"
            f"🎓 Matrícula: {aluno.matricula or 'Não informada'}\n"
            f"📍 Centro: {aluno.centro or 'Não informado'}"
        )
    }
