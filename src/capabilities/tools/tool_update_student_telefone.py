"""
src/capabilities/tools/tool_update_student_telefone.py
=========================================================
Capability: atualiza o telefone da pessoa. Exige confirmação (§S).
"""
from __future__ import annotations

import logging

from src.capabilities.registry import tool

logger = logging.getLogger(__name__)


@tool(
    "update_student_telefone",
    descricao="Atualiza o telefone cadastral da pessoa.",
    permissoes=("pessoa:write",),
    confirmacao=True,
)
async def update_telefone(user_id: str, novo_valor: str) -> dict:
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.pessoa_repository import PessoaRepository

    numero = "".join(ch for ch in (novo_valor or "") if ch.isdigit())
    if len(numero) < 10:
        raise ValueError("Telefone inválido (mínimo 10 dígitos com DDD).")

    async with AsyncSessionLocal() as session:
        aluno = await PessoaRepository(session).get_by_id(int(user_id))
        if not aluno:
            raise ValueError("Pessoa não encontrada.")
        aluno.telefone = numero
        await session.commit()

    logger.info("✅ [CAPABILITY] Telefone atualizado: user=%s", user_id)
    return {"mensagem": f"Telefone atualizado para *{numero}*!"}
