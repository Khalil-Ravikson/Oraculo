"""
tests/unit/test_query_strategies.py
-------------------------------------
Testes unitários das estratégias de transformação de query.
ZERO IO. ZERO LLM real. Puro Python.

Execute: pytest tests/unit/test_query_strategies.py -v
"""
import pytest
from src.rag.query.protocols import RawQuery
from src.rag.query.strategies import (
    HyDEStrategy,
    KeywordEnrichStrategy,
    MultiQueryStrategy,
    PassthroughStrategy,
    RAGFusionStrategy,
    StepBackStrategy,
)
from src.rag.query.transformer import QueryTransformer


# ─────────────────────────────────────────────────────────────────────────────
# PassthroughStrategy
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestPassthroughStrategy:
    def _query(self, text: str) -> RawQuery:
        return RawQuery(text=text)

    def test_retorna_query_original(self):
        s = PassthroughStrategy()
        r = s.transform(self._query("quando é a matrícula?"))
        assert r.primary == "quando é a matrícula?"
        assert r.original == "quando é a matrícula?"
        assert r.was_transformed is False

    def test_sempre_aplica(self):
        s = PassthroughStrategy()
        assert s.should_apply(self._query("oi")) is True
        assert s.should_apply(self._query("")) is True

    def test_strategy_name(self):
        assert PassthroughStrategy().name == "passthrough"


# ─────────────────────────────────────────────────────────────────────────────
# KeywordEnrichStrategy
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestKeywordEnrichStrategy:
    def _query(self, text: str, fatos: list[str] | None = None) -> RawQuery:
        return RawQuery(text=text, fatos_usuario=fatos or [])

    def test_enriquece_query_com_sinonimos(self):
        s = KeywordEnrichStrategy()
        q = self._query("quando posso cancelar minha matéria?")
        r = s.transform(q)
        # deve incluir sinônimos de "trancamento"
        assert len(r.primary) > len(q.text)
        assert r.was_transformed is True

    def test_injeta_fatos_do_usuario(self):
        s = KeywordEnrichStrategy()
        q = self._query("quando é a matrícula?", fatos=["Aluno de Engenharia Civil, noturno"])
        r = s.transform(q)
        assert "Engenharia Civil" in r.primary or "noturno" in r.primary

    def test_nao_aplica_para_query_muito_tecnica(self):
        s = KeywordEnrichStrategy()
        # Query com 3+ termos técnicos já não precisa de enriquecimento
        q = self._query("matricula veteranos 2026.1 calendario prazo edital CECEN")
        assert s.should_apply(q) is False

    def test_query_enriquecida_nao_excede_limite(self):
        s = KeywordEnrichStrategy()
        texto_longo = "matricula " * 30  # query muito longa
        q = self._query(texto_longo)
        r = s.transform(q)
        assert len(r.primary) <= 300

    def test_keywords_populadas(self):
        s = KeywordEnrichStrategy()
        q = self._query("quero informações sobre as cotas do PAES")
        r = s.transform(q)
        assert isinstance(r.keywords, list)


# ─────────────────────────────────────────────────────────────────────────────
# StepBackStrategy
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestStepBackStrategy:
    def _query(self, text: str) -> RawQuery:
        return RawQuery(text=text)

    def test_remove_datas_especificas(self):
        s = StepBackStrategy()
        q = self._query("qual a matrícula do dia 03/02/2026?")
        r = s.transform(q)
        assert "03/02/2026" not in r.step_back

    def test_remove_semestres(self):
        s = StepBackStrategy()
        q = self._query("matrícula veteranos 2026.1 fevereiro")
        r = s.transform(q)
        assert "2026.1" not in r.step_back

    def test_generaliza_siglas_cotas(self):
        s = StepBackStrategy()
        q = self._query("vagas BR-PPI Engenharia Civil UEMA")
        r = s.transform(q)
        assert "BR-PPI" not in r.step_back
        assert "cota" in r.step_back.lower() or len(r.step_back) > 0

    def test_primary_e_original(self):
        """O primary deve ser a query original; step_back é o generalizado."""
        s = StepBackStrategy()
        original = "vagas BR-PPI Engenharia Civil 2026.1"
        r = s.transform(self._query(original))
        assert r.primary == original  # primary NÃO muda
        assert r.step_back != original  # step_back muda

    def test_step_back_nao_fica_vazio(self):
        s = StepBackStrategy()
        q = self._query("matrícula 03/02/2026 a 07/02/2026 2026.1")
        r = s.transform(q)
        assert len(r.step_back) >= 5  # fallback das primeiras 3 palavras

    def test_aplica_query_longa(self):
        s = StepBackStrategy()
        assert s.should_apply(self._query("quando são os prazos de matrícula veteranos?")) is True

    def test_nao_aplica_query_curta(self):
        s = StepBackStrategy()
        assert s.should_apply(self._query("matrícula")) is False


