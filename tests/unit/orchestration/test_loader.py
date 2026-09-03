"""
`orchestration/loader.py` — carregamento da `GraphSpec` ativa (ADR 0008 Fase 5).
Cobre a degradação: sem Redis nem Postgres, cai no `specs/default.json`.
"""
from __future__ import annotations

from src.application.orchestration import loader
from src.application.orchestration.spec import GraphSpec


def test_default_spec_e_valida():
    spec = loader.default_spec()
    assert isinstance(spec, GraphSpec)
    assert spec.validate_topology() == []
    assert spec.entrypoint == "classify"


def test_carregar_spec_ativa_cai_no_default_sem_infra(monkeypatch):
    monkeypatch.setattr(loader, "_ler_redis", lambda: None)
    monkeypatch.setattr(loader, "_ler_postgres_sync", lambda: None)
    spec = loader.carregar_spec_ativa()
    assert spec.model_dump() == loader.default_spec().model_dump()


def test_carregar_spec_ativa_usa_redis_quando_presente(monkeypatch):
    raw = loader.default_spec().model_dump()
    raw["version"] = 99
    monkeypatch.setattr(loader, "_ler_redis", lambda: raw)
    spec = loader.carregar_spec_ativa()
    assert spec.version == 99


def test_spec_invalida_no_redis_cai_no_default(monkeypatch):
    monkeypatch.setattr(loader, "_ler_redis", lambda: {"version": 1, "entrypoint": "x", "nodes": [], "edges": []})
    monkeypatch.setattr(loader, "_ler_postgres_sync", lambda: None)
    spec = loader.carregar_spec_ativa()
    assert spec.model_dump() == loader.default_spec().model_dump()
