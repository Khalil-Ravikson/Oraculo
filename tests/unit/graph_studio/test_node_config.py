"""Testes de src/graph/node_config.py::mesclar_com_registry (função pura, sem DB)."""

from src.graph_studio.node_config import mesclar_com_registry
from datetime import datetime, timezone


def _no(node_id: str) -> dict:
    return {"id": node_id, "type": "mock", "metadata": {}, "input_ports": [], "output_ports": []}


class TestMesclarComRegistry:
    """Testes de mesclar_com_registry."""

    def test_no_sem_linha_config_e_implicitamente_habilitado(self):
        resultado = mesclar_com_registry([_no("stt_default")], [])
        assert resultado[0]["habilitado"] is True
        assert resultado[0]["versao"] == 0
        assert resultado[0]["config_overrides"] == {}
        assert resultado[0]["atualizado_por"] is None

    def test_no_com_linha_config_usa_valor_gravado(self):
        agora = datetime.now(timezone.utc)
        config_rows = [{
            "node_id": "lab_mcp", "habilitado": False,
            "config_overrides": {"x": 1}, "versao": 3,
            "atualizado_em": agora, "atualizado_por": "admin",
        }]
        resultado = mesclar_com_registry([_no("lab_mcp")], config_rows)
        assert resultado[0]["habilitado"] is False
        assert resultado[0]["versao"] == 3
        assert resultado[0]["config_overrides"] == {"x": 1}
        assert resultado[0]["atualizado_por"] == "admin"
        assert resultado[0]["atualizado_em"] == agora.isoformat()

    def test_mescla_preserva_campos_do_registry(self):
        no = {
            "id": "llm_default", "type": "llm_provider",
            "metadata": {"description": "x"},
            "input_ports": [{"name": "prompt"}],
            "output_ports": [{"name": "response"}],
        }
        resultado = mesclar_com_registry([no], [])
        assert resultado[0]["type"] == "llm_provider"
        assert resultado[0]["metadata"] == {"description": "x"}
        assert resultado[0]["input_ports"] == [{"name": "prompt"}]

    def test_mescla_varios_nos_alguns_com_config_outros_sem(self):
        config_rows = [
            {"node_id": "lab_rest", "habilitado": False, "config_overrides": {},
             "versao": 1, "atualizado_em": None, "atualizado_por": "x"},
        ]
        resultado = mesclar_com_registry(
            [_no("lab_rest"), _no("lab_mcp")], config_rows
        )
        por_id = {r["id"]: r for r in resultado}
        assert por_id["lab_rest"]["habilitado"] is False
        assert por_id["lab_mcp"]["habilitado"] is True

    def test_lista_vazia_de_nos(self):
        assert mesclar_com_registry([], []) == []
