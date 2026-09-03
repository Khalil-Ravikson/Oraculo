"""Trava os diagramas de `reference_flows.py` contra o código real."""

from src.graph_studio.reference_flows import FLUXOS, como_json
from src.infrastructure import route_registry


def test_toda_edge_referencia_um_no_do_mesmo_fluxo():
    for f in FLUXOS:
        ids = {n["id"] for n in f.nodes}
        for e in f.edges:
            assert e["de"] in ids, f"{f.slug}: aresta de '{e['de']}' sem nó"
            assert e["para"] in ids, f"{f.slug}: aresta para '{e['para']}' sem nó"


def test_fluxos_de_rota_batem_com_route_registry():
    for f in FLUXOS:
        if not f.rota:
            continue
        assert f.rota in route_registry.ROTAS, f"{f.slug} → rota '{f.rota}' não existe"


def test_metadados_minimos():
    slugs = [f.slug for f in FLUXOS]
    assert len(slugs) == len(set(slugs))
    for f in FLUXOS:
        assert f.nome and f.descricao and f.fonte
        assert f.nodes


def test_como_json_serializa():
    dados = como_json()
    assert isinstance(dados, list) and dados
    assert {"slug", "nome", "nodes", "edges"} <= set(dados[0])
