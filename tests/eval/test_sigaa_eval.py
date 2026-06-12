import os
import pytest
import asyncio
from pathlib import Path
from unittest.mock import patch
from src.infrastructure.scraping.implementations.sigaa_agent import SIGAAAgent
from src.application.workers.worker_sigaa import _run_notas, _run_indice, _run_historico, _run_turmas, _run_calendario

class MockRedis:
    def __init__(self):
        self.db = {}
    def get(self, key):
        val = self.db.get(key)
        if val is None:
            return None
        return val.encode('utf-8') if isinstance(val, str) else val
    def setex(self, key, time, value):
        self.db[key] = value
    def xadd(self, name, fields, **kwargs):
        pass

# Configura o arquivo de mock para todos os testes desta suíte
@pytest.fixture(scope="module", autouse=True)
def setup_mock_env():
    # Caminho absoluto para a página de mock htm
    mock_path = Path(__file__).parent.parent.parent / "SIGUEMA Acadêmico - Sistema Integrado de Gestão de Atividades Acadêmicas.htm"
    os.environ["SIGAA_MOCK_FILE"] = str(mock_path.resolve())
    
    mock_redis = MockRedis()
    patcher = patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis)
    patcher.start()
    yield
    patcher.stop()
    if "SIGAA_MOCK_FILE" in os.environ:
        del os.environ["SIGAA_MOCK_FILE"]

@pytest.mark.asyncio
async def test_eval_1_mostre_minhas_notas():
    """Eval 1: Mostre minhas notas. Critério: Retornar disciplinas e notas corretamente."""
    agent = SIGAAAgent()
    res = await agent.fluxo_consultar_notas()
    assert res.ok
    assert len(res.data["notas"]) == 6
    
    # Verifica notas
    e_analogica = next(n for n in res.data["notas"] if n["disciplina"] == "ELETRÔNICA ANALÓGICA")
    assert e_analogica["nota"] == "8.5"
    assert e_analogica["media"] == "8.5"
    assert e_analogica["situacao"] == "APROVADO"

@pytest.mark.asyncio
async def test_eval_2_qual_meu_cr():
    """Eval 2: Qual meu CR? Critério: Valor idêntico ao exibido pelo SIGAA (6.963)."""
    agent = SIGAAAgent()
    res = await agent.fluxo_consultar_indice()
    assert res.ok
    assert res.data["cr"] == "6.963"
    assert res.data["ira"] == "6.963"

@pytest.mark.asyncio
async def test_eval_3_quais_disciplinas_faltam():
    """Eval 3: Quais disciplinas faltam? Critério: Comparar histórico com estrutura curricular."""
    event = {
        "plan_id": "eval_test",
        "session_id": "test_session",
        "query": "Quais disciplinas faltam?",
        "step_id": "s1"
    }
    res = await _run_historico(event)
    assert res["status"] == "ok"
    assert "Carga Horária Concluída" in res["answer"]
    assert "3135 horas" in res["answer"]
    assert "80.1%" in res["answer"]
    assert "ELETRÔNICA ANALÓGICA" in res["answer"]
    assert "SISTEMAS INTELIGENTES" in res["answer"]
    assert "VARIÁVEIS COMPLEXAS" in res["answer"]

@pytest.mark.asyncio
async def test_eval_4_horas_complementares_faltam():
    """Eval 4: Quantas horas complementares faltam? Critério: Calcular diferença entre exigido e concluído (150 - 90 = 60)."""
    event = {
        "plan_id": "eval_test",
        "session_id": "test_session",
        "query": "Quantas horas complementares faltam?",
        "step_id": "s1"
    }
    res = await _run_historico(event)
    assert res["status"] == "ok"
    assert "Concluído" in res["answer"]
    assert "90 horas" in res["answer"]
    assert "Exigido" in res["answer"]
    assert "150 horas" in res["answer"]
    assert "Faltam" in res["answer"]
    assert "60 horas" in res["answer"]

@pytest.mark.asyncio
async def test_eval_5_prerequisitos_proximo_semestre():
    """Eval 5: Quais disciplinas do próximo semestre possuem pré-requisito? Critério: Cruzar matriz curricular com histórico."""
    event = {
        "plan_id": "eval_test",
        "session_id": "test_session",
        "query": "Quais matérias posso cursar no próximo semestre?",
        "step_id": "s1"
    }
    res = await _run_turmas(event)
    assert res["status"] == "ok"
    assert "SISTEMAS INTELIGENTES" in res["answer"]
    assert "VARIÁVEIS COMPLEXAS" in res["answer"]
    assert "ELETRÔNICA ANALÓGICA" in res["answer"]
