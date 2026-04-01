import logging
import asyncio
from dataclasses import dataclass, field
from typing import List, Dict, Any

from src.domain.ports.vector_store_port import IVectorStorePort
from src.rag.query_transform import QueryTransformada, transformar_para_step_back

logger = logging.getLogger(__name__)

# --- Configurações de Domínio (Inalteradas) ---
_SOURCE_PARA_TITULO = {
    "calendario-academico-2026.pdf": "Calendário Acadêmico UEMA 2026",
    "edital_paes_2026.pdf": "Edital PAES 2026 — Processo Seletivo UEMA",
    "guia_contatos_2025.pdf": "Guia de Contatos UEMA 2025",
}
_DOC_TYPE_PARA_LABEL = {
    "calendario": "CALENDÁRIO ACADÊMICO", 
    "edital": "EDITAL PAES 2026", 
    "contatos": "CONTATOS UEMA"
}
_BUSCA_CONFIG = {
    "calendario": {"k": 8}, 
    "edital": {"k": 10}, 
    "default": {"k": 6}
}
_MAX_CHUNKS_CONTEXTO = 4
_MAX_CHARS_CONTEXTO_TOTAL = 2500

@dataclass
class ChunkRecuperado:
    id: str
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
    Cérebro do RAG: Coordena a busca, aplica a inteligência do RRF (Reciprocal Rank Fusion)
    e garante que o contexto seja formatado sem alucinações.
    """
    def __init__(self, vector_store: IVectorStorePort):
        self.vector_store = vector_store

    async def executar(self, query_transformada: QueryTransformada, source_filter: str = None, doc_type: str = None) -> ResultadoRecuperacao:
        config = _BUSCA_CONFIG.get(doc_type or "default", _BUSCA_CONFIG["default"])
        k_final = config["k"]

        # 1. Busca Principal (Chamando a lógica interna de RRF)
        todos_chunks = await self._buscar_com_rrf(
            query_transformada.query_principal, source_filter, k_final
        )

        # 2. Busca para Sub-Queries (Opcional, com k reduzido)
        if query_transformada.sub_queries:
            tarefas = [self._buscar_com_rrf(q, source_filter, k_final // 2) for q in query_transformada.sub_queries]
            resultados_sub = await asyncio.gather(*tarefas)
            for sub_lista in resultados_sub:
                todos_chunks.extend(sub_lista)

        # 3. Deduplica por conteúdo e Ordena pelo Score final
        chunks_unicos = self._deduplicar_e_ordenar(todos_chunks)
        metodo = "hibrido"

        # 4. Fallback (Step-Back) se não vier nada
        if not chunks_unicos:
            step_back_query = transformar_para_step_back(query_transformada.query_original)
            chunks_unicos = await self._buscar_com_rrf(step_back_query, source_filter, k_final)
            chunks_unicos = self._deduplicar_e_ordenar(chunks_unicos)
            metodo = "step_back"

        if not chunks_unicos:
            return ResultadoRecuperacao(encontrou=False, metodo_usado="vazio")

        # 5. Seleciona pela janela de contexto (Tokens/Chars) e formata
        chunks_selecionados = self._selecionar_chunks(chunks_unicos)
        contexto_formatado = self._formatar_contexto_hierarquico(chunks_selecionados)

        return ResultadoRecuperacao(
            chunks=chunks_selecionados, 
            contexto_formatado=contexto_formatado, 
            encontrou=True, 
            metodo_usado=metodo
        )

    async def _buscar_com_rrf(self, query: str, source_filter: str, k: int) -> List[ChunkRecuperado]:
        """
        AQUI MORA A REGRA DE NEGÓCIO:
        Pede dados crus ao banco e calcula a fusão (RRF) na memória do Python.
        """
        # Pede listas cruas (Independente de banco de dados)
        dados_crus = await self.vector_store.buscar_contexto(query, k, source_filter)
        
        lista_vetor = dados_crus.get("vetorial", [])
        lista_texto = dados_crus.get("textual", [])

        # Algoritmo RRF (Reciprocal Rank Fusion)
        # k=60 é a constante padrão que suaviza o ranking
        rrf_const = 60
        scores = {}
        docs_map = {}

        # Pontua resultados da busca semântica
        for rank, doc in enumerate(lista_vetor, start=1):
            doc_id = doc["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_const + rank)
            docs_map[doc_id] = doc

        # Pontua resultados da busca por palavra-chave (BM25)
        for rank, doc in enumerate(lista_texto, start=1):
            doc_id = doc["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_const + rank)
            docs_map[doc_id] = doc

        # Converte para objetos de domínio ChunkRecuperado
        resultados = []
        for doc_id, score in scores.items():
            raw = docs_map[doc_id]
            content = raw.get("content", "").strip()
            
            # Filtro de qualidade mínima
            if len(content) >= 30:
                resultados.append(ChunkRecuperado(
                    id=doc_id,
                    content=content,
                    source=raw.get("source", ""),
                    doc_type=raw.get("doc_type", ""),
                    rrf_score=score
                ))
        
        return resultados

    def _deduplicar_e_ordenar(self, chunks: List[ChunkRecuperado]) -> List[ChunkRecuperado]:
        vistos = {}
        for chunk in chunks:
            # Fingerprint pelos primeiros 100 caracteres
            fingerprint = chunk.content[:100].strip().lower()
            if fingerprint not in vistos or chunk.rrf_score > vistos[fingerprint].rrf_score:
                vistos[fingerprint] = chunk
        
        # Ordenação final: o melhor score RRF fica no topo
        return sorted(vistos.values(), key=lambda c: c.rrf_score, reverse=True)

    def _selecionar_chunks(self, chunks: List[ChunkRecuperado]) -> List[ChunkRecuperado]:
        selecionados = []
        total_chars = 0
        for chunk in chunks:
            # Respeita o limite de chunks E o limite de caracteres para não estourar o LLM
            if len(selecionados) >= _MAX_CHUNKS_CONTEXTO or \
               (total_chars + len(chunk.content) > _MAX_CHARS_CONTEXTO_TOTAL and selecionados):
                break
            selecionados.append(chunk)
            total_chars += len(chunk.content)
        return selecionados

    def _formatar_contexto_hierarquico(self, chunks: List[ChunkRecuperado]) -> str:
        # Agrupa chunks por fonte para facilitar a leitura do LLM
        por_source = {}
        for chunk in chunks:
            por_source.setdefault(chunk.source, []).append(chunk)

        blocos = []
        for source, source_chunks in por_source.items():
            primeiro = source_chunks[0]
            cabecalho = f"━━━ FONTE: {primeiro.titulo_fonte} [{primeiro.label_tipo}] ━━━"
            # Concatena os chunks da mesma fonte
            conteudos = [cabecalho] + [c.content for c in source_chunks]
            blocos.append("\n".join(conteudos))
        
        return "\n\n".join(blocos)