"""
tests/eval/test_rag_behavior.py
---------------------------------
Testes de comportamento do RAG e tomada de decisão da LLM.

Estas categorias de teste são:
  - Testes de INGESTÃO: verificam que os chunks são gerados corretamente
  - Testes de AVALIAÇÃO DE RESPOSTA: verificam qualidade/fidelidade das respostas
  - Testes de COMPORTAMENTO DA LLM: verificam guardrails e decisões
  - Testes de SEMANTICIDADE: verificam roteamento semântico

Precisam de Redis rodando mas NÃO precisam de LLM real (usam mocks).
Execute: pytest tests/eval/ -v -m "not llm"

Para testes com LLM real:
Execute: pytest tests/eval/ -v -m llm
"""
from __future__ import annotations

import hashlib
import pytest
from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIA 1: Testes de Ingestão
# Verificam que o pipeline de ingestão gera chunks corretos
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestIngestionBehavior:
    """Testa que a ingestão gera chunks com as propriedades corretas."""

    def test_chunk_tem_prefixo_hierarquico(self, sample_chunks):
        """Todo chunk deve começar com prefixo [LABEL | doc_type]."""
        from src.rag.ingestion.chunker_factory import RecursiveChunker
        import os

        chunker = RecursiveChunker(chunk_size=400, overlap=60)
        texto = "EVENTO: Matrícula de veteranos | DATA: 03/02/2026 a 07/02/2026 | SEM: 2026.1"
        chunks = chunker.chunk(texto, source="calendario-academico-2026.pdf", doc_type="calendario")

        assert chunks
        # A pipeline (não o chunker diretamente) adiciona o prefixo
        # Verificamos que o chunker gera o metadata necessário
        assert chunks[0].metadata["doc_type"] == "calendario"

    def test_pipeline_adiciona_prefixo_anti_alucinacao(self, tmp_path):
        """O IngestionPipeline deve adicionar prefixo hierárquico a cada chunk."""
        from src.rag.ingestion.chunker_factory import RecursiveChunker

        texto = "EVENTO: Matrícula | DATA: 03/02/2026"
        tmp_file = tmp_path / "test.txt"
        tmp_file.write_text(texto, encoding="utf-8")

        # Mock do parser
        mock_parser = MagicMock()
        mock_parser.parse.return_value = texto

        # Mock do embeddings
        mock_emb = MagicMock()
        mock_emb.embed_documents.return_value = [[0.1] * 768]

        # Mock do salvar_chunk
        saved_chunks = []
        with patch("src.rag.ingestion.pipeline.salvar_chunk", side_effect=lambda **kw: saved_chunks.append(kw)):
            from src.rag.ingestion.pipeline import IngestionPipeline
            pipeline = IngestionPipeline(
                parser=mock_parser,
                chunker=RecursiveChunker(chunk_size=500),
                embeddings=mock_emb,
            )
            result = pipeline.run(str(tmp_file), doc_type="calendario", label="CALENDÁRIO UEMA 2026")

        assert result.success
        assert result.chunks_saved > 0
        # Verifica prefixo nos chunks salvos
        for chunk_data in saved_chunks:
            assert "[CALENDÁRIO UEMA 2026 | calendario]" in chunk_data["content"]

    def test_ingestao_txt_funciona(self, tmp_path):
        """Arquivos .txt devem ser ingeridos sem erros."""
        from src.rag.ingestion.chunker_factory import RecursiveChunker
        from src.infrastructure.adapters.parsers.txt_adapter import TxtAdapter

        conteudo = "Contato CTIC: ctic@uema.br | Ramal: 2020\nContato PROG: prog@uema.br"
        tmp_file = tmp_path / "contatos.txt"
        tmp_file.write_text(conteudo, encoding="utf-8")

        parser = TxtAdapter()
        texto = parser.parse(str(tmp_file))
        assert "ctic@uema.br" in texto

    def test_txt_adapter_detecta_encoding_latin1(self, tmp_path):
        from src.infrastructure.adapters.parsers.txt_adapter import TxtAdapter

        conteudo = "Matrícula com acentuação especial"
        tmp_file = tmp_path / "latin1.txt"
        tmp_file.write_bytes(conteudo.encode("latin-1"))

        parser = TxtAdapter()
        texto = parser.parse(str(tmp_file))
        assert len(texto) > 0  # deve ler sem crash

    def test_markdown_chunker_preserva_hierarquia(self):
        """MarkdownHeaderChunker deve criar chunks com contexto de headers."""
        from src.rag.ingestion.chunker_factory import MarkdownHeaderChunker

        texto = """# Calendário 2026
## Semestre 2026.1
### Matrícula Veteranos
Data: 03/02/2026 a 07/02/2026
### Início das Aulas
Data: 10/02/2026"""

        chunker = MarkdownHeaderChunker(chunk_size=200, overlap=20)
        chunks = chunker.chunk(texto, source="cal.pdf", doc_type="calendario")
        assert len(chunks) >= 2
        # Verifica que o contexto do semestre aparece nos chunks de sub-seção
        textos = " ".join(c.text for c in chunks)
        assert "Semestre 2026.1" in textos or "2026.1" in textos


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIA 2: Avaliação de Resposta (sem LLM real)
# Verifica as propriedades estruturais das respostas
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestResponseQuality:
    """Testa propriedades de qualidade das respostas (sem LLM real)."""

    def test_output_validator_rejeita_resposta_vazia(self):
        from src.application.use_cases.output_validator import validar
        from src.agent.state import AgentState
        from src.domain.entities import Rota

        state = AgentState(user_id="u", session_id="s", mensagem_original="?", rota=Rota.GERAL)
        r = validar(state, "")
        assert r.valido is False

    def test_output_validator_rejeita_erro_langchain(self):
        from src.application.use_cases.output_validator import validar
        from src.agent.state import AgentState
        from src.domain.entities import Rota

        state = AgentState(user_id="u", session_id="s", mensagem_original="?", rota=Rota.GERAL)
        r = validar(state, "Agent stopped due to max iterations.")
        assert r.valido is False

    def test_output_validator_aceita_resposta_valida(self):
        from src.application.use_cases.output_validator import validar
        from src.agent.state import AgentState
        from src.domain.entities import Rota

        state = AgentState(user_id="u", session_id="s", mensagem_original="?", rota=Rota.CALENDARIO)
        r = validar(state, "A matrícula de veteranos ocorre de 03/02 a 07/02/2026.")
        assert r.valido is True

    def test_crag_score_baixo_gera_disclaimer(self):
        """Quando CRAG detecta contexto fraco, deve adicionar disclaimer."""
        from src.agent.core import _crag_avaliar_e_corrigir
        from src.rag.hybrid_retriever import ResultadoRecuperacao, ChunkRecuperado
        from src.rag.query.protocols import RawQuery
        from src.rag.query.transformer import QueryTransformer
        from src.domain.entities import Rota

        # Cria recuperacao com score muito baixo
        chunk_fraco = ChunkRecuperado(
            content="conteúdo genérico",
            source="calendario.pdf",
            doc_type="calendario",
            chunk_index=0,
            rrf_score=0.005,  # abaixo do mínimo
        )
        recuperacao = ResultadoRecuperacao(
            chunks=[chunk_fraco],
            contexto_formatado="conteúdo genérico",
            encontrou=True,
            metodo_usado="hibrido",
        )
        qt_raw = RawQuery(text="teste")
        from src.rag.query.protocols import TransformedQuery
        qt = TransformedQuery(original="teste", primary="teste")

        _, score, disclaimer = _crag_avaliar_e_corrigir(recuperacao, qt, Rota.CALENDARIO)
        assert disclaimer  # deve ter disclaimer quando score baixo


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIA 3: Comportamento da LLM / Guardrails
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestLLMBehavior:
    """Testa o comportamento dos guardrails e decisões do agente."""

    def test_guardrails_bloqueia_saudacao(self):
        from src.agent.core import _guardrails

        assert _guardrails("oi") is not None
        assert _guardrails("olá") is not None
        assert _guardrails("bom dia") is not None
        assert _guardrails("obrigado!") is not None

    def test_guardrails_nao_bloqueia_pergunta_substantiva(self):
        from src.agent.core import _guardrails

        assert _guardrails("quando é a matrícula de veteranos?") is None
        assert _guardrails("qual o email da PROG?") is None
        assert _guardrails("vagas BR-PPI engenharia civil") is None

    def test_guardrails_bloqueia_fora_dominio(self):
        from src.agent.core import _guardrails

        result = _guardrails("me recomenda um filme para assistir")
        assert result is not None  # deve ser bloqueado

        result = _guardrails("qual o placar do jogo de futebol?")
        assert result is not None

    def test_guardrails_nao_bloqueia_uema_com_keyword_fora_dominio(self):
        """'redação do PAES' contém 'redação' mas deve passar porque 'PAES' é UEMA."""
        from src.agent.core import _guardrails

        result = _guardrails("como é a redação do PAES?")
        assert result is None  # PAES é keyword UEMA → não bloqueia

    def test_self_rag_skip_mensagem_curta_conversacional(self):
        from src.agent.core import _decidir_precisa_rag
        from src.domain.entities import Rota
        from src.memory.working_memory import _historico_vazio

        h = _historico_vazio()
        # "ok" é conversacional curto → não precisa RAG
        assert _decidir_precisa_rag("ok", Rota.GERAL, h) is False
        assert _decidir_precisa_rag("obrigado", Rota.GERAL, h) is False

    def test_self_rag_usa_rag_para_rota_especifica(self):
        from src.agent.core import _decidir_precisa_rag
        from src.domain.entities import Rota
        from src.memory.working_memory import _historico_vazio

        h = _historico_vazio()
        # Rota específica sempre precisa RAG
        assert _decidir_precisa_rag("ok", Rota.CALENDARIO, h) is True
        assert _decidir_precisa_rag("quando?", Rota.EDITAL, h) is True

    def test_self_rag_usa_rag_com_keyword_uema(self):
        from src.agent.core import _decidir_precisa_rag
        from src.domain.entities import Rota
        from src.memory.working_memory import _historico_vazio

        h = _historico_vazio()
        assert _decidir_precisa_rag("matrícula quando?", Rota.GERAL, h) is True
        assert _decidir_precisa_rag("email CTIC suporte", Rota.GERAL, h) is True

    def test_step_back_remove_especificidade(self):
        """O step-back deve generalizar queries específicas."""
        from src.agent.core import _gerar_query_stepback

        q = "matrícula Engenharia Civil noturno 2026.1"
        step = _gerar_query_stepback(q)
        assert "2026.1" not in step
        assert step  # não pode ficar vazio


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIA 4: Teste de Semanticidade (Roteamento Semântico)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSemanticRouting:
    """Testa o roteamento semântico via router.py (regex fallback)."""

    @pytest.mark.parametrize("texto,rota_esperada", [
        ("quando é a matrícula de veteranos?", "CALENDARIO"),
        ("início das aulas semestre 2026.1", "CALENDARIO"),
        ("prazo para trancamento de matrícula", "CALENDARIO"),
        ("quantas vagas tem o PAES 2026?", "EDITAL"),
        ("o que é a cota BR-PPI?", "EDITAL"),
        ("documentos para inscrição PAES", "EDITAL"),
        ("email da coordenação de informática", "CONTATOS"),
        ("telefone PROG pró-reitoria", "CONTATOS"),
        ("contato CTIC suporte TI", "CONTATOS"),
    ])
    def test_router_regex_mapeia_corretamente(self, texto, rota_esperada):
        """O router regex deve mapear textos conhecidos para rotas corretas."""
        from src.application.use_cases.router import analisar
        from src.domain.entities import EstadoMenu

        rota = analisar(texto, EstadoMenu.MAIN)
        assert rota.value == rota_esperada, (
            f"'{texto}' mapeou para '{rota.value}', esperado '{rota_esperada}'"
        )

    @pytest.mark.parametrize("texto", [
        "oi tudo bem?",
        "obrigado",
        "me faz uma redação sobre o clima",
        "qual a capital da França?",
    ])
    def test_router_retorna_geral_para_fora_dominio(self, texto):
        from src.application.use_cases.router import analisar
        from src.domain.entities import EstadoMenu, Rota

        rota = analisar(texto, EstadoMenu.MAIN)
        assert rota == Rota.GERAL

    def test_router_estado_submenu_forca_rota(self):
        """Estado de submenu ativo deve forçar a rota independente do texto."""
        from src.application.use_cases.router import analisar
        from src.domain.entities import EstadoMenu, Rota

        # No submenu EDITAL, qualquer texto → EDITAL
        rota = analisar("quando é a matrícula?", EstadoMenu.SUB_EDITAL)
        assert rota == Rota.EDITAL

    def test_router_edital_tem_prioridade_sobre_calendario(self):
        """'data de inscrição do PAES' deve ir para EDITAL, não CALENDARIO."""
        from src.application.use_cases.router import analisar
        from src.domain.entities import EstadoMenu, Rota

        rota = analisar("data de inscrição do PAES 2026", EstadoMenu.MAIN)
        assert rota == Rota.EDITAL  # PAES é edital, não calendário genérico


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIA 5: Testes com LLM real (marcados como @pytest.mark.llm)
# Requerem GEMINI_API_KEY real configurada
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.slow
class TestLLMRealBehavior:
    """
    Testes com chamadas reais ao Gemini.
    Execute: pytest tests/eval/ -v -m llm

    IMPORTANTE: Estes testes custam tokens. Use com moderação.
    """

    def test_hyde_gera_documento_relevante(self):
        """HyDE deve gerar documento hipotético sobre UEMA."""
        import os
        if not os.environ.get("GEMINI_API_KEY") or "test-key" in os.environ.get("GEMINI_API_KEY", ""):
            pytest.skip("GEMINI_API_KEY real não configurada")

        from src.providers.gemini_provider import get_gemini_client
        from src.rag.query.strategies import HyDEStrategy

        # Cria provider real mínimo
        class SimpleProvider:
            def gerar_resposta_sincrono(self, prompt, **kwargs):
                from src.providers.gemini_provider import chamar_gemini
                return chamar_gemini(prompt=prompt, **kwargs)

        s = HyDEStrategy(llm_provider=SimpleProvider())
        from src.rag.query.protocols import RawQuery
        q = RawQuery(text="quando é a matrícula de veteranos da UEMA?")
        r = s.transform(q)

        assert r.was_transformed
        assert r.hypothetical_doc
        assert "matrícula" in r.hypothetical_doc.lower() or "UEMA" in r.hypothetical_doc

    def test_gemini_nao_alucina_datas_sem_contexto(self):
        """Sem contexto RAG, Gemini deve dizer que não tem informação."""
        import os
        if not os.environ.get("GEMINI_API_KEY") or "test-key" in os.environ.get("GEMINI_API_KEY", ""):
            pytest.skip("GEMINI_API_KEY real não configurada")

        from src.providers.gemini_provider import chamar_gemini
        from src.application.graph.prompts import SYSTEM_UEMA, montar_prompt_geracao

        prompt = montar_prompt_geracao(
            pergunta="qual é a data exata da matrícula de veteranos 2026?",
            contexto_rag="",  # SEM contexto RAG
        )
        resp = chamar_gemini(prompt=prompt, system_instruction=SYSTEM_UEMA)

        assert resp.sucesso
        # Com sistema configurado corretamente, deve indicar que não tem info
        conteudo_lower = resp.conteudo.lower()
        has_uncertainty = any(t in conteudo_lower for t in [
            "não encontr", "não tenho", "não possuo", "sem informação",
            "consulte", "verificar", "uema.br", "calendário",
        ])
        assert has_uncertainty, f"LLM não indicou incerteza. Resposta: {resp.conteudo[:200]}"