"""
src/agents/academic_knowledge/service.py
===========================================
Ex `infrastructure/services/rag_search_service.py` (Fase 4 do
PLANO_REFATORACAO_SUPERVISOR.md, seção 2.4). `RAGSearchService.buscar()`
mantém o corpo idêntico ao original (decide query rewrite → busca paralela
→ RRF → dedup → fallback step-back → rerank → formata contexto) — só o
rerank passou a vir de `capabilities/rag/reranker.py` em vez de
`application/chain/reranker.py` (ambos coexistem: o segundo agora é um shim).

`AcademicKnowledgeAgent` é o esqueleto mínimo do `BaseAgent` (ver
agents/base.py e agents/registry.py, Fase 2) — registrado no Agent Registry,
mas AINDA NÃO é o caminho quente de produção: o pipeline vivo despacha
`RAGSearchService` e `SynthesisService` via workers Celery separados
(worker_rag_search_task, worker_synthesis_task) para permitir paralelismo via
chord, não via `agent.execute(context)` direto. `AcademicKnowledgeAgent`
existe para uso futuro (ex.: um modo de chamada direta sem Celery), sem
mudar o fluxo de despacho atual.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from src.agents.base import AgentEnabledMixin
from src.agents.academic_knowledge.query_transform import QueryTransformService
from src.agents.academic_knowledge.synthesis import SynthesisService

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
# RAGSearchService principal
# ─────────────────────────────────────────────────────────────────────────────

class RAGSearchService:
    """
    Serviço central de busca RAG. Injetado nos workers e nas tools de domínio.
    Gerencia transformação de queries, busca assíncrona paralela, fusão via RRF,
    deduplicação de conteúdo textual e fallback automático via Step-Back.
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

    async def _obter_embedding_async(self, q: str) -> list[float]:
        """Gera embedding assincronamente sem bloquear o event loop principal."""
        emb = self._get_embeddings()
        return await asyncio.to_thread(emb.embed_query, _normalizar(q))

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
        metadata_filter: dict | None = None,
    ) -> ToolResult:
        """
        Busca híbrida avançada utilizando expansão paralela, fusão RRF e pipeline de fallback.

        Fluxo de Execução:
          1. Expansão de queries (Proper Nouns + local keyword enrichment).
          2. Geração assíncrona concorrente dos embeddings necessários.
          3. Execução paralela de buscas híbridas no Redis para todas as queries.
          4. Fusão de rankings usando Reciprocal Rank Fusion (RRF) na memória.
          5. Deduplicação por conteúdo textual (fingerprint de 100 caracteres).
          6. Fallback dinâmico para a query Step-Back se os resultados forem nulos.
          7. Filtragem opcional por tipo de documento (doc_type).
          8. Rerank local via Cross-Encoder (CPU) e formatação final.
        """
        try:
            # 1. Transformação e expansão de query
            transformed = await self._qt.transformar(query, rota, fatos, historico)
            queries_to_search = transformed.all_queries

            # 2. Geração paralela de embeddings
            emb_tasks = [self._obter_embedding_async(q) for q in queries_to_search]
            embeddings_list = await asyncio.gather(*emb_tasks)

            # 3. Execução paralela de buscas híbridas no Redis
            from src.infrastructure.redis_client import busca_hibrida

            search_tasks = []
            for i, q in enumerate(queries_to_search):
                kv = k_vector // 2 if i > 0 else k_vector
                kt = k_text // 2 if i > 0 else k_text

                # Garante que as sub-queries não tenham k excessivamente baixo
                kv = max(kv, 3)
                kt = max(kt, 3)

                search_tasks.append(
                    asyncio.to_thread(
                        busca_hibrida,
                        query_text=_normalizar(q),
                        query_embedding=embeddings_list[i],
                        source_filter=source_filter,
                        k_vector=kv,
                        k_text=kt,
                        metadata_filter=metadata_filter,
                    )
                )

            resultados_listas = await asyncio.gather(*search_tasks)

            # 4. Fusão RRF (Reciprocal Rank Fusion) para combinar rankings paralelos
            rrf_const = 60
            fused_scores = {}
            docs_map = {}
            for lista in resultados_listas:
                for rank, doc in enumerate(lista, start=1):
                    doc_id = doc["id"]
                    fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + 1.0 / (rrf_const + rank)
                    # Mantém o documento com o melhor score original
                    if doc_id not in docs_map or doc.get("rrf_score", 0) > docs_map[doc_id].get("rrf_score", 0):
                        docs_map[doc_id] = doc

            fused_results = []
            for doc_id, score in fused_scores.items():
                doc = docs_map[doc_id].copy()
                doc["rrf_score"] = score
                fused_results.append(doc)

            # 5. Deduplicação por conteúdo textual (evita chunks duplicados)
            vistos = {}
            for doc in fused_results:
                fingerprint = doc.get("content", "").strip().lower()
                if fingerprint not in vistos or doc["rrf_score"] > vistos[fingerprint]["rrf_score"]:
                    vistos[fingerprint] = doc

            resultados = sorted(vistos.values(), key=lambda d: d["rrf_score"], reverse=True)
            metodo_busca = "multi_hibrido" if len(queries_to_search) > 1 else "hibrido"

            # 6. Fallback dinâmico via Step-Back se busca principal retornar vazia
            if not resultados and transformed.step_back:
                logger.info("⚠️ RAG busca vazia. Acionando Step-Back Fallback: '%s'", transformed.step_back)
                sb_embedding = await self._obter_embedding_async(transformed.step_back)
                sb_resultados = await asyncio.to_thread(
                    busca_hibrida,
                    query_text=_normalizar(transformed.step_back),
                    query_embedding=sb_embedding,
                    source_filter=source_filter,
                    k_vector=k_vector,
                    k_text=k_text,
                    metadata_filter=metadata_filter,
                )

                # Deduplica e ordena os resultados do step-back
                vistos_sb = {}
                for doc in sb_resultados:
                    fingerprint = doc.get("content", "").strip().lower()
                    if fingerprint not in vistos_sb or doc.get("rrf_score", 0) > vistos_sb[fingerprint].get("rrf_score", 0):
                        vistos_sb[fingerprint] = doc
                resultados = sorted(vistos_sb.values(), key=lambda d: d.get("rrf_score", 0), reverse=True)
                metodo_busca = "step_back_fallback"

            if not resultados:
                return ToolResult.success(
                    message=f"Não encontrei informações sobre isso nos documentos da UEMA.",
                    data={"chunks": [], "found": False, "doc_type": doc_type},
                )

            # 7. Filtragem por doc_type (se aplicável)
            if doc_type and doc_type != "geral" and not source_filter:
                filtrados = [r for r in resultados if r.get("doc_type") == doc_type]
                resultados = filtrados if filtrados else resultados

            # 8. Rerank local via Cross-Encoder (CPU)
            if self._use_rerank and len(resultados) > 1:
                resultados = await self._rerank(query, resultados, top_k=5)
            else:
                resultados = resultados[:5]

            # 9. Formatação final do contexto
            contexto = self._formatar_contexto(resultados)
            top_score = resultados[0].get("rerank_score", resultados[0].get("rrf_score", 0))

            return ToolResult.success(
                message=contexto,
                data={
                    "chunks":     resultados,
                    "found":      True,
                    "doc_type":   doc_type,
                    "query_used": transformed.primary,
                    "top_score":  round(top_score, 3),
                    "count":      len(resultados),
                    "metodo":     metodo_busca,
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
            from src.capabilities.rag.reranker import rerank
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
# Utilitários
# ─────────────────────────────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return s.lower().strip()


_SOURCE_TITULOS = {
    "calendario-academico-2026.pdf": "Calendário Acadêmico UEMA 2026",
    "edital_paes_2026.pdf":          "Edital PAES 2026",
    "guia_contatos_2025.pdf":        "Guia de Contatos UEMA 2025",
}


def _source_para_titulo(source: str) -> str:
    return _SOURCE_TITULOS.get(source, source.replace("-", " ").replace("_", " ").title())


# ─────────────────────────────────────────────────────────────────────────────
# AcademicKnowledgeAgent — esqueleto BaseAgent (ver docstring do módulo)
# ─────────────────────────────────────────────────────────────────────────────

class AcademicKnowledgeAgent(AgentEnabledMixin):
    name = "academic_knowledge"
    description = "Responde perguntas acadêmicas via RAG (calendário, editais, contatos, wiki)."
    permissions: list[str] = []

    def __init__(self) -> None:
        self._rag = RAGSearchService()
        self._synthesis = SynthesisService()

    async def execute(self, context):
        from src.agents.base import AgentResponse

        query = context.conversation.get("query", "") if context.conversation else ""
        rota = context.conversation.get("rota", "GERAL") if context.conversation else "GERAL"

        rag_result = await self._rag.buscar(query, rota=rota)
        if not rag_result.ok or not rag_result.data.get("found"):
            return AgentResponse(answer=rag_result.message or "Não encontrei informações sobre isso.")

        synth_result = await self._synthesis.sintetizar(
            chunks=rag_result.data.get("chunks", []),
            plan_ctx={"query": query, "user_context": context.identity},
        )
        return AgentResponse(answer=synth_result.answer, status="error" if synth_result.error else "ok")
