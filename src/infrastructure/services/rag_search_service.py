"""
src/infrastructure/services/rag_search_service.py  (SUBSTITUIÇÃO COMPLETA)
---------------------------------------------------------------------------
SERVICE PURO de busca RAG — sem Celery, sem HTTP, sem estado global.
Workers apenas instanciam este service e chamam .buscar().

PIPELINE:
  query original
    → QueryTransformService.transformar()   (LLM leve, opcional)
    → busca_hibrida() KNN + BM25            (Redis, CPU-only)
    → rerank local                          (cross-encoder CPU)
    → ToolResult

PRINCÍPIO CPU-ONLY:
  Embeddings → Gemini API (nuvem)
  LLM query transform → Gemini Flash (nuvem, barato)
  Cross-encoder rerank → CPU local (modelo leve ~90MB)
  Redis KNN + BM25 → CPU local

REGISTRAR NO METADATA:
  Após ingestão bem-sucedida, registra chunk no Postgres via DocumentChunkRepository.
  Workers chamam .registrar_chunks_postgres() ao final da ingestão.
"""
from __future__ import annotations

import asyncio
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ToolResult (mantido compatível com código existente)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    ok: bool
    message: str = ""
    data: dict = field(default_factory=dict)
    error: str = ""

    @classmethod
    def success(cls, message: str, data: dict | None = None) -> "ToolResult":
        return cls(ok=True, message=message, data=data or {})

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(ok=False, error=error)

    def to_agent_str(self) -> str:
        return self.message if self.ok else f"[ERRO] {self.error}"


# ─────────────────────────────────────────────────────────────────────────────
# QueryTransformService — reescreve a query antes da busca
# ─────────────────────────────────────────────────────────────────────────────

class QueryTransformService:
    """
    Transforma a query do usuário para melhorar o recall.

    ESTRATÉGIA POR CUSTO:
      1. Heurística local (0 tokens): enriquece termos UEMA, resolve pronomes
      2. Gemini Flash (só se query curta/vaga e rota == GERAL):
         ~30 tokens por chamada

    NÃO usa HyDE aqui (muito caro para produção).
    HyDE fica disponível via flag para queries complexas no futuro.
    """

    _SINONIMOS = {
        "matricula":   ["rematricula", "inscricao semestral"],
        "trancamento": ["cancelamento disciplina", "trancar materia"],
        "cotas":       ["br-ppi", "br-q", "pcd", "reserva de vagas"],
        "suporte":     ["ctic", "helpdesk", "chamado ti"],
        "calendario":  ["datas letivas", "prazo academico", "semestre"],
    }

    def transformar_local(
        self,
        query: str,
        fatos: list[str] | None = None,
    ) -> str:
        """Enriquecimento local sem LLM."""
        norm = _normalizar(query)
        extras: list[str] = []

        for termo, sinonimos in self._SINONIMOS.items():
            if termo in norm:
                extras.extend(sinonimos[:1])

        # Injeta fato mais relevante do usuário
        if fatos:
            extras.append(fatos[0][:60])

        partes = [query] + extras
        return " ".join(partes)[:280]

    async def transformar_com_flash(
        self,
        query: str,
        rota: str,
        historico: str = "",
    ) -> str:
        """
        Reescrita via Gemini Flash — só ativa para queries vagas em rota GERAL
        ou queries com pronomes que precisam de resolução de referência.
        """
        # Heurística: vale chamar o Flash?
        q_lower = query.lower()
        tem_pronome = any(p in q_lower for p in ["isso", "ele", "ela", "aquilo", "esse"])
        e_vaga = len(query.split()) <= 4
        if not (tem_pronome or (e_vaga and rota == "GERAL")):
            return query

        try:
            from src.infrastructure.settings import settings
            import google.genai as genai
            from google.genai import types

            historico_trecho = historico[-200:] if historico else ""
            prompt = (
                f"Histórico:\n{historico_trecho}\n\n"
                f"Reescreva como query técnica para busca em documentos da UEMA "
                f"(máx 15 palavras, sem pronomes, sem artigos):\n{query}"
            )
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=50,
                ),
            )
            reescrita = (response.text or "").strip()
            if len(reescrita) >= 5:
                logger.debug("🔄 Query transform: '%s' → '%s'", query[:50], reescrita[:50])
                return reescrita
        except Exception as e:
            logger.debug("QueryTransform Flash falhou: %s", e)
        return query


# ─────────────────────────────────────────────────────────────────────────────
# RAGSearchService principal
# ─────────────────────────────────────────────────────────────────────────────

