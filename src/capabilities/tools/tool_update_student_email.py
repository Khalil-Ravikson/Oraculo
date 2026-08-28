"""
src/capabilities/tools/tool_update_student_email.py
======================================================
Capability: atualiza o e-mail da pessoa. Exige confirmação (§S) — é escrita.
"""
from __future__ import annotations

import logging
import re

from src.capabilities.registry import tool

logger = logging.getLogger(__name__)

_RE_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[a-z]{2,}$", re.IGNORECASE)


@tool(
    "update_student_email",
    descricao="Atualiza o e-mail cadastral da pessoa.",
    permissoes=("pessoa:write",),
    confirmacao=True,
)
async def update_email(user_id: str, novo_valor: str) -> dict:
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.pessoa_repository import PessoaRepository

    if not _RE_EMAIL.match(novo_valor or ""):
        raise ValueError("E-mail inválido.")

    async with AsyncSessionLocal() as session:
        aluno = await PessoaRepository(session).get_by_id(int(user_id))
        if not aluno:
            raise ValueError("Pessoa não encontrada.")
        aluno.email = novo_valor
        await session.commit()

    logger.info("✅ [CAPABILITY] E-mail atualizado: user=%s", user_id)
    return {"mensagem": f"E-mail atualizado para *{novo_valor}* com sucesso!"}
