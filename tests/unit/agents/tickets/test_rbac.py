# tests/unit/agents/tickets/test_rbac.py
"""
Cobertura de agents/tickets/rbac.py::checar_permissao_chamado() — item
pendente do ADR 0001 ("RBAC completo ainda não está testado corretamente em
main"), fechado como parte da Fase 2 do plano de integração. Complementa
tests/unit/domain/test_permissions.py (a lógica pura de permissão) cobrindo
a camada que busca a pessoa no banco e decide autorizar/bloquear o funil de
ticket/CRUD — chamada tanto pelo dispatcher.py legado (ticket_flow.py/
crud_tool.py) quanto pelos nodes nativos do LangGraph (langgraph_experiment/
nodes.py, Fase 2).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.tickets.rbac import checar_permissao_chamado


def _pessoa(**overrides) -> dict:
    base = {
        "nome": "Fulano de Tal", "email": "fulano@aluno.uema.br", "telefone": "5598999999999",
        "matricula": "2024001", "centro": "CECEN", "curso": "Engenharia Civil",
        "role": "estudante", "status": "ativo", "pode_abrir_chamado": True,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_pessoa_ativa_com_permissao_e_autorizada():
    with patch(
        "src.capabilities.persistence.pessoa_lookup.buscar_pessoa_por_telefone",
        new_callable=AsyncMock, return_value=_pessoa(),
    ):
        autorizado, msg_bloqueio, pessoa = await checar_permissao_chamado("5598999999999")

    assert autorizado is True
    assert msg_bloqueio == ""
    assert pessoa["nome"] == "Fulano de Tal"


@pytest.mark.asyncio
async def test_pessoa_com_status_inativo_e_bloqueada():
    with patch(
        "src.capabilities.persistence.pessoa_lookup.buscar_pessoa_por_telefone",
        new_callable=AsyncMock, return_value=_pessoa(status="inativo"),
    ):
        autorizado, msg_bloqueio, pessoa = await checar_permissao_chamado("5598999999999")

    assert autorizado is False
    assert "inativo" in msg_bloqueio.lower()
    assert pessoa is not None  # pessoa é devolvida mesmo bloqueada


@pytest.mark.asyncio
async def test_pessoa_com_status_pendente_e_bloqueada():
    with patch(
        "src.capabilities.persistence.pessoa_lookup.buscar_pessoa_por_telefone",
        new_callable=AsyncMock, return_value=_pessoa(status="pendente"),
    ):
        autorizado, msg_bloqueio, _ = await checar_permissao_chamado("5598999999999")

    assert autorizado is False
    assert "cadastro" in msg_bloqueio.lower()


@pytest.mark.asyncio
async def test_pessoa_role_publico_nao_pode_abrir_chamado():
    """role=publico não tem Recurso.CHAMADO_GLPI na matriz de permissões
    (domain/permissions.py) — bloqueado mesmo com status ativo."""
    with patch(
        "src.capabilities.persistence.pessoa_lookup.buscar_pessoa_por_telefone",
        new_callable=AsyncMock, return_value=_pessoa(role="publico"),
    ):
        autorizado, msg_bloqueio, _ = await checar_permissao_chamado("5598999999999")

    assert autorizado is False
    assert msg_bloqueio != ""


@pytest.mark.asyncio
async def test_pessoa_com_pode_abrir_chamado_false_e_bloqueada_mesmo_com_rbac_ok():
    """Segunda camada de bloqueio, independente do RBAC de domain/
    permissions.py: flag administrativa por pessoa (`pode_abrir_chamado`)."""
    with patch(
        "src.capabilities.persistence.pessoa_lookup.buscar_pessoa_por_telefone",
        new_callable=AsyncMock, return_value=_pessoa(pode_abrir_chamado=False),
    ):
        autorizado, msg_bloqueio, _ = await checar_permissao_chamado("5598999999999")

    assert autorizado is False
    assert "administração" in msg_bloqueio.lower() or "bloqueada" in msg_bloqueio.lower()


@pytest.mark.asyncio
async def test_pessoa_nao_encontrada_sem_skip_registration_e_bloqueada(monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "DEV_TEST_SKIP_REGISTRATION", False)
    with patch(
        "src.capabilities.persistence.pessoa_lookup.buscar_pessoa_por_telefone",
        new_callable=AsyncMock, return_value=None,
    ):
        autorizado, msg_bloqueio, pessoa = await checar_permissao_chamado("5598000000000")

    assert autorizado is False
    assert "cadastro" in msg_bloqueio.lower()
    assert pessoa is None


@pytest.mark.asyncio
async def test_pessoa_nao_encontrada_com_skip_registration_sintetiza_usuario_permissivo(monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "DEV_TEST_SKIP_REGISTRATION", True)
    with patch(
        "src.capabilities.persistence.pessoa_lookup.buscar_pessoa_por_telefone",
        new_callable=AsyncMock, return_value=None,
    ):
        autorizado, msg_bloqueio, pessoa = await checar_permissao_chamado("5598000000000")

    assert autorizado is True
    assert msg_bloqueio == ""
    assert pessoa["role"] == "estudante"
    assert pessoa["status"] == "ativo"
    assert pessoa["pode_abrir_chamado"] is True
    assert pessoa["telefone"] == "5598000000000"