class RAGSearchService:
    """
    Service de busca RAG. Injetado nos workers e nas tools de domínio.

    USO NOS WORKERS:
        svc = RAGSearchService()
        result = await svc.buscar("matrícula veteranos", doc_type="calendario")
        chunks = result.data.get("chunks", [])
    """

    def __init__(
        self,
        embedding_model: Any = None,
        query_transform: QueryTransformService | None = None,
        use_rerank: bool = True,
    ):
        self._emb = embedding_model
        self._qt = query_transform or QueryTransformService()
        self._use_rerank = use_rerank

    async def buscar(
        self,
        query: str,
        doc_type: str = "geral",
        source_filter: str | None = None,
        k_vector: int = 6,
        k_text: int = 8,
        rota: str = "GERAL",
        fatos: list[str] | None = None,
        historico: str = "",
    ) -> ToolResult:
        """
        Busca híbrida completa com query transform e rerank.

        Returns:
          ToolResult.message = contexto formatado para o LLM
          ToolResult.data    = {"chunks": [...], "found": bool, ...}
        """
        try:
            # 1. Query Transform (heurística local, sempre)
            query_enriquecida = self._qt.transformar_local(query, fatos)

            # 2. Query Transform (Flash, só se vaga/pronome)
            query_final = await self._qt.transformar_com_flash(
                query_enriquecida, rota, historico
            )
            query_norm = _normalizar(query_final)

            # 3. Embedding (Gemini API — nuvem, CPU-friendly)
            emb = self._get_embeddings()
            vetor = await asyncio.to_thread(emb.embed_query, query_norm)

            # 4. Busca híbrida KNN + BM25
            from src.infrastructure.redis_client import busca_hibrida
            resultados = await asyncio.to_thread(
                busca_hibrida,
                query_text=query_norm,
                query_embedding=vetor,
                source_filter=source_filter,
                k_vector=k_vector,
                k_text=k_text,
            )

            if not resultados:
                return ToolResult.success(
                    message=f"Não encontrei informações sobre isso nos documentos da UEMA.",
                    data={"chunks": [], "found": False, "doc_type": doc_type},
                )

            # 5. Filtro por doc_type (quando não há source_filter específico)
            if doc_type and doc_type != "geral" and not source_filter:
                filtrados = [r for r in resultados if r.get("doc_type") == doc_type]
                resultados = filtrados if filtrados else resultados

            # 6. Rerank local (cross-encoder CPU, modelo leve)
            if self._use_rerank and len(resultados) > 1:
                resultados = await self._rerank(query, resultados, top_k=5)
            else:
                resultados = resultados[:5]

            # 7. Formata contexto para o LLM com título real (não hash)
            contexto = self._formatar_contexto(resultados)
            top_score = resultados[0].get("rerank_score", resultados[0].get("rrf_score", 0))

            return ToolResult.success(
                message=contexto,
                data={
                    "chunks":     resultados,
                    "found":      True,
                    "doc_type":   doc_type,
                    "query_used": query_final,
                    "top_score":  round(top_score, 3),
                    "count":      len(resultados),
                },
            )

        except Exception as e:
            logger.exception("❌ [RAGSearchService] buscar falhou: %s", e)
            return ToolResult.failure(f"Erro técnico na busca: {str(e)[:100]}")

    def _formatar_contexto(self, chunks: list[dict], max_chars: int = 2000) -> str:
        """
        Formata chunks com título REAL do documento (do Postgres via metadata).
        O LLM vê "Calendário Acadêmico UEMA 2026", não "abc123def456".
        """
        por_source: dict[str, list[dict]] = {}
        for chunk in chunks:
            src = chunk.get("source", "Documento")
            por_source.setdefault(src, []).append(chunk)

        blocos = []
        total_chars = 0
        for source, source_chunks in por_source.items():
            # Usa o label do chunk se disponível (anti-alucinação prefix)
            primeiro = source_chunks[0]
            label = primeiro.get("label") or _source_para_titulo(source)
            cabecalho = f"━━━ {label} ━━━"
            conteudos = "\n\n".join(c.get("content", "").strip() for c in source_chunks)

            bloco = f"{cabecalho}\n{conteudos}"
            if total_chars + len(bloco) > max_chars:
                break
            blocos.append(bloco)
            total_chars += len(bloco)

        return "\n\n".join(blocos)

    async def _rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = 5,
    ) -> list[dict]:
        """Cross-encoder local (CPU). Modelo leve ~90MB."""
        try:
            from src.application.chain.reranker import rerank
            return await rerank(query, chunks, top_k=top_k)
        except Exception:
            return chunks[:top_k]

    def _get_embeddings(self) -> Any:
        if self._emb is None:
            from src.rag.embeddings import get_embeddings
            self._emb = get_embeddings()
        return self._emb


# ─────────────────────────────────────────────────────────────────────────────
# DocumentChunkRepository — registra metadados no Postgres
# ─────────────────────────────────────────────────────────────────────────────

