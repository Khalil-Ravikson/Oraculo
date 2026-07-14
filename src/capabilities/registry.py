"""
src/capabilities/registry.py
===============================
Ex `domain/tools/crud_tools.py` + `domain/tools/tool_registry.py` (este
último já deletado na Fase 1 — era `StructuredTool` factory morta; o
mecanismo escolhido aqui é o decorator+dict de `crud_tools.py`, mais simples
e com um padrão real de uso). Fase 6 do PLANO_REFATORACAO_SUPERVISOR.md,
seção 2.5.

ACHADO desta fase: nenhum destes tools tinha consumidor vivo — o único
import de `crud_tools.executar_tool` era em
`application/chain/oracle_chain.bak` (arquivo `.bak`, nunca executado). A
rota "CRUD" do Supervisor aponta hoje para um worker "crud_confirm" que não
existe (ver `agents/tickets/service.py`). Migrado mesmo assim porque a
implementação é válida e reaproveitável — só não está conectada a nenhum
fluxo de produção no momento. Conectar isso é trabalho de produto (decidir
COMO o CRUD confirma e dispara), não desta fase estrutural.

Tools CRUD — pensadas para executar APENAS após confirmação do usuário
(HITL). Cada tool valida que user_id está operando sobre seus próprios dados.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

_TOOL_REGISTRY: dict[str, callable] = {}


def tool(name: str):
    """Decorator para registrar capabilities por nome."""
    def decorator(fn):
        _TOOL_REGISTRY[name] = fn
        return fn
    return decorator


async def executar_tool(tool_name: str, args: dict) -> dict:
    """Dispatcher central de capabilities registradas."""
    fn = _TOOL_REGISTRY.get(tool_name)
    if not fn:
        raise ValueError(f"Tool '{tool_name}' não encontrada.")
    return await fn(**args)


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


@tool("update_student_telefone")
async def update_telefone(user_id: str, novo_valor: str) -> dict:
    """Atualiza telefone do aluno — mantém consistência com o WhatsApp."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.postgres_user_repository import PostgresUserRepository

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