# ─────────────────────────────────────────────────────────────────────────────
# HyDEStrategy
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestHyDEStrategy:
    def _query(self, text: str) -> RawQuery:
        return RawQuery(text=text)

    def test_sem_llm_retorna_passthrough(self):
        s = HyDEStrategy(llm_provider=None)
        q = self._query("quando é a matrícula?")
        r = s.transform(q)
        assert r.primary == q.text
        assert r.was_transformed is False

    def test_sem_llm_nao_aplica(self):
        s = HyDEStrategy(llm_provider=None)
        assert s.should_apply(self._query("quando é a matrícula?")) is False

    def test_com_llm_mock_transforma(self, mock_llm_hyde):
        s = HyDEStrategy(llm_provider=mock_llm_hyde)
        q = self._query("quando é a matrícula de veteranos?")
        # Faz should_apply retornar True
        assert s.should_apply(q) is True
        r = s.transform(q)
        assert r.was_transformed is True
        assert r.hypothetical_doc  # deve ter doc hipotético
        assert r.primary != q.text  # primary = doc hipotético

    def test_com_llm_falho_retorna_passthrough(self):
        """Mesmo com LLM que falha, deve retornar gracefully."""
        from unittest.mock import MagicMock
        broken_llm = MagicMock()
        broken_llm.gerar_resposta_sincrono.side_effect = RuntimeError("API indisponível")

        s = HyDEStrategy(llm_provider=broken_llm)
        q = self._query("quando é a matrícula?")
        r = s.transform(q)
        # Graceful degradation: retorna original sem crash
        assert r.primary == q.text
        assert r.was_transformed is False

    def test_aplica_apenas_perguntas_abertas(self):
        s = HyDEStrategy(llm_provider=MagicMock())
        assert s.should_apply(self._query("quando é a matrícula?")) is True
        assert s.should_apply(self._query("o que é o PAES?")) is True
        # Queries muito técnicas não precisam de HyDE
        assert s.should_apply(self._query("matricula veteranos 2026.1 calendario prazo")) is False


# ─────────────────────────────────────────────────────────────────────────────
# MultiQueryStrategy
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestMultiQueryStrategy:
    def test_sem_llm_retorna_passthrough(self):
        s = MultiQueryStrategy(llm_provider=None)
        q = RawQuery(text="quais documentos e prazos para o PAES?")
        r = s.transform(q)
        assert r.primary == q.text
        assert not r.variants

    def test_com_llm_mock_gera_sub_queries(self, mock_llm):
        mock_llm.default_response = "documentos necessários inscrição PAES 2026\nprazo inscrição cronograma\ncategorias cotas requisitos"
        s = MultiQueryStrategy(llm_provider=mock_llm)
        q = RawQuery(text="quais documentos e datas para me inscrever com cota PcD no PAES?")
        assert s.should_apply(q) is True
        r = s.transform(q)
        assert r.was_transformed is True
        # primary = primeira sub-query
        assert len(r.primary) > 5
        # variants = restantes
        assert isinstance(r.variants, list)

    def test_nao_aplica_query_curta(self):
        s = MultiQueryStrategy(llm_provider=MagicMock())
        assert s.should_apply(RawQuery(text="matrícula quando?")) is False

    def test_falha_graceful(self):
        from unittest.mock import MagicMock
        broken = MagicMock()
        broken.gerar_resposta_sincrono.side_effect = Exception("timeout")
        s = MultiQueryStrategy(llm_provider=broken)
        q = RawQuery(text="quais documentos e prazos e regras para o PAES e as cotas?")
        r = s.transform(q)
        assert r.primary == q.text  # fallback seguro


