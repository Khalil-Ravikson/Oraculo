"""
src/capabilities/persistence/registration_repository.py
===========================================================
Ex SQL cru embutido em `application/routing/registration_funnel.py` (Fase 6
do PLANO_REFATORACAO_SUPERVISOR.md, seção 2.6). Função async pura, sem
decisão de negócio — só a gravação em `pessoas`.
"""
from __future__ import annotations


async def salvar_pessoa(telefone: str, nome: str, curso: str) -> None:
    from src.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO pessoas (telefone, nome, curso, role, status)
                VALUES (:tel, :nome, :curso, 'estudante', 'ativo')
                ON CONFLICT (telefone) DO UPDATE
                SET nome=EXCLUDED.nome, curso=EXCLUDED.curso
            """),
            {"tel": telefone, "nome": nome, "curso": curso},
        )
        await db.commit()
