# tests/unit/infrastructure/test_agent_catalog_repository.py
"""
Testa AgentCatalogRepository (Sprint 2, Fase 4) com uma AsyncSession mockada
— mesma convenção do resto da suíte unit (sem banco real).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.infrastructure.repositories.agent_catalog_repository import AgentCatalogRepository


def _make_session():
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_upsert_from_code_executa_insert_on_conflict():
    session = _make_session()
    repo = AgentCatalogRepository(session)

    await repo.upsert_from_code("sigaa", "Consulta SIGAA", ["aluno"])

    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_listar_converte_rows_em_dicts():
    row = MagicMock(
        nome="sigaa", descricao="desc", permissions=["aluno"], ativo=True,
        criado_em="2026-01-01", atualizado_em="2026-01-02", atualizado_por="admin",
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = _make_session()
    session.execute.return_value = result

    repo = AgentCatalogRepository(session)
    linhas = await repo.listar()

    assert linhas == [{
        "nome": "sigaa", "descricao": "desc", "permissions": ["aluno"], "ativo": True,
        "criado_em": "2026-01-01", "atualizado_em": "2026-01-02", "atualizado_por": "admin",
    }]


@pytest.mark.asyncio
async def test_set_ativo_e_atualizar_descricao_fazem_flush():
    session = _make_session()
    repo = AgentCatalogRepository(session)

    await repo.set_ativo("sigaa", False, admin="fulano")
    await repo.atualizar_descricao("sigaa", "nova descrição", admin="fulano")

    assert session.execute.await_count == 2
    assert session.flush.await_count == 2