# ─────────────────────────────────────────────────────────────────────────────
# RAGFusionStrategy
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestRAGFusionStrategy:
    def test_sem_llm_retorna_passthrough(self):
        s = RAGFusionStrategy(llm_provider=None)
        q = RawQuery(text="email do CTIC")
        r = s.transform(q)
        assert r.primary == q.text

    def test_com_llm_mock_gera_variantes(self, mock_llm):
        mock_llm.default_response = "contato suporte técnico CTIC UEMA\ne-mail setor TI universidade\ntelefone informática UEMA"
        s = RAGFusionStrategy(llm_provider=mock_llm, n_variantes=3)
        q = RawQuery(text="email do CTIC")
        assert s.should_apply(q) is True
        r = s.transform(q)
        assert r.was_transformed is True
        assert len(r.variants) >= 1  # pelo menos 1 variante


# ─────────────────────────────────────────────────────────────────────────────
# QueryTransformer (orquestrador)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestQueryTransformer:
    def test_first_match_aplica_primeira_estrategia_aceita(self):
        """Deve aplicar só a primeira estratégia que aceita a query."""
        s1 = PassthroughStrategy()
        s2 = KeywordEnrichStrategy()
        transformer = QueryTransformer([s1, s2], first_match=True)
        q = RawQuery(text="matrícula")
        r = transformer.transform(q)
        # PassthroughStrategy sempre aceita, então deve parar nela
        assert r.strategy_used == "passthrough"

    def test_first_match_pula_estrategia_que_nao_aceita(self):
        """Se a primeira estratégia não aceita, deve tentar a próxima."""
        # HyDE sem LLM retorna should_apply=False
        s1 = HyDEStrategy(llm_provider=None)
        s2 = KeywordEnrichStrategy()
        transformer = QueryTransformer([s1, s2], first_match=True)
        q = RawQuery(text="quando é a matrícula?")
        r = transformer.transform(q)
        # HyDE não aceita (sem LLM), KeywordEnrich aceita
        assert r.strategy_used != "hyde"

    def test_build_for_route_calendario(self):
        t = QueryTransformer.build_for_route("CALENDARIO")
        assert t is not None
        q = RawQuery(text="quando é a matrícula?")
        r = t.transform(q)
        assert r.primary  # deve retornar algo

    def test_build_for_route_edital(self):
        t = QueryTransformer.build_for_route("EDITAL")
        assert t is not None

    def test_build_for_route_wiki(self):
        t = QueryTransformer.build_for_route("WIKI")
        assert t is not None

    def test_build_for_rota_desconhecida_usa_geral(self):
        t = QueryTransformer.build_for_route("ROTA_INEXISTENTE")
        assert t is not None
        q = RawQuery(text="teste")
        r = t.transform(q)
        assert r.primary  # não pode crashar

    def test_query_vazia_retorna_passthrough(self):
        t = QueryTransformer.build_for_route("CALENDARIO")
        r = t.transform(RawQuery(text=""))
        assert r.primary == ""
        assert r.was_transformed is False

    def test_accumulate_mode_acumula_variantes(self):
        """No modo accumulate, todas as estratégias contribuem."""
        s1 = KeywordEnrichStrategy()
        s2 = StepBackStrategy()
        transformer = QueryTransformer([s1, s2], first_match=False)
        q = RawQuery(text="quando é a matrícula de veteranos 03/02/2026?")
        r = transformer.transform(q)
        assert "+" in r.strategy_used  # múltiplas estratégias

    def test_build_rag_fusion(self):
        t = QueryTransformer.build_rag_fusion()
        assert t is not None
        q = RawQuery(text="email do CTIC suporte")
        r = t.transform(q)
        assert r.primary  # não crasha

    def test_never_raises_exception(self):
        """O transformer NUNCA deve deixar uma exceção vazar."""
        from unittest.mock import MagicMock
        broken = MagicMock()
        broken.name = "broken"
        broken.should_apply.return_value = True
        broken.transform.side_effect = RuntimeError("kaboom")

        transformer = QueryTransformer([broken, PassthroughStrategy()])
        r = transformer.transform(RawQuery(text="teste"))
        # Deve ter passado para o PassthroughStrategy ou retornado error_passthrough
        assert r.primary == "teste"