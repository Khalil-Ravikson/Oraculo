# src/tools/crud_tools.py
"""
Tools CRUD — executadas APENAS após confirmação do usuário (HITL).
Cada tool valida que user_id está operando sobre seus próprios dados.
"""
from __future__ import annotations
import logging
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories.postgres_user_repository import (
    PostgresUserRepository,
)

logger = logging.getLogger(__name__)

_TOOL_REGISTRY: dict[str, callable] = {}

def tool(name: str):
    """Decorator para registrar tools."""
    def decorator(fn):
        _TOOL_REGISTRY[name] = fn
        return fn
    return decorator


async def executar_tool(tool_name: str, args: dict) -> dict:
    """Dispatcher central de tools CRUD."""
    fn = _TOOL_REGISTRY.get(tool_name)
    if not fn:
        raise ValueError(f"Tool '{tool_name}' não encontrada.")
    return await fn(**args)


@tool("update_student_email")
async def update_email(user_id: str, novo_valor: str) -> dict:
    """Atualiza e-mail do aluno autenticado."""
    import re
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


@tool("update_student_telefone")
async def update_telefone(user_id: str, novo_valor: str) -> dict:
    """Atualiza telefone do aluno — mantém consistência com o WhatsApp."""
    async with AsyncSessionLocal() as session:
        repo  = PostgresUserRepository(session)
        aluno = await repo.get_by_id(user_id)
        if not aluno:
            raise ValueError("Usuário não encontrado.")
        await repo.update(aluno, {"telefone": novo_valor})
        await session.commit()

    return {"mensagem": f"Telefone atualizado para *{novo_valor}*!"}


@tool("get_student_info")
async def get_info(user_id: str) -> dict:
    """Retorna dados cadastrais do aluno (leitura — sem confirmação necessária)."""
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