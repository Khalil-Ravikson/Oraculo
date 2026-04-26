from langchain_core.tools import tool
from src.infrastructure.database.session import AsyncSessionLocal
from src.infrastructure.database.models import Pessoa
from sqlalchemy import select
import json, asyncio

@tool
def buscar_usuario(telefone: str) -> str:
    """Busca usuário pelo número de telefone. Apenas admins."""
    import re
    telefone = re.sub(r"\D", "", telefone)
    
    async def _query():
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
    
    result = asyncio.run(_query())
    return json.dumps(result, ensure_ascii=False)


@tool
def cadastrar_usuario(nome: str, telefone: str, curso: str = "") -> str:
    """Cadastra novo usuário institucional. Apenas admins."""
    import re
    from src.domain.entities.enums import RoleEnum, StatusMatriculaEnum
    
    telefone = re.sub(r"\D", "", telefone)
    if len(telefone) < 10:
        return json.dumps({"status": "erro", "message": "Telefone inválido."})

    async def _insert():
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
    
    result = asyncio.run(_insert())
    return json.dumps(result, ensure_ascii=False)