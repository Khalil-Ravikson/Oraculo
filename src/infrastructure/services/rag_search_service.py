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
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TransformedQuery — guarda estado das queries transformadas
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TransformedQuery:
    original: str
    primary: str
    variants: list[str] = field(default_factory=list)
    step_back: str = ""
    keywords: list[str] = field(default_factory=list)
    strategy_used: str = "passthrough"
    was_transformed: bool = False

    @property
    def all_queries(self) -> list[str]:
        queries = [self.primary]
        queries.extend(v for v in self.variants if v and v != self.primary)
        return queries


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
    Transforma a query do usuário para melhorar o recall e precisão da busca RAG.
    
    ESTRATÉGIAS UTILIZADAS:
      1. ProperNounQueryStrategy: regex local que envolve nomes próprios em aspas
         para forçar exact matching via BM25/FTS no Redis.
      2. KeywordEnrich: enriquece com sinônimos do domínio UEMA e fatos do usuário.
      3. StepBackStrategy: gera uma query de generalização (fallback) removendo
         datas e especificações, permitindo busca ampla.
      4. Gemini Flash: reescrita contextual em caso de pronomes/queries vagas.
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
        """Enriquece a query localmente usando sinônimos do domínio e fatos do usuário."""
        norm = _normalizar(query)
        extras: list[str] = []

        for termo, sinonimos in self._SINONIMOS.items():
            if termo in norm:
                extras.extend(sinonimos[:1])

        # Injeta fato mais relevante do usuário se disponível
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
        """Reescrita contextual via Gemini Flash para queries vagas ou com pronomes."""
        q_lower = query.lower().strip()
        palavras = q_lower.split()
        
        # Flexibiliza a detecção de pronomes ou query vaga que precisa de contexto
        tem_pronome = any(p in q_lower for p in ["isso", "ele", "ela", "aquilo", "esse", "este", "esta", "onde", "quando", "como", "qual", "quais", "cade", "cadê"])
        e_vaga = len(palavras) <= 5
        
        if not (tem_pronome or (e_vaga and rota in ("GERAL", "CALENDARIO", "EDITAL", "WIKI"))):
            return query

        if not historico:
            return query

        try:
            from src.infrastructure.settings import settings
            import google.genai as genai
            from google.genai import types

            # Pega uma fatia generosa do histórico (até 1500 chars) para garantir contexto real
            historico_trecho = historico[-1500:]
            
            prompt = (
                f"<system_instruction>\n"
                f"Você é um especialista em reescrita de buscas para RAG da UEMA.\n"
                f"Analise o histórico recente da conversa e reescreva a última pergunta do usuário como uma query técnica direta, sem pronomes ou artigos, otimizada para pesquisa no banco de dados vetorial.\n"
                f"Responda APENAS com a query reescrita, sem markdown, sem explicações.\n"
                f"</system_instruction>\n\n"
                f"<historico>\n{historico_trecho}\n</historico>\n\n"
                f"<pergunta_usuario>{query}</pergunta_usuario>\n\n"
                f"Query Reescrita:"
            )
            
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=60,
                ),
            )
            reescrita = (response.text or "").strip()
            # Remove aspas se o modelo adicionar
            reescrita = reescrita.replace('"', '').replace("'", "")
            if len(reescrita) >= 3:
                logger.debug("🔄 Query transform: '%s' → '%s'", query[:50], reescrita[:50])
                return reescrita
        except Exception as e:
            logger.warning("⚠️ QueryTransform Flash falhou: %s", e)
        return query

    async def transformar(
        self,
        query: str,
        rota: str = "GERAL",
        fatos: list[str] | None = None,
        historico: str = "",
    ) -> TransformedQuery:
        """
        Gera a query transformada com variantes locais de busca exata e geral (Step-Back).
        """
        # 1. Identificar nomes próprios (ProperNounQueryStrategy)
        re_nome = re.compile(
            r'\b([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]+(?:\s+[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]+){1,4})\b'
        )
        re_titulos = re.compile(
            r'\b(Dr\.?|Dra\.?|Prof\.?|Profa\.?|Sr\.?|Sra\.?)\s+', re.I
        )

        nomes = re_nome.findall(query)
        nomes = [n for n in nomes if len(n.split()) >= 2]
        
        variants = []
        strategy = "passthrough"
        was_transformed = False
        keywords = []

        if nomes:
            nome_principal = max(nomes, key=len)
            nome_limpo = re_titulos.sub("", nome_principal).strip()
            variante_exata = f'"{nome_limpo}"'
            variante_sem_titulo = query.replace(nome_principal, nome_limpo)
            
            variants.append(variante_exata)
            if (variante_sem_titulo != query) and (variante_sem_titulo not in variants):
                variants.append(variante_sem_titulo)
            
            keywords.append(nome_limpo)
            strategy = "proper_noun"
            was_transformed = True

        # 2. Enriquecimento de palavras-chave UEMA
        query_enriquecida = self.transformar_local(query, fatos)
        if query_enriquecida != query:
            if strategy == "passthrough":
                strategy = "keyword_enrich"
            was_transformed = True
            primary = query_enriquecida
        else:
            primary = query

        # 3. Gerar query Step-Back (StepBackStrategy)
        texto_sb = query
        texto_sb = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "", texto_sb)
        texto_sb = re.sub(r"\b20\d{2}[\./]\d{1,2}\b", "", texto_sb)
        texto_sb = re.sub(r"\b(br-ppi|br-q|br-dc|ir-ppi|cfo-pp|pcd)\b", "cota", texto_sb, flags=re.I)
        texto_sb = re.sub(r"\b([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]{3,})\b", "", texto_sb)
        step_back = " ".join(texto_sb.split())
        if len(step_back) < 10:
            step_back = " ".join(query.split()[:3])

        # 4. Gemini Flash (reescrita externa opcional)
        query_llm = await self.transformar_com_flash(primary, rota, historico)
        if query_llm != primary:
            primary = query_llm
            strategy = "llm_transform"
            was_transformed = True

        return TransformedQuery(
            original=query,
            primary=primary,
            variants=variants,
            step_back=step_back,
            keywords=keywords,
            strategy_used=strategy,
            was_transformed=was_transformed,
        )


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