"""
src/capabilities/persistence/admin_repository.py
====================================================
Ex `domain/tools/admin_tools.py` (Fase 6 do PLANO_REFATORACAO_SUPERVISOR.md,
seção 2.5). Duas mudanças em relação ao original:

  1. `asyncio.run()` dentro de contexto potencialmente async removido — eram
     funções `@tool` do LangChain (síncronas por exigência da API antiga),
     que embrulhavam uma query async com `asyncio.run(_query())`. Como não
     existe mais nenhum caminho vivo de tool-calling via LangChain no
     projeto (`oracle_chain.py`, que usava isso, é hoje `oracle_chain.bak`),
     essas viraram funções async nativas — quem quiser expor via
     tool-calling futuramente embrulha isso, não o contrário.
  2. Sem consumidor vivo hoje (só `oracle_chain.bak` importava). Migrado
     porque a lógica é válida e reaproveitável para um futuro
     `agents/conversation/` ou `agents/administration/` que precise buscar/
     cadastrar usuário administrativamente.
"""
from __future__ import annotations

import re


async def buscar_usuario(telefone: str) -> dict:
    """Busca usuário pelo número de telefone. Apenas admins."""
    from sqlalchemy import select
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.database.models import Pessoa

    telefone = re.sub(r"\D", "", telefone)

    async with AsyncSessionLocal() as s:
        r = await s.execute(select(Pessoa).where(Pessoa.telefone == telefone))
        p = r.scalar_one_or_none()
        if not p:
            return {"status": "nao_encontrado", "telefone": telefone}
        return {
            "status": "encontrado",
            "id": p.id,
            "nome": p.nome,
            "email": p.email,
            "role": p.role.value,
            "status_usuario": p.status.value,
            "curso": p.curso,
            "matricula": p.matricula,
        }


async def cadastrar_usuario(nome: str, telefone: str, curso: str = "") -> dict:
    """Cadastra novo usuário institucional. Apenas admins."""
    from sqlalchemy import select
    from src.domain.entities.enums import RoleEnum, StatusMatriculaEnum
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.database.models import Pessoa

    telefone = re.sub(r"\D", "", telefone)
    if len(telefone) < 10:
        return {"status": "erro", "message": "Telefone inválido."}

    async with AsyncSessionLocal() as s:
        existe = await s.execute(select(Pessoa).where(Pessoa.telefone == telefone))
        if existe.scalar_one_or_none():
            return {"status": "erro", "message": "Telefone já cadastrado."}

        p = Pessoa(
            nome=nome.strip(),
            email=f"{telefone}@uema.br",  # placeholder
            telefone=telefone,
            curso=curso or None,
            role=RoleEnum.estudante,
            status=StatusMatriculaEnum.pendente,
        )
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return {"status": "cadastrado", "id": p.id, "nome": p.nome}
