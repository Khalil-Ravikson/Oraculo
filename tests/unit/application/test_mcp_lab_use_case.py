# tests/unit/application/test_mcp_lab_use_case.py
"""
Fase 4 do plano de integração (Decisão 03): McpLabUseCase é a nova camada
de Application entre mcp_lab/router.py e mcp_lab/clients.py (SDK `mcp`)/
httpx/EvolutionAdapter. Todo teste aqui mocka as sessões MCP e o adapter de
mídia — mcp_lab nunca teve testes automatizados antes desta fase.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.use_cases.mcp_lab_use_case import McpLabUseCase


def _fake_call_tool_result(payload, is_error=False):
    bloco = MagicMock()
    bloco.text = __import__("json").dumps(payload)
    resultado = MagicMock()
    resultado.content = [bloco]
    resultado.is_error = is_error
    return resultado


def _fake_session(call_tool_return=None, call_tool_side_effect=None):
    session = MagicMock()
    if call_tool_side_effect is not None:
        session.call_tool = AsyncMock(side_effect=call_tool_side_effect)
    else:
        session.call_tool = AsyncMock(return_value=call_tool_return)

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm(), session


@pytest.fixture
def uc():
    return McpLabUseCase()


# ─────────────────────────────────────────────────────────────────────────────
# buscar_perguntas / obter_respostas (StackExchange)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_perguntas_formata_resultado(uc):
    payload = {"items": [
        {"question_id": 1, "title": "Como fazer X?", "score": 10, "answer_count": 2, "link": "https://x"},
    ]}
    cm, _ = _fake_session(call_tool_return=_fake_call_tool_result(payload))

    with patch(
        "src.application.use_cases.mcp_lab_use_case.stackexchange_session", return_value=cm,
    ):
        result = await uc.buscar_perguntas("como fazer X")

    assert "Como fazer X?" in result["mensagem"]


@pytest.mark.asyncio
async def test_buscar_perguntas_sem_resultado(uc):
    cm, _ = _fake_session(call_tool_return=_fake_call_tool_result({"items": []}))

    with patch(
        "src.application.use_cases.mcp_lab_use_case.stackexchange_session", return_value=cm,
    ):
        result = await uc.buscar_perguntas("pergunta inexistente")

    assert "nenhuma pergunta" in result["mensagem"].lower()


@pytest.mark.asyncio
async def test_buscar_perguntas_erro_de_rede_nao_propaga(uc):
    cm, _ = _fake_session(call_tool_side_effect=RuntimeError("gateway offline"))

    with patch(
        "src.application.use_cases.mcp_lab_use_case.stackexchange_session", return_value=cm,
    ):
        result = await uc.buscar_perguntas("qualquer coisa")

    assert "erro" in result["mensagem"].lower()


@pytest.mark.asyncio
async def test_obter_respostas_remove_html_do_corpo(uc):
    payload = {"items": [{"is_accepted": True, "score": 5, "body": "<p>Resposta <b>formatada</b></p>"}]}
    cm, _ = _fake_session(call_tool_return=_fake_call_tool_result(payload))

    with patch(
        "src.application.use_cases.mcp_lab_use_case.stackexchange_session", return_value=cm,
    ):
        result = await uc.obter_respostas(123)

    assert "<p>" not in result["mensagem"]
    assert "<b>" not in result["mensagem"]
    assert "Resposta" in result["mensagem"]


@pytest.mark.asyncio
async def test_obter_respostas_is_error_do_servidor_mcp(uc):
    cm, _ = _fake_session(call_tool_return=_fake_call_tool_result({"erro": "not found"}, is_error=True))

    with patch(
        "src.application.use_cases.mcp_lab_use_case.stackexchange_session", return_value=cm,
    ):
        result = await uc.obter_respostas(999)

    assert "erro" in result["mensagem"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# buscar_web (Brave via MCP)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_web_sem_api_key_nao_chama_sessao(uc, monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "BRAVE_API_KEY", "")
    session_factory = MagicMock()

    with patch(
        "src.application.use_cases.mcp_lab_use_case.brave_session", session_factory,
    ):
        result = await uc.buscar_web("uema")

    assert "BRAVE_API_KEY" in result["mensagem"]
    session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_buscar_web_formata_resultados(uc, monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "BRAVE_API_KEY", "chave-teste")
    payload = {"results": [{"title": "UEMA", "url": "https://uema.br", "description": "Universidade"}]}
    cm, _ = _fake_session(call_tool_return=_fake_call_tool_result(payload))

    with patch(
        "src.application.use_cases.mcp_lab_use_case.brave_session", return_value=cm,
    ):
        result = await uc.buscar_web("uema")

    assert "UEMA" in result["mensagem"]
    assert "uema.br" in result["mensagem"]


# ─────────────────────────────────────────────────────────────────────────────
# buscar_imagem (Brave REST direto + entrega via WhatsApp)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_imagem_sem_chat_id_nao_chama_rede(uc, monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "BRAVE_API_KEY", "chave-teste")

    result = await uc.buscar_imagem("gato", chat_id="")

    assert "chat_id" in result["mensagem"].lower() or "destino" in result["mensagem"].lower()


@pytest.mark.asyncio
async def test_buscar_imagem_sucesso_usa_capability_de_evolution_tool(uc, monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "BRAVE_API_KEY", "chave-teste")

    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = {
        "results": [{"title": "Gato", "properties": {"url": "https://x.com/gato.jpg"}}],
    }
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=fake_resp)
    fake_client_cm = MagicMock()
    fake_client_cm.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=fake_client_cm), \
         patch(
             "src.capabilities.messaging.evolution_tool.enviar_midia_por_url",
             new_callable=AsyncMock, return_value=True,
         ) as mock_enviar:
        result = await uc.buscar_imagem("gato", chat_id="5598@s.whatsapp.net")

    assert "enviada" in result["mensagem"].lower()
    mock_enviar.assert_awaited_once_with(
        "5598@s.whatsapp.net", "https://x.com/gato.jpg",
        mediatype="image", mimetype="image/jpeg", caption="Gato",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GitHub
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_repos_github_sem_api_key(uc, monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "GITHUB_API_KEY", "")

    result = await uc.buscar_repos_github("langgraph")

    assert "GITHUB_API_KEY" in result["mensagem"]


@pytest.mark.asyncio
async def test_perfil_github_usuario_nao_encontrado(uc, monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "GITHUB_API_KEY", "chave-teste")
    cm, _ = _fake_session(call_tool_return=_fake_call_tool_result([]))

    with patch(
        "src.application.use_cases.mcp_lab_use_case.github_session", return_value=cm,
    ):
        result = await uc.perfil_github("usuario-fantasma")

    assert "não encontrado" in result["mensagem"].lower()


@pytest.mark.asyncio
async def test_perfil_github_sucesso(uc, monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "GITHUB_API_KEY", "chave-teste")
    payload = {"name": "Fulano", "login": "fulano", "bio": "dev", "public_repos": 5, "followers": 10, "html_url": "https://github.com/fulano"}
    cm, _ = _fake_session(call_tool_return=_fake_call_tool_result(payload))

    with patch(
        "src.application.use_cases.mcp_lab_use_case.github_session", return_value=cm,
    ):
        result = await uc.perfil_github("fulano")

    assert "Fulano" in result["mensagem"]
    assert "5 repos" in result["mensagem"]


# ─────────────────────────────────────────────────────────────────────────────
# mcp_lab/tools.py — facade fino, delega pro use case
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tools_buscar_perguntas_delega_pro_use_case():
    from mcp_lab import tools

    with patch.object(
        tools._use_case, "buscar_perguntas", new_callable=AsyncMock,
        return_value={"mensagem": "ok"},
    ) as mock_metodo:
        result = await tools.buscar_perguntas("query", site="stackoverflow")

    assert result == {"mensagem": "ok"}
    mock_metodo.assert_awaited_once_with("query", "stackoverflow")


@pytest.mark.asyncio
async def test_tools_buscar_imagem_repassa_argumentos():
    from mcp_lab import tools

    with patch.object(
        tools._use_case, "buscar_imagem", new_callable=AsyncMock,
        return_value={"mensagem": "ok"},
    ) as mock_metodo:
        await tools.buscar_imagem("gato", "5598@s.whatsapp.net")

    mock_metodo.assert_awaited_once_with("gato", "5598@s.whatsapp.net")
