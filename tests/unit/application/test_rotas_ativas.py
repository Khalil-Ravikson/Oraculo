"""Kill-switch por rota (item B3).

Existe porque o kill-switch de `/hub/agents` NÃO resolve o problema do v1:
ele desliga por agente, e `WIKI`, `CONTATOS`, `CALENDARIO`, `EDITAL` e
`GERAL` compartilham o mesmo agente (`academic_knowledge`). Desligar o
calendário por lá derrubaria a wiki junto — e `GREETING`,
`MEDIA_DOWNLOAD` e `CHECK_STATUS` sequer têm agente, então o breaker nunca
os alcançou.
"""
from __future__ import annotations

import pytest

from src.application.orchestration.nodes import _rota_ativa
from src.infrastructure.settings import settings


# O valor de produção do v1. Vive no `.env.example`, não no default do
# código — ver o comentário de `settings.ROTAS_ATIVAS`.
ROTAS_V1 = "WIKI,CONTATOS,GREETING,ESCALAR_HUMANO"


@pytest.fixture
def escopo_v1(monkeypatch):
    monkeypatch.setattr(settings, "ROTAS_ATIVAS", ROTAS_V1)


@pytest.mark.parametrize("rota", ["WIKI", "CONTATOS", "GREETING", "ESCALAR_HUMANO"])
def test_rotas_do_v1_estao_ligadas(escopo_v1, rota: str) -> None:
    assert _rota_ativa(rota)


@pytest.mark.parametrize("rota", [
    "CALENDARIO", "EDITAL", "SIGAA", "TICKET_ABERTURA",
    "CRUD", "MEDIA_DOWNLOAD", "CHECK_STATUS", "GERAL",
])
def test_rotas_fora_do_v1_estao_desligadas(escopo_v1, rota: str) -> None:
    assert not _rota_ativa(rota)


def test_default_do_codigo_nao_restringe() -> None:
    """Sem configuração, nada é bloqueado — é o que mantém a suíte rodando
    na mesma configuração que o código tinha antes do v1."""
    assert settings.ROTAS_ATIVAS == ""
    assert _rota_ativa("TICKET_ABERTURA")


def test_comparacao_ignora_caixa(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ROTAS_ATIVAS", "wiki, Contatos ")
    assert _rota_ativa("WIKI")
    assert _rota_ativa("CONTATOS")


def test_lista_vazia_libera_tudo(monkeypatch) -> None:
    """O comportamento anterior ao v1 continua alcançável sem editar código."""
    monkeypatch.setattr(settings, "ROTAS_ATIVAS", "")
    assert _rota_ativa("CALENDARIO")
    assert _rota_ativa("SIGAA")


def test_wiki_e_calendario_dividem_o_agente(monkeypatch) -> None:
    """A razão de existir deste kill-switch, fixada como teste: se um dia
    alguém separar os agentes, este teste falha e o comentário do
    `settings.ROTAS_ATIVAS` precisa ser revisto."""
    from src.infrastructure import route_registry

    assert route_registry._DEFAULTS["WIKI"].agente == "academic_knowledge"
    assert route_registry._DEFAULTS["CALENDARIO"].agente == "academic_knowledge"
