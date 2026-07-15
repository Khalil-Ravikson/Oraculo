# tests/unit/capabilities/persistence/test_registration_repository.py
"""
Testes de src/capabilities/persistence/registration_repository.py (Sprint 3,
Fase 0) — cobre os dois bugs corrigidos: email NOT NULL faltando no INSERT
(quebrava cadastro de número novo) e ON CONFLICT não promovendo status/role
de quem já estava pré-cadastrado (lista de inscrição).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.capabilities.persistence.registration_repository import salvar_pessoa


@pytest.mark.asyncio
async def test_salvar_pessoa_inclui_email_sintetico_para_satisfazer_not_null(monkeypatch):
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    class _SessionCtx:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "src.infrastructure.database.session.AsyncSessionLocal",
        lambda: _SessionCtx(),
    )

    await salvar_pessoa("5599999999", "Joao Da Silva", "Engenharia")

    session.execute.assert_awaited_once()
    args = session.execute.await_args.args
    params = args[1]
    assert params["email"] == "5599999999@whatsapp.oraculo.local"
    assert params["tel"] == "5599999999"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_salvar_pessoa_sql_promove_status_preservando_role_pre_atribuido(monkeypatch):
    """O UPDATE do ON CONFLICT deve sempre setar status='ativo' e só
    rebaixar role para 'estudante' quando o valor pré-existente for o
    default 'publico' — nunca sobrescrever role de um pré-cadastro
    (servidor/professor/admin) nem o e-mail real já cadastrado."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    class _SessionCtx:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "src.infrastructure.database.session.AsyncSessionLocal",
        lambda: _SessionCtx(),
    )

    await salvar_pessoa("5599999999", "Joao Da Silva", "Engenharia")

    sql_text = str(session.execute.await_args.args[0])
    assert "status = 'ativo'" in sql_text
    assert "role" in sql_text and "publico" in sql_text
    assert "email" not in sql_text.split("SET")[1]  # email não é sobrescrito no UPDATE
