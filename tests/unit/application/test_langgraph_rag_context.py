# tests/unit/application/test_langgraph_rag_context.py
"""
Fase 3.5: antes desta fase, `langgraph_experiment/nodes.py::responder_rag_direto`
chamava RAGSearchService/SynthesisService só com a mensagem crua — rota fina,
histórico (L1) e fatos (L4) se perdiam ao entrar no grafo, e o SemanticCache
nunca era consultado (toda pergunta repetida pagava LLM de novo). Estes testes
cobrem que o contexto agora chega nos dois serviços, e que um hit de cache
evita as chamadas de RAG/síntese por completo.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langgraph_experiment.state import OraculoState
from langgraph_experiment.nodes import responder_rag_direto, rag_node
from src.application.runtime.dispatcher_langgraph import _reset_payload_para_rota


def _mock_rag_result(chunks=None):
    result = MagicMock()
    result.ok = True
    result.data = {"found": True, "chunks": chunks or [{"text": "chunk 1"}]}
    return result


def _mock_synth_result(answer="resposta sintetizada"):
    result = MagicMock()
    result.ok = True
    result.answer = answer
    result.error = ""
    return result


def test_oraculo_state_tem_campos_de_contexto_com_default_vazio():
    state = OraculoState()
    assert state.rota == ""
    assert state.history == ""
    assert state.fatos == []


def test_reset_payload_para_rota_inclui_contexto():
    payload = _reset_payload_para_rota(
        "sess-1", "qual o edital?", "rag",
        rota="EDITAL", history="hist", fatos=["fato1"],
    )
    assert payload["rota"] == "EDITAL"
    assert payload["history"] == "hist"
    assert payload["fatos"] == ["fato1"]


def test_reset_payload_para_rota_defaults_retrocompativeis():
    """Chamada só com os 3 args posicionais originais (ticket/crud, sem
    contexto de RAG) continua funcionando sem quebrar."""
    payload = _reset_payload_para_rota("sess-1", "abrir chamado", "ticket")
    assert payload["rota"] == ""
    assert payload["history"] == ""
    assert payload["fatos"] == []


@pytest.mark.asyncio
async def test_responder_rag_direto_propaga_rota_history_fatos():
    """Cache miss → RAGSearchService/SynthesisService devem receber rota,
    doc_type resolvido, histórico e fatos — não mais os defaults genéricos."""
    rag_instance = MagicMock()
    rag_instance.buscar = AsyncMock(return_value=_mock_rag_result())
    synth_instance = MagicMock()
    synth_instance.sintetizar = AsyncMock(return_value=_mock_synth_result())
    cache_instance = MagicMock()
    cache_instance.get = AsyncMock(return_value=None)
    cache_instance.set = AsyncMock(return_value=None)

    with patch("src.agents.academic_knowledge.service.RAGSearchService", return_value=rag_instance), \
         patch("src.agents.academic_knowledge.synthesis.SynthesisService", return_value=synth_instance), \
         patch("src.infrastructure.semantic_cache.SemanticCache", return_value=cache_instance):

        answer = await responder_rag_direto(
            "quando é o edital do PAES?", rota="EDITAL",
            history="usuário perguntou sobre matrícula antes",
            fatos=["curso: Engenharia Civil"], session_id="sess-1",
        )

    assert answer == "resposta sintetizada"

    rag_instance.buscar.assert_awaited_once()
    _, kwargs = rag_instance.buscar.call_args
    assert kwargs["rota"] == "EDITAL"
    assert kwargs["doc_type"] == "edital"
    assert kwargs["historico"] == "usuário perguntou sobre matrícula antes"
    assert kwargs["fatos"] == ["curso: Engenharia Civil"]

    synth_instance.sintetizar.assert_awaited_once()
    _, kwargs = synth_instance.sintetizar.call_args
    plan_ctx = kwargs["plan_ctx"]
    assert plan_ctx["route"] == "EDITAL"
    assert plan_ctx["history"] == "usuário perguntou sobre matrícula antes"
    assert plan_ctx["fatos"] == ["curso: Engenharia Civil"]
    assert plan_ctx["session_id"] == "sess-1"

    cache_instance.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_responder_rag_direto_cache_hit_pula_rag_e_sintese():
    cache_instance = MagicMock()
    cache_instance.get = AsyncMock(return_value={"answer": "resposta do cache"})

    rag_cls = MagicMock()
    synth_cls = MagicMock()

    with patch("src.agents.academic_knowledge.service.RAGSearchService", rag_cls), \
         patch("src.agents.academic_knowledge.synthesis.SynthesisService", synth_cls), \
         patch("src.infrastructure.semantic_cache.SemanticCache", return_value=cache_instance):

        answer = await responder_rag_direto("qual o calendário?", rota="CALENDARIO")

    assert answer == "resposta do cache"
    rag_cls.assert_not_called()
    synth_cls.assert_not_called()


@pytest.mark.asyncio
async def test_responder_rag_direto_nao_consulta_cache_pra_rota_excluida():
    """SIGAA nunca deveria ser cacheado (dado dinâmico por usuário) — cache
    não é nem consultado."""
    rag_instance = MagicMock()
    rag_instance.buscar = AsyncMock(return_value=_mock_rag_result())
    synth_instance = MagicMock()
    synth_instance.sintetizar = AsyncMock(return_value=_mock_synth_result())
    cache_cls = MagicMock()

    with patch("src.agents.academic_knowledge.service.RAGSearchService", return_value=rag_instance), \
         patch("src.agents.academic_knowledge.synthesis.SynthesisService", return_value=synth_instance), \
         patch("src.infrastructure.semantic_cache.SemanticCache", cache_cls):

        await responder_rag_direto("qual minha nota?", rota="SIGAA")

    cache_cls.assert_not_called()


@pytest.mark.asyncio
async def test_rag_node_le_contexto_do_state():
    state = OraculoState(
        session_id="sess-2", message="qual o edital?", route="rag",
        rota="EDITAL", history="hist", fatos=["fato1"],
    )
    with patch(
        "langgraph_experiment.nodes.responder_rag_direto",
        new_callable=AsyncMock, return_value="resposta",
    ) as mock_responder:
        result = await rag_node(state)

    assert result == {"answer": "resposta"}
    mock_responder.assert_awaited_once_with(
        "qual o edital?", rota="EDITAL", history="hist", fatos=["fato1"], session_id="sess-2",
    )
