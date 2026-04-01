import logging
from dataclasses import dataclass, field
from typing import List

from src.domain.ports.vector_store_port import IVectorStorePort
# Ajuste o import abaixo se o seu query_transform estiver em outra pasta
from src.rag.query_transform import QueryTransformada, transformar_para_step_back

logger = logging.getLogger(__name__)

# Configurações de Domínio (Mantidas do seu código original)
_SOURCE_PARA_TITULO = {
    "calendario-academico-2026.pdf": "Calendário Acadêmico UEMA 2026",
    "edital_paes_2026.pdf": "Edital PAES 2026 — Processo Seletivo UEMA",
    "guia_contatos_2025.pdf": "Guia de Contatos UEMA 2025",
}
_DOC_TYPE_PARA_LABEL = {"calendario": "CALENDÁRIO ACADÊMICO", "edital": "EDITAL PAES 2026", "contatos": "CONTATOS UEMA"}
_BUSCA_CONFIG = {"calendario": {"k_vector": 6, "k_text": 8}, "edital": {"k_vector": 6, "k_text": 8}, "default": {"k_vector": 6, "k_text": 6}}
_MAX_CHUNKS_CONTEXTO = 4
_MAX_CHARS_CONTEXTO_TOTAL = 2500

@dataclass
class ChunkRecuperado:
    content: str
    source: str
    doc_type: str
    rrf_score: float

    @property
    def titulo_fonte(self) -> str:
        return _SOURCE_PARA_TITULO.get(self.source, self.source)

    @property
    def label_tipo(self) -> str:
        return _DOC_TYPE_PARA_LABEL.get(self.doc_type, "INFORMAÇÃO")

@dataclass
class ResultadoRecuperacao:
    chunks: List[ChunkRecuperado] = field(default_factory=list)
    contexto_formatado: str = ""
    encontrou: bool = False
    metodo_usado: str = ""

class RetrieveContextUseCase:
    """
    Caso de Uso: Buscar contexto no banco vetorial, aplicar deduplicação 
    e montar os cabeçalhos hierárquicos (Anti-Alucinação).
    """
    def __init__(self, vector_store: IVectorStorePort):
        self.vector_store = vector_store

    async def executar(self, query_transformada: QueryTransformada, source_filter: str = None, doc_type: str = None) -> ResultadoRecuperacao:
        config = _BUSCA_CONFIG.get(doc_type or "default", _BUSCA_CONFIG["default"])
        
        # 1. Busca Principal
        todos_chunks = await self._buscar(
            query_transformada.query_principal, source_filter, config["k_vector"], config["k_text"]
        )

        # 2. Busca para Sub-Queries (se houver)
        for sub_query in query_transformada.sub_queries:
            sub_chunks = await self._buscar(
                sub_query, source_filter, config["k_vector"] // 2, config["k_text"] // 2
            )
            todos_chunks.extend(sub_chunks)

        # 3. Deduplica e Ordena
        chunks_unicos = self._deduplicar_e_ordenar(todos_chunks)
        metodo = "hibrido"

        # 4. Fallback (Step-Back)
        if not chunks_unicos:
            step_back_query = transformar_para_step_back(query_transformada.query_original)
            chunks_unicos = await self._buscar(step_back_query, source_filter, config["k_vector"], config["k_text"])
            chunks_unicos = self._deduplicar_e_ordenar(chunks_unicos)
            metodo = "step_back"

        if not chunks_unicos:
            return ResultadoRecuperacao(encontrou=False, metodo_usado="vazio")

        # 5. Seleciona os melhores e formata
        chunks_selecionados = self._selecionar_chunks(chunks_unicos)
        contexto_formatado = self._formatar_contexto_hierarquico(chunks_selecionados)

        return ResultadoRecuperacao(
            chunks=chunks_selecionados, 
            contexto_formatado=contexto_formatado, 
            encontrou=True, 
            metodo_usado=metodo
        )

    async def _buscar(self, query: str, source_filter: str, k_vector: int, k_text: int) -> List[ChunkRecuperado]:
        # AQUI É A MÁGICA: O Use Case não sabe se é Redis ou Postgres!
        resultados_raw = await self.vector_store.buscar_hibrido(query, k_vector, k_text, source_filter)
        
        chunks = []
        for r in resultados_raw:
            content = r.get("content", "").strip()
            if len(content) >= 30:
                chunks.append(ChunkRecuperado(
                    content=content, source=r.get("source", ""),
                    doc_type=r.get("doc_type", ""), rrf_score=r.get("rrf_score", 0.0)
                ))
        return chunks

    def _deduplicar_e_ordenar(self, chunks: List[ChunkRecuperado]) -> List[ChunkRecuperado]:
        vistos = {}
        for chunk in chunks:
            fingerprint = chunk.content[:100].strip().lower()
            if fingerprint not in vistos or chunk.rrf_score > vistos[fingerprint].rrf_score:
                vistos[fingerprint] = chunk
        return sorted(vistos.values(), key=lambda c: c.rrf_score, reverse=True)

    def _selecionar_chunks(self, chunks: List[ChunkRecuperado]) -> List[ChunkRecuperado]:
        selecionados = []
        total_chars = 0
        for chunk in chunks:
            if len(selecionados) >= _MAX_CHUNKS_CONTEXTO or (total_chars + len(chunk.content) > _MAX_CHARS_CONTEXTO_TOTAL and selecionados):
                break
            selecionados.append(chunk)
            total_chars += len(chunk.content)
        return selecionados

    def _formatar_contexto_hierarquico(self, chunks: List[ChunkRecuperado]) -> str:
        por_source = {}
        for chunk in chunks:
            por_source.setdefault(chunk.source, []).append(chunk)

        blocos = []
        for source, source_chunks in por_source.items():
            primeiro = source_chunks[0]
            cabecalho = f"━━━ FONTE: {primeiro.titulo_fonte} [{primeiro.label_tipo}] ━━━"
            conteudos = [cabecalho] + [c.content for c in source_chunks]
            blocos.append("\n".join(conteudos))
        return "\n\n".join(blocos)