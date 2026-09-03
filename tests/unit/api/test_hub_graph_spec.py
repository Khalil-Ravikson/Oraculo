# tests/unit/api/test_hub_graph_spec.py
"""
Endpoints da aba "Grafo de produção" do Graph Studio (ADR 0008 Fases 4/5):
autenticação, leitura da spec ativa, e a validação PRÉ-banco de criar um
fluxo novo (a parte que não precisa de Postgres).
"""
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.web.hub import router
from src.application.use_cases.admin_auth import TokenPayload


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def auth():
    payload = TokenPayload(sub="admin", iat=int(time.time()), exp=int(time.time()) + 3600)
    mock = MagicMock()
    mock.token_esta_bloqueado.return_value = False
    mock.verificar_token.return_value = payload
    return mock


@pytest.mark.parametrize("method,path,kwargs", [
    ("get", "/hub/graph-studio/spec", {}),
    ("get", "/hub/graph-studio/spec/historico", {}),
    ("post", "/hub/graph-studio/spec/nova-rota", {"json": {"rota": "X"}}),
    ("post", "/hub/graph-studio/spec/rota/remover", {"json": {"node_id": "x"}}),
])
def test_sem_cookie_retorna_401(client, method, path, kwargs):
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_get_spec_devolve_topologia_ativa_e_catalogo(client, auth):
    with patch("src.api.routers.web.hub.get_admin_auth", return_value=auth):
        client.cookies.set("admin_token", "t")
        r = client.get("/hub/graph-studio/spec")
    assert r.status_code == 200
    d = r.json()
    assert d["spec"]["entrypoint"] == "classify"
    assert any(t["nome"] == "rag" for t in d["tipos"])
    assert "by_state_route" in d["routers"]
    assert "greeting" in d["tipos_adicionaveis"]
    assert d["rotas_editaveis"] == []          # nada personalizado no default


def test_nova_rota_greeting_passa_da_validacao_de_entrypoint(client, auth):
    """Regressão do bug reportado: criar um fluxo `greeting` chamado TESTE não
    pode falhar em 'entrypoint_node deve ser um nó do grafo'. Sem Postgres o
    endpoint falha na transação (erro genérico) — mas a validação pré-banco
    tem que passar."""
    with patch("src.api.routers.web.hub.get_admin_auth", return_value=auth):
        client.cookies.set("admin_token", "t")
        r = client.post("/hub/graph-studio/spec/nova-rota", json={
            "rota": "TESTE", "node_type": "greeting", "gatilho": "teste",
        })
    assert r.status_code == 200
    err = r.json().get("error", "")
    assert "entrypoint_node" not in err, f"regrediu: {err}"


def test_nova_rota_rejeita_tipo_invalido(client, auth):
    with patch("src.api.routers.web.hub.get_admin_auth", return_value=auth):
        client.cookies.set("admin_token", "t")
        r = client.post("/hub/graph-studio/spec/nova-rota", json={
            "rota": "TESTE2", "node_type": "ticket_ask_tipo",
        })
    assert "não pode ser adicionado" in r.json().get("error", "")


def test_nova_rota_rejeita_nome_de_rota_fixa(client, auth):
    with patch("src.api.routers.web.hub.get_admin_auth", return_value=auth):
        client.cookies.set("admin_token", "t")
        r = client.post("/hub/graph-studio/spec/nova-rota", json={"rota": "GERAL"})
    assert "fixa" in r.json().get("error", "").lower()
