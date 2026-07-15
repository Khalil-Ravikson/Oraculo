# tests/unit/application/test_admin_commands_prompt.py
"""
Confirma o cutover dos comandos WhatsApp `!prompt ...`/`!prompt reset`
(Sprint 2, Fase 8) — antes escreviam direto na chave Redis crua
`admin:system_prompt`, agora chamam `prompt_config.publicar_novo_prompt` /
`resetar_para_padrao` (catálogo Postgres versionado).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.application.use_cases.admin_commands import AdminCommandsUseCase


def _patch_async_session(monkeypatch):
    session = MagicMock()
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
    return session


@pytest.mark.asyncio
async def test_prompt_set_chama_publicar_novo_prompt(monkeypatch):
    session = _patch_async_session(monkeypatch)
    publicar_mock = AsyncMock(return_value=MagicMock(version=1))
    monkeypatch.setattr(
        "src.capabilities.persistence.prompt_config.publicar_novo_prompt", publicar_mock,
    )

    use_case = AdminCommandsUseCase()
    resposta = await use_case._prompt_set(
        type("M", (), {"group": lambda self, i: "prompt novo com mais de vinte caracteres"})(),
        "admin1",
    )

    assert "atualizado" in resposta.lower()
    publicar_mock.assert_awaited_once()
    args = publicar_mock.await_args.args
    assert args[1] == "academic_knowledge"
    assert args[2] == "prompt novo com mais de vinte caracteres"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_prompt_set_rejeita_prompt_curto(monkeypatch):
    publicar_mock = AsyncMock()
    monkeypatch.setattr(
        "src.capabilities.persistence.prompt_config.publicar_novo_prompt", publicar_mock,
    )

    use_case = AdminCommandsUseCase()
    resposta = await use_case._prompt_set(
        type("M", (), {"group": lambda self, i: "curto"})(), "admin1",
    )

    assert "muito curto" in resposta.lower()
    publicar_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_reset_chama_resetar_para_padrao(monkeypatch):
    session = _patch_async_session(monkeypatch)
    resetar_mock = AsyncMock()
    monkeypatch.setattr(
        "src.capabilities.persistence.prompt_config.resetar_para_padrao", resetar_mock,
    )

    use_case = AdminCommandsUseCase()
    resposta = await use_case._prompt_reset(None, "admin1")

    assert "restaurado" in resposta.lower()
    resetar_mock.assert_awaited_once_with(session, "academic_knowledge", created_by="admin1")
    session.commit.assert_awaited_once()
