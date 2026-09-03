"""Testes de src/graph/mcp_server_registry.py — validação SSRF chamada
ANTES de qualquer escrita (mockado: sessão fake, sem DB real)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.exc import IntegrityError

from src.graph_studio import mcp_server_registry
from src.infrastructure.security.ssrf_validator import URLInseguraError


class TestRegistrar:
    """Testes de mcp_server_registry.registrar."""

    @pytest.mark.asyncio
    async def test_url_insegura_rejeitada_antes_de_tocar_sessao(self):
        """SSRF check acontece ANTES de qualquer session.add/flush —
        session nem deveria ser tocada se a URL for rejeitada."""
        session = AsyncMock()

        with patch(
            "src.graph_studio.mcp_server_registry.validar_url_publica",
            side_effect=URLInseguraError("IP privado")
        ):
            with pytest.raises(URLInseguraError):
                await mcp_server_registry.registrar(session, "x", "http://127.0.0.1/mcp")

        session.add.assert_not_called()
        session.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_registro_bem_sucedido(self):
        session = AsyncMock()
        session.add = MagicMock()  # session.add() é síncrono na API real do SQLAlchemy

        with patch("src.graph_studio.mcp_server_registry.validar_url_publica", return_value=None):
            resultado = await mcp_server_registry.registrar(
                session, "stackexchange", "https://gateway.example/stackexchange/mcp",
                "Busca no StackExchange", admin="tester",
            )

        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        assert resultado["name"] == "stackexchange"
        assert resultado["url"] == "https://gateway.example/stackexchange/mcp"

    @pytest.mark.asyncio
    async def test_nome_duplicado_levanta_erro_especifico(self):
        session = AsyncMock()
        session.add = MagicMock()
        session.flush.side_effect = IntegrityError("stmt", {}, Exception("dup"))

        with patch("src.graph_studio.mcp_server_registry.validar_url_publica", return_value=None):
            with pytest.raises(mcp_server_registry.NomeDuplicadoError, match="stackexchange"):
                await mcp_server_registry.registrar(
                    session, "stackexchange", "https://gateway.example/mcp"
                )

        session.rollback.assert_awaited_once()


class TestSetHabilitado:
    """Testes de mcp_server_registry.set_habilitado."""

    @pytest.mark.asyncio
    async def test_toggle_existente_retorna_true(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.rowcount = 1
        session.execute.return_value = result_mock

        ok = await mcp_server_registry.set_habilitado(session, "x", False, admin="a")
        assert ok is True
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_toggle_inexistente_retorna_false(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.rowcount = 0
        session.execute.return_value = result_mock

        ok = await mcp_server_registry.set_habilitado(session, "nao_existe", True)
        assert ok is False


class TestRemover:
    """Testes de mcp_server_registry.remover."""

    @pytest.mark.asyncio
    async def test_remover_existente_retorna_true(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.rowcount = 1
        session.execute.return_value = result_mock

        ok = await mcp_server_registry.remover(session, "x")
        assert ok is True

    @pytest.mark.asyncio
    async def test_remover_inexistente_retorna_false(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.rowcount = 0
        session.execute.return_value = result_mock

        ok = await mcp_server_registry.remover(session, "nao_existe")
        assert ok is False


class TestListar:
    """Testes de mcp_server_registry.listar."""

    @pytest.mark.asyncio
    async def test_listar_degrada_para_lista_vazia_em_falha(self):
        session = AsyncMock()
        session.execute.side_effect = Exception("postgres fora")

        resultado = await mcp_server_registry.listar(session)
        assert resultado == []
