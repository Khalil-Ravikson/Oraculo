"""
Plano A / Fase 2 — `src/infrastructure/route_registry.py`.

Contrato da camada síncrona (caminho quente): Redis → `_DEFAULTS`, degradação
sem exceção, reverso node→rota, predicado de delegação, validação de escrita,
e a paridade `_DEFAULTS` ↔ migration 010 ↔ `contracts.ROTAS_SEM_CACHE`.
"""
import importlib.util
import pathlib

import pytest

from src.infrastructure import route_registry as rr
from src.router.contracts import ROTAS_SEM_CACHE

_MIG = pathlib.Path(__file__).parents[3] / "migrations" / "versions" / "010_route_registry.py"
_spec = importlib.util.spec_from_file_location("_migration_010", _MIG)
_migration_010 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration_010)
SEED_010 = {row["rota"]: row for row in _migration_010._SEED}


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


def test_get_rota_desconhecida_retorna_config_legado(fake_redis):
    cfg = rr.get("ROTA_QUE_NAO_EXISTE")
    assert cfg.rota == "ROTA_QUE_NAO_EXISTE" and cfg.owner == "legacy"


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


def test_delega_para_legado():
    geral = rr._DEFAULTS["GERAL"]                 # owner=langgraph
    sigaa = rr._DEFAULTS["SIGAA"]                 # owner=langgraph_conditional
    assert geral.delega_para_legado(False) is False
    assert geral.delega_para_legado(True) is False
    assert sigaa.delega_para_legado(False) is True     # flag OFF → dispatcher.py
    assert sigaa.delega_para_legado(True) is False     # flag ON → grafo


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
        "owner": "legacy", "entrypoint_node": "rag", "agente": "academic_knowledge",
        "planner_steps": ["rag_search"], "k": 5,
    })
    assert out["owner"] == "legacy" and out["k"] == 5


# ─── Snapshot p/ o Hub ──────────────────────────────────────────────────────

def test_snapshot_merge_com_defaults():
    snap = {s["rota"]: s for s in rr.snapshot([])}
    assert len(snap) == 11
    assert snap["GERAL"]["versao"] == 0          # não gravada
    assert snap["GERAL"]["planner_steps"] == ["rag_search"]
    assert snap["SIGAA"]["owner"] == "langgraph_conditional"
    assert all(s["fixa"] for s in snap.values())


def test_snapshot_inclui_rota_personalizada():
    from dataclasses import replace
    custom = replace(rr.merge_default("TESTE_GUI", {"owner": "legacy"}), versao=3)
    snap = {s["rota"]: s for s in rr.snapshot([custom])}
    assert len(snap) == 12
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
    assert cfg.owner == "legacy" and cfg.k == 0


def test_rota_desconhecida_delega_para_legado_e_nao_gateia():
    # Comportamento antigo de rota fora de _ROTAS_LANGGRAPH: dispatcher.py,
    # sem circuit-breaker, sem detour, hint = GERAL.
    u = rr.get("INTENT_CUSTOM_DO_OPERADOR")
    assert u.owner == "legacy"
    assert u.delega_para_legado(False) is True and u.delega_para_legado(True) is True
    assert u.agente is None and u.permite_detour is False
    assert u.doc_type == "geral" and u.k == 6 and u.planner_steps == ("rag_search",)


# ─── Paridade das 3 fontes ─────────────────────────────────────────────────

def test_defaults_batem_com_o_seed_da_migration_010():
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
        seed_steps = tuple(seed["planner_steps"]) if seed["planner_steps"] is not None else None
        assert cfg.planner_steps == seed_steps, rota


def test_rotas_sem_cache_derivam_dos_defaults():
    derivado = frozenset(r for r, c in rr._DEFAULTS.items() if not c.cacheavel)
    assert derivado == ROTAS_SEM_CACHE


def test_defaults_cobrem_rotas_validas():
    from src.router.contracts import ROTAS_VALIDAS
    assert set(rr._DEFAULTS) == set(ROTAS_VALIDAS)
