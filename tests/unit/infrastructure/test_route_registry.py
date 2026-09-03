"""
Plano A / Fase 2 + ADR 0008 — `src/infrastructure/route_registry.py`.

Contrato da camada síncrona (caminho quente): Redis → `_DEFAULTS`, degradação
sem exceção, reverso node→rota, validação de escrita, e a paridade
`_DEFAULTS` ↔ migrations 010/022/023 ↔ `contracts.ROTAS_SEM_CACHE`.
"""
import importlib.util
import pathlib

import pytest

from src.infrastructure import route_registry as rr
from src.router.contracts import ROTAS_SEM_CACHE

_VERS = pathlib.Path(__file__).parents[3] / "migrations" / "versions"


def _carregar_seed(nome: str) -> dict:
    spec = importlib.util.spec_from_file_location(f"_mig_{nome}", _VERS / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {row["rota"]: row for row in getattr(mod, "_SEED", [])}


# Todas as migrations que semeiam linhas em `route_registry`. `_DEFAULTS` do
# código deve bater com a UNIÃO delas, DEPOIS de aplicar as transformações da
# migration 023 (ADR 0008 Fase 3): owner→"langgraph" em todas, planner_steps
# removido.
_SEED_BRUTO = {
    **_carregar_seed("010_route_registry"),
    **_carregar_seed("022_route_registry_escalar_humano"),
}
SEED_010 = {
    rota: {**{k: v for k, v in row.items() if k != "planner_steps"}, "owner": "langgraph"}
    for rota, row in _SEED_BRUTO.items()
}


class _FakeRedisText:
    def __init__(self, dados=None):
        self._d = dict(dados or {})

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v):
        self._d[k] = str(v)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedisText()
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", lambda: fake)
    return fake


# ─── Leitura / degradação ───────────────────────────────────────────────────

def test_get_cai_no_default_quando_redis_vazio(fake_redis):
    assert rr.get("GERAL").entrypoint_node == "rag"
    assert rr.get("SIGAA").agente == "sigaa"


def test_get_le_do_redis(fake_redis):
    import json
    cfg = rr.get("GERAL")
    fake_redis.set("route:GERAL", json.dumps({**rr.to_dict(cfg), "k": 42}))
    assert rr.get("GERAL").k == 42


def test_get_rota_desconhecida_cai_no_no_rag(fake_redis):
    cfg = rr.get("ROTA_QUE_NAO_EXISTE")
    assert cfg.rota == "ROTA_QUE_NAO_EXISTE" and cfg.entrypoint_node == "rag"


def test_get_com_redis_fora_do_ar_nao_levanta(monkeypatch):
    def _boom():
        raise ConnectionError("Redis simulado fora")
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", _boom)
    assert rr.get("CALENDARIO").doc_type == "calendario"  # _DEFAULTS


def test_get_com_json_corrompido_no_redis_cai_no_default(fake_redis):
    fake_redis.set("route:WIKI", "{ isso não é json }")
    assert rr.get("WIKI").entrypoint_node == "rag"


# ─── Reverso + predicado de delegação ───────────────────────────────────────

def test_rota_do_node():
    assert rr.rota_do_node("rag") == "GERAL"
    assert rr.rota_do_node("ticket") == "TICKET_ABERTURA"
    assert rr.rota_do_node("crud") == "CRUD"


# ─── Validação de escrita ───────────────────────────────────────────────────

def test_validar_rejeita_owner_invalido():
    with pytest.raises(rr.CamposInvalidos):
        rr.validar_campos({"owner": "qualquer"})


def test_validar_rejeita_node_inexistente():
    with pytest.raises(rr.CamposInvalidos):
        rr.validar_campos({"entrypoint_node": "node_fantasma"})


def test_validar_rejeita_agente_inexistente():
    with pytest.raises(rr.CamposInvalidos):
        rr.validar_campos({"agente": "agente_que_nao_existe"})


