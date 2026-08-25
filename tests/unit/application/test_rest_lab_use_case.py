# tests/unit/application/test_rest_lab_use_case.py
"""
Fase 3 do plano de integração (Decisão 03): RestLabUseCase é a nova camada
de Application entre rest_lab/router.py e rest_lab/clients.py (httpx). Todo
teste aqui mocka rest_lab.clients.get_client — rest_lab historicamente nunca
teve testes automatizados e chamava APIs públicas reais (JSONPlaceholder/
DummyJSON/httpbin) sem mock nenhum; isolar da rede real agora que existe uma
camada testável é parte do fechamento desta fase.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.application.use_cases.rest_lab_use_case import RestLabUseCase


def _fake_response(json_data, status_code=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "erro", request=MagicMock(), response=resp,
        )
    return resp


def _fake_client(**method_returns) -> MagicMock:
    client = MagicMock()
    for metodo, retorno in method_returns.items():
        setattr(client, metodo, AsyncMock(return_value=retorno))
    return client


@pytest.fixture
def uc():
    return RestLabUseCase()


# ─────────────────────────────────────────────────────────────────────────────
# listar_usuarios / obter_usuario
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_listar_usuarios_formata_ate_o_limite(uc):
    usuarios = [
        {"id": i, "name": f"User {i}", "email": f"u{i}@x.com", "address": {"city": "São Luís"}}
        for i in range(1, 15)
    ]
    client = _fake_client(get=_fake_response(usuarios))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.listar_usuarios()

    assert "Usuários" in result["mensagem"]
    assert result["mensagem"].count("São Luís") == 10  # _LIMITE_LISTA


@pytest.mark.asyncio
async def test_obter_usuario_sucesso(uc):
    u = {
        "name": "Fulano", "username": "fulano", "email": "fulano@x.com",
        "phone": "123", "company": {"name": "ACME"}, "address": {"city": "São Luís"},
    }
    client = _fake_client(get=_fake_response(u))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.obter_usuario(1)

    assert "Fulano" in result["mensagem"]
    assert "ACME" in result["mensagem"]


@pytest.mark.asyncio
async def test_obter_usuario_404_mensagem_amigavel(uc):
    client = _fake_client(get=_fake_response({}, status_code=404))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.obter_usuario(999)

    assert "não encontrado" in result["mensagem"].lower()


@pytest.mark.asyncio
async def test_obter_usuario_erro_de_rede_nao_propaga_excecao(uc):
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.obter_usuario(1)

    assert "erro" in result["mensagem"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# criar_post / atualizar_post / deletar_post
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_criar_post_sucesso(uc):
    client = _fake_client(post=_fake_response({"id": 101, "title": "Novo post"}))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.criar_post("Novo post", "corpo")

    assert "101" in result["mensagem"]
    assert "Novo post" in result["mensagem"]


@pytest.mark.asyncio
async def test_atualizar_post_404(uc):
    client = _fake_client(put=_fake_response({}, status_code=404))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.atualizar_post(999, "titulo novo")

    assert "não encontrado" in result["mensagem"].lower()


@pytest.mark.asyncio
async def test_deletar_post_sucesso(uc):
    client = _fake_client(delete=_fake_response({}))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.deletar_post(5)

    assert "deletado" in result["mensagem"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# listar_produtos / buscar_produto
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_listar_produtos_mostra_total(uc):
    payload = {"products": [{"id": 1, "title": "Caneta", "price": 2.5}], "total": 42}
    client = _fake_client(get=_fake_response(payload))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.listar_produtos()

    assert "42" in result["mensagem"]
    assert "Caneta" in result["mensagem"]


@pytest.mark.asyncio
async def test_buscar_produto_sem_resultado(uc):
    client = _fake_client(get=_fake_response({"products": []}))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.buscar_produto("inexistente")

    assert "nenhum produto" in result["mensagem"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# testar_status / echo_request
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_testar_status_nao_levanta_pra_status_nao_2xx(uc):
    """Comportamento deliberado (ver docstring do método): observa o status
    sem virar exceção, mesmo pra 500."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 500
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.testar_status(500)

    assert "500" in result["mensagem"]


@pytest.mark.asyncio
async def test_echo_request_mostra_url_e_args(uc):
    payload = {"url": "https://httpbin.org/get", "args": {"origem": "oraculo-rest-lab"}, "headers": {"User-Agent": "x"}}
    client = _fake_client(get=_fake_response(payload))

    with patch("src.application.use_cases.rest_lab_use_case.get_client", return_value=client):
        result = await uc.echo_request()

    assert "httpbin.org/get" in result["mensagem"]


# ─────────────────────────────────────────────────────────────────────────────
# rest_lab/tools.py — facade fino, delega pra RestLabUseCase
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tools_listar_usuarios_delega_pro_use_case():
    from rest_lab import tools

    with patch.object(
        tools._use_case, "listar_usuarios", new_callable=AsyncMock,
        return_value={"mensagem": "ok"},
    ) as mock_metodo:
        result = await tools.listar_usuarios()

    assert result == {"mensagem": "ok"}
    mock_metodo.assert_awaited_once()


@pytest.mark.asyncio
async def test_tools_obter_usuario_repassa_argumento():
    from rest_lab import tools

    with patch.object(
        tools._use_case, "obter_usuario", new_callable=AsyncMock,
        return_value={"mensagem": "ok"},
    ) as mock_metodo:
        await tools.obter_usuario(42)

    mock_metodo.assert_awaited_once_with(42)
