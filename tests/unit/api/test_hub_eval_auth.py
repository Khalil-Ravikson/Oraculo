# tests/unit/api/test_hub_eval_auth.py
"""
Sprint 3 (Fase 3) — unifica os dois blocos duplicados de rotas de eval
(sem prefixo, órfão, removido; com prefixo /eval/*, o único usado por
eval.html) e fecha o buraco de autenticação: nenhum dos 8 endpoints
verificava `_verificar_cookie` antes desta fase, permitindo qualquer
anônimo disparar chamadas reais ao LLM sem login.
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


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/hub/eval/dataset", {}),
        ("post", "/hub/eval/single", {"json": {"question": "qual o calendário?"}}),
        ("post", "/hub/eval/run", {"json": {}}),
        ("get", "/hub/eval/stream", {}),
        ("get", "/hub/eval/results", {}),
        ("post", "/hub/eval/query", {"json": {"pergunta": "oi"}}),
        ("get", "/hub/eval/eventos", {}),
        ("post", "/hub/eval/run-full", {"json": {}}),
    ],
)
def test_eval_endpoint_sem_cookie_retorna_401(client, method, path, kwargs):
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 401


def test_eval_dataset_com_cookie_valido_retorna_dados(client):
    payload = TokenPayload(sub="admin", iat=int(time.time()), exp=int(time.time()) + 3600)
    mock_auth = MagicMock()
    mock_auth.token_esta_bloqueado.return_value = False
    mock_auth.verificar_token.return_value = payload

    with patch("src.api.routers.web.hub.get_admin_auth", return_value=mock_auth):
        client.cookies.set("admin_token", "valid-token")
        r = client.get("/hub/eval/dataset")

    assert r.status_code == 200
    assert "dataset" in r.json()