def test_validar_rejeita_campo_nao_editavel():
    with pytest.raises(rr.CamposInvalidos):
        rr.validar_campos({"rota": "OUTRA"})
    with pytest.raises(rr.CamposInvalidos):
        rr.validar_campos({"versao": 99})


def test_validar_rejeita_k_negativo():
    with pytest.raises(rr.CamposInvalidos):
        rr.validar_campos({"k": -1})


def test_validar_normaliza_bool_e_agente_vazio():
    out = rr.validar_campos({"cacheavel": 1, "agente": ""})
    assert out["cacheavel"] is True and out["agente"] is None


def test_validar_aceita_campos_ok():
    out = rr.validar_campos({
        "owner": "langgraph", "entrypoint_node": "rag", "agente": "academic_knowledge",
        "k": 5,
    })
    assert out["owner"] == "langgraph" and out["k"] == 5


# ─── Snapshot p/ o Hub ──────────────────────────────────────────────────────

def test_snapshot_merge_com_defaults():
    snap = {s["rota"]: s for s in rr.snapshot([])}
    assert len(snap) == len(rr._DEFAULTS)
    assert snap["GERAL"]["versao"] == 0          # não gravada
    assert "planner_steps" not in snap["GERAL"]
    assert snap["SIGAA"]["owner"] == "langgraph"
    assert all(s["fixa"] for s in snap.values())


def test_snapshot_inclui_rota_personalizada():
    from dataclasses import replace
    custom = replace(rr.merge_default("TESTE_GUI", {"owner": "langgraph"}), versao=3)
    snap = {s["rota"]: s for s in rr.snapshot([custom])}
    assert len(snap) == len(rr._DEFAULTS) + 1
    assert snap["TESTE_GUI"]["fixa"] is False
    assert snap["TESTE_GUI"]["versao"] == 3


def test_validar_nome_rota():
    assert rr.validar_nome_rota(" teste_gui ") == "TESTE_GUI"
    for ruim in ("ab", "1XYZ", "com-traco", "GERAL"):
        with pytest.raises(rr.CamposInvalidos):
            rr.validar_nome_rota(ruim)


def test_pode_apagar():
    assert rr.pode_apagar("TESTE_GUI") is True
    assert rr.pode_apagar("GERAL") is False


def test_merge_default_rota_personalizada_parte_do_unknown():
    cfg = rr.merge_default("NOVA_ROTA", {"k": 0})
    assert cfg.entrypoint_node == "rag" and cfg.k == 0


def test_rota_desconhecida_cai_no_no_rag_sem_gate():
    # ADR 0008: rota fora de `_DEFAULTS` roda no grafo pelo nó `rag`, sem
    # circuit-breaker (agente=None), sem detour, hint = GERAL.
    u = rr.get("INTENT_CUSTOM_DO_OPERADOR")
    assert u.entrypoint_node == "rag"
    assert u.agente is None and u.permite_detour is False
    assert u.doc_type == "geral" and u.k == 6


# ─── Paridade das 3 fontes ─────────────────────────────────────────────────

def test_defaults_batem_com_o_seed_das_migrations():
    assert set(rr._DEFAULTS) == set(SEED_010)
    for rota, cfg in rr._DEFAULTS.items():
        seed = SEED_010[rota]
        assert cfg.entrypoint_node == seed["entrypoint_node"], rota
        assert cfg.owner == seed["owner"], rota
        assert cfg.agente == seed["agente"], rota
        assert cfg.cacheavel == seed["cacheavel"], rota
        assert cfg.permite_detour == seed["permite_detour"], rota
        assert cfg.doc_type == seed["doc_type"], rota
        assert cfg.k == seed["k"], rota


def test_rotas_sem_cache_derivam_dos_defaults():
    derivado = frozenset(r for r, c in rr._DEFAULTS.items() if not c.cacheavel)
    assert derivado == ROTAS_SEM_CACHE


def test_defaults_cobrem_rotas_validas():
    from src.router.contracts import ROTAS_VALIDAS
    assert set(rr._DEFAULTS) == set(ROTAS_VALIDAS)