class DocumentChunkRepository:
    """
    Registra metadados dos chunks no Postgres após ingestão.
    Garante rastreabilidade: LLM vê título real, não hash.
    """

    async def registrar_batch(
        self,
        chunks_info: list[dict],
        db_session: Any = None,
    ) -> int:
        """
        Registra múltiplos chunks no Postgres.

        chunks_info: lista de dicts com:
          chunk_id, source, titulo, doc_type, chunk_index,
          chars, parser_usado, chunker_usado, label
        """
        if db_session is None:
            from src.infrastructure.database.session import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                return await self._inserir_batch(chunks_info, session)
        return await self._inserir_batch(chunks_info, db_session)

    async def _inserir_batch(self, chunks_info: list[dict], session: Any) -> int:
        from sqlalchemy import text

        salvos = 0
        for info in chunks_info:
            try:
                await session.execute(
                    text("""
                        INSERT INTO document_chunks
                            (chunk_id, source, titulo, doc_type, chunk_index,
                             chars, parser_usado, chunker_usado, label)
                        VALUES
                            (:chunk_id, :source, :titulo, :doc_type, :chunk_index,
                             :chars, :parser, :chunker, :label)
                        ON CONFLICT (chunk_id) DO UPDATE SET
                            titulo = EXCLUDED.titulo,
                            indexado_em = NOW()
                    """),
                    {
                        "chunk_id":    info.get("chunk_id", "")[:16],
                        "source":      info.get("source", "")[:300],
                        "titulo":      info.get("titulo", "")[:500],
                        "doc_type":    info.get("doc_type", "geral")[:50],
                        "chunk_index": info.get("chunk_index", 0),
                        "chars":       info.get("chars"),
                        "parser":      info.get("parser_usado", "")[:50],
                        "chunker":     info.get("chunker_usado", "")[:50],
                        "label":       info.get("label", "")[:300],
                    },
                )
                salvos += 1
            except Exception as e:
                logger.warning("⚠️  DocumentChunkRepository: %s", e)
        await session.commit()
        return salvos

    async def listar_fontes(self, session: Any = None) -> list[dict]:
        """Lista todas as fontes indexadas para o painel admin."""
        from sqlalchemy import text
        if session is None:
            from src.infrastructure.database.session import AsyncSessionLocal
            async with AsyncSessionLocal() as s:
                return await self._query_fontes(s)
        return await self._query_fontes(session)

    async def _query_fontes(self, session: Any) -> list[dict]:
        from sqlalchemy import text
        result = await session.execute(text("""
            SELECT source, titulo, doc_type,
                   COUNT(*) as total_chunks,
                   SUM(chars) as total_chars,
                   MAX(indexado_em) as ultima_indexacao
            FROM document_chunks
            GROUP BY source, titulo, doc_type
            ORDER BY ultima_indexacao DESC
        """))
        return [dict(r._mapping) for r in result.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Adapters de domínio (compatibilidade com tool_registry.py existente)
# ─────────────────────────────────────────────────────────────────────────────

class CalendarioService:
    def __init__(self, rag: RAGSearchService): self._rag = rag
    async def consultar(self, query: str) -> ToolResult:
        return await self._rag.buscar(query, doc_type="calendario", k_vector=8, k_text=10, rota="CALENDARIO")

class EditalService:
    def __init__(self, rag: RAGSearchService): self._rag = rag
    async def consultar(self, query: str) -> ToolResult:
        return await self._rag.buscar(query, doc_type="edital", k_vector=6, k_text=10, rota="EDITAL")

class ContatosService:
    def __init__(self, rag: RAGSearchService): self._rag = rag
    async def consultar(self, query: str) -> ToolResult:
        return await self._rag.buscar(query, doc_type="contatos", k_vector=7, k_text=5, rota="CONTATOS")

class WikiCTICService:
    def __init__(self, rag: RAGSearchService): self._rag = rag
    async def consultar(self, query: str) -> ToolResult:
        return await self._rag.buscar(query, doc_type="wiki_ctic", k_vector=5, k_text=6, rota="WIKI")


# ─────────────────────────────────────────────────────────────────────────────
# Utilitários
# ─────────────────────────────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    s = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return s.lower().strip()


_SOURCE_TITULOS = {
    "calendario-academico-2026.pdf": "Calendário Acadêmico UEMA 2026",
    "edital_paes_2026.pdf":          "Edital PAES 2026",
    "guia_contatos_2025.pdf":        "Guia de Contatos UEMA 2025",
}


def _source_para_titulo(source: str) -> str:
    return _SOURCE_TITULOS.get(source, source.replace("-", " ").replace("_", " ").title())