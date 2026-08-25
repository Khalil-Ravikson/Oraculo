# tests/unit/application/test_langgraph_rag_celery_dispatch.py
"""
Fase 2b do plano de integração (Decisão 02): com
settings.FEATURE_LANGGRAPH_CELERY_DISPATCH ligada,
langgraph_experiment/nodes.py::responder_rag_direto passa a despachar RAG+
síntese pros workers Celery especializados (filas rag_search/synthesis) via
um chord, em vez de chamar RAGSearchService/SynthesisService in-process.

Estes testes mockam a camada Celery inteira (chord/AsyncResult) — não
substituem o teste de carga contra um stack real (Redis + workers rag_search/
synthesis vivos) que o plano exige antes de considerar a Fase 2b fechada;
cobrem só a lógica de despacho/timeout/erro do lado do node.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_experiment.nodes import responder_rag_direto


def _mock_async_result(retorno=None, excecao=None):
    r = MagicMock()
    if excecao is not None:
        r.get = MagicMock(side_effect=excecao)
    else:
        r.get = MagicMock(return_value=retorno)
    return r


def _mock_chord_cls(async_result):
    chord_instance = MagicMock()
    chord_instance.apply_async = MagicMock(return_value=async_result)
    return MagicMock(return_value=chord_instance)


@pytest.fixture(autouse=True)
def _flag_ligada(monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "FEATURE_LANGGRAPH_CELERY_DISPATCH", True)


@pytest.mark.asyncio
async def test_responder_rag_direto_despacha_via_celery_quando_flag_ligada():
    cache_instance = MagicMock()
    cache_instance.get = AsyncMock(return_value=None)

    async_result = _mock_async_result(
        retorno={"status": "ok", "answer": "resposta via celery", "plan_id": "lg-teste"}
    )
    chord_cls = _mock_chord_cls(async_result)

    with patch("src.infrastructure.semantic_cache.SemanticCache", return_value=cache_instance), \
         patch("celery.chord", chord_cls):
        answer = await responder_rag_direto(
            "quando é o edital do PAES?", rota="EDITAL",
            history="hist", fatos=["curso: Engenharia Civil"], session_id="sess-1",
        )

    assert answer == "resposta via celery"

    chord_cls.assert_called_once()
    header, _body_sig = chord_cls.call_args[0]
    assert len(header) == 1  # 1 signature de worker_rag_search_task no header do chord

    async_result.get.assert_called_once()
    _, kwargs = async_result.get.call_args
    assert kwargs["timeout"] > 0  # RAG_SEARCH_TIMEOUT_S + SYNTHESIS_TIMEOUT_S


@pytest.mark.asyncio
async def test_responder_rag_direto_via_celery_nao_consulta_rag_synthesis_in_process():
    """Com a flag ligada, RAGSearchService/SynthesisService in-process não
    devem ser instanciados — a busca/síntese acontece só nos workers."""
    cache_instance = MagicMock()
    cache_instance.get = AsyncMock(return_value=None)
    async_result = _mock_async_result(retorno={"status": "ok", "answer": "ok"})
    chord_cls = _mock_chord_cls(async_result)
    rag_cls = MagicMock()
    synth_cls = MagicMock()

    with patch("src.infrastructure.semantic_cache.SemanticCache", return_value=cache_instance), \
         patch("celery.chord", chord_cls), \
         patch("src.agents.academic_knowledge.service.RAGSearchService", rag_cls), \
         patch("src.agents.academic_knowledge.synthesis.SynthesisService", synth_cls):
        await responder_rag_direto("qual o calendário?", rota="CALENDARIO")

    rag_cls.assert_not_called()
    synth_cls.assert_not_called()


@pytest.mark.asyncio
async def test_responder_rag_direto_via_celery_status_erro_devolve_mensagem_do_worker():
    cache_instance = MagicMock()
    cache_instance.get = AsyncMock(return_value=None)
    async_result = _mock_async_result(
        retorno={"status": "error", "answer": "", "plan_id": "lg-teste"}
    )
    chord_cls = _mock_chord_cls(async_result)

    with patch("src.infrastructure.semantic_cache.SemanticCache", return_value=cache_instance), \
         patch("celery.chord", chord_cls):
        answer = await responder_rag_direto("pergunta qualquer", rota="GERAL")

    assert "não encontrei" in answer.lower() or "nao encontrei" in answer.lower()


@pytest.mark.asyncio
async def test_responder_rag_direto_via_celery_timeout_devolve_mensagem_amigavel():
    cache_instance = MagicMock()
    cache_instance.get = AsyncMock(return_value=None)
    async_result = _mock_async_result(excecao=TimeoutError("chord não terminou a tempo"))
    chord_cls = _mock_chord_cls(async_result)

    with patch("src.infrastructure.semantic_cache.SemanticCache", return_value=cache_instance), \
         patch("celery.chord", chord_cls):
        answer = await responder_rag_direto("pergunta qualquer", rota="GERAL")

    assert "lentidão" in answer.lower()


@pytest.mark.asyncio
async def test_responder_rag_direto_via_celery_ainda_respeita_cache_hit():
    """O cache continua sendo checado ANTES do despacho Celery — hit evita
    tocar o chord por completo, igual ao caminho in-process."""
    cache_instance = MagicMock()
    cache_instance.get = AsyncMock(return_value={"answer": "resposta do cache"})
    chord_cls = MagicMock()

    with patch("src.infrastructure.semantic_cache.SemanticCache", return_value=cache_instance), \
         patch("celery.chord", chord_cls):
        answer = await responder_rag_direto("qual o calendário?", rota="CALENDARIO")

    assert answer == "resposta do cache"
    chord_cls.assert_not_called()
