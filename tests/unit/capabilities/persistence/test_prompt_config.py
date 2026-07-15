# tests/unit/capabilities/persistence/test_prompt_config.py
"""
Testes de src/capabilities/persistence/prompt_config.py (Sprint 2, Fase 8) —
módulo central de leitura/escrita de prompts versionados, com AsyncSession
mockada (mesma convenção do resto da suíte unit).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.capabilities.persistence.prompt_config import (
    historico,
    obter_prompt_ativo,
    publicar_novo_prompt,
    resetar_para_padrao,
)


def _session_com_scalar(valor):
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = valor
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_obter_prompt_ativo_usa_postgres_quando_existe():
    row = MagicMock(prompt_text="prompt do banco")
    session = _session_com_scalar(row)

    prompt = await obter_prompt_ativo(session, "academic_knowledge", fallback="hardcoded")

    assert prompt == "prompt do banco"


@pytest.mark.asyncio
async def test_obter_prompt_ativo_cai_para_redis_legado_se_postgres_vazio():
    session = _session_com_scalar(None)
    redis = MagicMock()
    redis.get.return_value = "prompt legado do redis"

    prompt = await obter_prompt_ativo(session, "academic_knowledge", fallback="hardcoded", redis=redis)

    assert prompt == "prompt legado do redis"


@pytest.mark.asyncio
async def test_obter_prompt_ativo_cai_para_hardcoded_se_nada_disponivel():
    session = _session_com_scalar(None)
    redis = MagicMock()
    redis.get.return_value = None

    prompt = await obter_prompt_ativo(session, "academic_knowledge", fallback="hardcoded", redis=redis)

    assert prompt == "hardcoded"


@pytest.mark.asyncio
async def test_obter_prompt_ativo_cai_para_hardcoded_se_postgres_falhar_e_sem_redis():
    session = MagicMock()
    session.execute = AsyncMock(side_effect=ConnectionError("postgres fora do ar"))

    prompt = await obter_prompt_ativo(session, "academic_knowledge", fallback="hardcoded")

    assert prompt == "hardcoded"


@pytest.mark.asyncio
async def test_publicar_novo_prompt_incrementa_versao_e_desativa_anterior():
    session = MagicMock()
    result_versao = MagicMock()
    result_versao.scalar_one_or_none.return_value = 2  # última versão existente
    session.execute = AsyncMock(return_value=result_versao)
    session.flush = AsyncMock()
    session.add = MagicMock()

    nova = await publicar_novo_prompt(session, "academic_knowledge", "novo texto", created_by="fulano")

    assert nova.version == 3
    assert nova.active is True
    assert nova.created_by == "fulano"
    assert session.execute.await_count == 2  # select versão + update desativa anterior
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_publicar_primeiro_prompt_comeca_na_versao_1():
    session = MagicMock()
    result_versao = MagicMock()
    result_versao.scalar_one_or_none.return_value = None  # nenhuma versão ainda
    session.execute = AsyncMock(return_value=result_versao)
    session.flush = AsyncMock()
    session.add = MagicMock()

    nova = await publicar_novo_prompt(session, "sigaa", "primeiro prompt")

    assert nova.version == 1


@pytest.mark.asyncio
async def test_resetar_para_padrao_desativa_e_faz_flush():
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()

    await resetar_para_padrao(session, "academic_knowledge", created_by="fulano")

    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_historico_converte_rows_em_dicts():
    row = MagicMock(version=2, prompt_text="v2", active=True, created_at="2026-01-01", created_by="fulano")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    linhas = await historico(session, "academic_knowledge")

    assert linhas == [{
        "version": 2, "prompt_text": "v2", "active": True,
        "created_at": "2026-01-01", "created_by": "fulano",
    }]
