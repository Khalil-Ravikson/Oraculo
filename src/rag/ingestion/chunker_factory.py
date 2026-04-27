"""
src/rag/ingestion/chunker_factory.py
--------------------------------------
Factory de estratégias de chunking.

ESTRATÉGIAS DISPONÍVEIS:
  recursive  → RecursiveCharacterTextSplitter (LangChain)
               Divide por parágrafos → frases → chars. Melhor geral.

  markdown   → MarkdownHeaderTextSplitter (LangChain)
               Divide por headers (# ## ###). Ideal para output do Docling/Marker.
               Preserva contexto hierárquico.

  semantic   → Semantic Chunking via embeddings
               Detecta breakpoints semânticos. Chunks variam de tamanho.
               Mais caro (1 embedding por sentença) mas chunks muito mais coerentes.

  token      → TokenTextSplitter (LangChain)
               Divide por tokens (não chars). Mais preciso para modelos com
               limite de context window.

COMO ADICIONAR:
  1. Criar função _build_meu_chunker() retornando IChunkerStrategy
  2. Registrar em _REGISTRY
  3. Zero mudanças em outros arquivos.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Protocolo
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChunkResult:
    text: str
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class IChunkerStrategy(Protocol):
    @property
    def name(self) -> str: ...
    def chunk(self, text: str, source: str, doc_type: str) -> list[ChunkResult]: ...


# ─────────────────────────────────────────────────────────────────────────────
# Estratégias
# ─────────────────────────────────────────────────────────────────────────────

class RecursiveChunker:
    """RecursiveCharacterTextSplitter — melhor geral para texto corrido."""

    def __init__(self, chunk_size: int = 400, overlap: int = 60):
        self._size = chunk_size
        self._overlap = overlap

    @property
    def name(self) -> str:
        return "recursive"

    def chunk(self, text: str, source: str, doc_type: str) -> list[ChunkResult]:
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
        except ImportError:
            from langchain.text_splitter import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._size,
            chunk_overlap=self._overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
            length_function=len,
        )
        docs = splitter.create_documents([text])
        return [
            ChunkResult(
                text=d.page_content,
                metadata={"chunk_index": i, "source": source, "doc_type": doc_type, "chunker": "recursive"},
            )
            for i, d in enumerate(docs)
        ]


class MarkdownHeaderChunker:
    """
    MarkdownHeaderTextSplitter — divide por hierarquia de headers.

    IDEAL para:
      - Output do Docling (converte tabelas em Markdown com headers)
      - Output do Marker (preserva # ## ### do PDF)
      - Documentos com estrutura clara de seções

    Cada chunk preserva o contexto do header pai, ex:
      # Calendário UEMA 2026
      ## Semestre 2026.1
      ### Matrícula de Veteranos
      DATA: 03/02/2026 a 07/02/2026

    O chunk inclui "# Calendário UEMA 2026 > ## Semestre 2026.1 > ### Matrícula..."
    como metadata, o que melhora muito o retrieval.
    """

    def __init__(self, chunk_size: int = 400, overlap: int = 60):
        self._size = chunk_size
        self._overlap = overlap

    @property
    def name(self) -> str:
        return "markdown"

    def chunk(self, text: str, source: str, doc_type: str) -> list[ChunkResult]:
        try:
            from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
        except ImportError:
            from langchain.text_splitter import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

        headers_to_split_on = [
            ("#",   "h1"),
            ("##",  "h2"),
            ("###", "h3"),
            ("####","h4"),
        ]

        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
            strip_headers=False,  # mantém headers no texto do chunk
        )

        # Segunda fase: re-divide chunks muito grandes
        char_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._size,
            chunk_overlap=self._overlap,
        )

        try:
            md_docs = md_splitter.split_text(text)
        except Exception:
            # Se não tem Markdown válido, cai no RecursiveChunker
            logger.debug("⚠️  Texto sem headers Markdown, usando recursive fallback")
            return RecursiveChunker(self._size, self._overlap).chunk(text, source, doc_type)

        # Re-divide chunks muito grandes
        final_docs = char_splitter.split_documents(md_docs)

        results = []
        for i, doc in enumerate(final_docs):
            meta = {
                "chunk_index": i,
                "source": source,
                "doc_type": doc_type,
                "chunker": "markdown",
            }
            # Preserva hierarquia de headers como contexto
            header_ctx = " > ".join(
                v for k, v in sorted(doc.metadata.items())
                if k.startswith("h") and v
            )
            if header_ctx:
                meta["header_context"] = header_ctx

            results.append(ChunkResult(text=doc.page_content, metadata=meta))

        return results


class SemanticChunker:
    """
    Semantic Chunking via embeddings.

    ALGORITMO:
      1. Divide texto em sentenças
      2. Calcula embedding de cada sentença
      3. Detecta breakpoints onde a similaridade coseno cai abaixo de threshold
      4. Agrupa sentenças em chunks com base nesses breakpoints

    CUSTO: 1 embedding por sentença (~100 sentenças = 100 embeddings)
    QUANDO USAR: Documentos de conhecimento onde coerência semântica importa
    mais que tamanho uniforme (wikis, manuais, FAQ).

    NÃO USAR para: Calendários e editais com dados tabulares (prefira markdown).
    """

    def __init__(self, embeddings_model=None, breakpoint_threshold: float = 0.7):
        self._embeddings = embeddings_model
        self._threshold = breakpoint_threshold

    @property
    def name(self) -> str:
        return "semantic"

    def chunk(self, text: str, source: str, doc_type: str) -> list[ChunkResult]:
        if self._embeddings is None:
            logger.warning("⚠️  SemanticChunker sem modelo de embeddings, usando recursive")
            return RecursiveChunker().chunk(text, source, doc_type)

        try:
            from langchain_experimental.text_splitter import SemanticChunker
            splitter = SemanticChunker(
                embeddings=self._embeddings,
                breakpoint_threshold_type="percentile",
                breakpoint_threshold_amount=self._threshold * 100,
            )
            docs = splitter.create_documents([text])
            return [
                ChunkResult(
                    text=d.page_content,
                    metadata={"chunk_index": i, "source": source, "doc_type": doc_type, "chunker": "semantic"},
                )
                for i, d in enumerate(docs)
            ]
        except ImportError:
            logger.warning("⚠️  langchain-experimental não instalado. pip install langchain-experimental")
            return RecursiveChunker().chunk(text, source, doc_type)
        except Exception as e:
            logger.error("❌ SemanticChunker falhou: %s. Usando recursive.", e)
            return RecursiveChunker().chunk(text, source, doc_type)


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, callable] = {
    "recursive": lambda **kw: RecursiveChunker(**kw),
    "markdown":  lambda **kw: MarkdownHeaderChunker(**kw),
    "semantic":  lambda **kw: SemanticChunker(**kw),
}
if doc_type in ("edital",) and not embeddings:
    return ChunkerFactory.get("recursive", chunk_size=300, overlap=80)  # era parent_child

class ChunkerFactory:
    """
    Fábrica de estratégias de chunking.

    Uso:
        chunker = ChunkerFactory.get("markdown", chunk_size=400, overlap=60)
        chunks = chunker.chunk(texto, source="edital.pdf", doc_type="edital")

        # Ou automático baseado no parser usado:
        chunker = ChunkerFactory.for_parser("docling")
        chunks = chunker.chunk(texto, source="edital.pdf", doc_type="edital")
    """

    @staticmethod
    def get(strategy: str = "recursive", **kwargs) -> IChunkerStrategy:
        builder = _REGISTRY.get(strategy.lower())
        if builder is None:
            raise ValueError(f"Chunker '{strategy}' não encontrado. Disponíveis: {list(_REGISTRY.keys())}")
        return builder(**kwargs)

    @staticmethod
    def for_parser(parser_name: str, **kwargs) -> IChunkerStrategy:
        """
        Seleciona o melhor chunker para o parser usado.

        docling/marker → markdown (output é Markdown com headers)
        pymupdf/txt    → recursive (texto corrido)
        unstructured   → recursive (formato variável)
        """
        if parser_name in ("docling", "marker"):
            return ChunkerFactory.get("markdown", **kwargs)
        return ChunkerFactory.get("recursive", **kwargs)

    @staticmethod
    def for_doc_type(doc_type: str, embeddings=None, **kwargs) -> IChunkerStrategy:
        """
        Seleciona o melhor chunker para o tipo de documento.

        calendario/edital → markdown (estrutura hierárquica preservada)
        contatos          → recursive (dados tabulares simples)
        wiki_ctic         → semantic (documentação técnica)
        geral             → recursive
        """
        if doc_type in ("calendario", "edital"):
            return ChunkerFactory.get("markdown", **kwargs)
        if doc_type in ("wiki_ctic",) and embeddings:
            return ChunkerFactory.get("semantic", embeddings_model=embeddings, **kwargs)
        if doc_type in ("edital",) and not embeddings:
            return ChunkerFactory.get("parent_child", parent_size=1200, child_size=300)
        return ChunkerFactory.get("recursive", **kwargs)