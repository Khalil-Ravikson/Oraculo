"""Testes de src/graph/topology_registry.py — validação chamada ANTES de
qualquer escrita (mockado: sessão fake, sem DB real)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.graph_studio import topology_registry


class TestSalvar:
    """Testes de topology_registry.salvar."""

    @pytest.mark.asyncio
    async def test_topologia_invalida_rejeitada_antes_de_tocar_sessao(self):
        session = AsyncMock()

        with patch(
            "src.graph_studio.topology_registry.validar_topologia",
            return_value=["Nó 'x' não existe no NodeRegistry."]
        ):
            with pytest.raises(topology_registry.TopologiaInvalidaError) as exc_info:
                await topology_registry.salvar(session, "minha-topo", {"nodes": [], "edges": []})

        assert "não existe" in str(exc_info.value)
        assert exc_info.value.erros == ["Nó 'x' não existe no NodeRegistry."]
        session.execute.assert_not_called()
        session.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_salvar_valido_executa_upsert(self):
        session = AsyncMock()

        linha_fake = MagicMock()
        linha_fake.name = "minha-topo"
        linha_fake.description = ""
        linha_fake.topology_json = {"nodes": [], "edges": []}
        linha_fake.status = "draft"
        linha_fake.versao = 1
        linha_fake.atualizado_em = None
        linha_fake.atualizado_por = "tester"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = linha_fake
        session.execute.return_value = result_mock

        with patch("src.graph_studio.topology_registry.validar_topologia", return_value=[]):
            resultado = await topology_registry.salvar(
                session, "minha-topo", {"nodes": [{"node_id": "a"}], "edges": []},
                admin="tester",
            )

        session.flush.assert_awaited_once()
        assert resultado["name"] == "minha-topo"
        assert resultado["atualizado_por"] == "tester"


class TestObter:
    """Testes de topology_registry.obter."""

    @pytest.mark.asyncio
    async def test_obter_inexistente_retorna_none(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        resultado = await topology_registry.obter(session, "nao-existe")
        assert resultado is None


class TestRemover:
    """Testes de topology_registry.remover."""

    @pytest.mark.asyncio
    async def test_remover_existente_retorna_true(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.rowcount = 1
        session.execute.return_value = result_mock

        assert await topology_registry.remover(session, "x") is True

    @pytest.mark.asyncio
    async def test_remover_inexistente_retorna_false(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.rowcount = 0
        session.execute.return_value = result_mock

        assert await topology_registry.remover(session, "nao-existe") is False


class TestListar:
    """Testes de topology_registry.listar."""

    @pytest.mark.asyncio
    async def test_listar_degrada_para_lista_vazia_em_falha(self):
        session = AsyncMock()
        session.execute.side_effect = Exception("postgres fora")

        assert await topology_registry.listar(session) == []
