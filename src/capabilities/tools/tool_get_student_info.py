"""
src/capabilities/tools/tool_get_student_info.py
==================================================
Capability: lê os dados cadastrais da pessoa. Registrada em
`capabilities/registry.py` com manifesto (§S). Vinculada ao agente `tickets`
via `agente_tools` (migration 012).
"""
from __future__ import annotations

from src.capabilities.registry import tool


@tool(
    "get_student_info",
    descricao="Mostra os dados cadastrais da pessoa (leitura).",
    permissoes=("pessoa:read",),
    confirmacao=False,
)
async def get_info(user_id: str) -> dict:
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.pessoa_repository import PessoaRepository

    async with AsyncSessionLocal() as session:
        aluno = await PessoaRepository(session).get_by_id(int(user_id))
    if not aluno:
        raise ValueError("Pessoa não encontrada.")

    return {
        "mensagem": (
            f"📋 *Seus dados cadastrais:*\n\n"
            f"👤 Nome: {aluno.nome}\n"
            f"📧 E-mail: {aluno.email}\n"
            f"📚 Curso: {aluno.curso or 'Não informado'}\n"
            f"🎓 Matrícula: {aluno.matricula or 'Não informada'}\n"
            f"📍 Centro: {aluno.centro.value if aluno.centro else 'Não informado'}"
        )
    }
