"""
src/capabilities/persistence/pessoa_lookup.py
================================================
Leitura pura em `pessoas` para pré-preencher o checklist do funil de tickets
e calcular RBAC (Recurso.CHAMADO_GLPI + pessoa.pode_abrir_chamado). Só SELECT
— não é afetado pela flag `DEV_TEST_NO_DB_WRITE` (essa flag gateia só
escrita, ver dev_dump.py).
"""
from __future__ import annotations


async def buscar_pessoa_por_telefone(telefone: str) -> dict | None:
    from src.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            text("""
                SELECT nome, email, telefone, matricula, centro, curso,
                       role, status, pode_abrir_chamado
                FROM pessoas
                WHERE telefone = :tel
            """),
            {"tel": telefone},
        )
        row = r.fetchone()
        return dict(row._mapping) if row else None
