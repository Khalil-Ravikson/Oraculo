import re
import hashlib
import logging
from typing import List, Dict, Any
from src.domain.ports.document_parser import IDocumentParser
from src.domain.ports.vector_store_port import IVectorStorePort

logger = logging.getLogger(__name__)

class IngestDocumentUseCase:
    """
    Caso de Uso responsável por ler um documento, fatiá-lo (chunking) 
    e salvá-lo no banco vetorial. Totalmente agnóstico de infraestrutura.
    """
    def __init__(self, parser: IDocumentParser, vector_store: IVectorStorePort):
        self.parser = parser
        self.vector_store = vector_store

    async def executar(self, file_path: str, source_name: str, config: dict) -> int:
        """
        Retorna o número de chunks salvos.
        """
        logger.info(f"📦 Iniciando ingestão do documento: {source_name}")
        
        # 1. Extração de Texto via Interface
        instrucao = config.get("parsing_instruction", "")
        texto_bruto = self.parser.parse(file_path, instrucao)

        if not texto_bruto.strip():
            logger.warning(f"⚠️ O parser não conseguiu extrair texto de '{source_name}'.")
            return 0

        # 2. Limpeza (Regra de Domínio)
        texto_limpo = self._limpar_texto(texto_bruto)

        # 3. Chunking Hierárquico (Regra de Domínio)
        chunk_size = config.get("chunk_size", 400)
        overlap = config.get("overlap", 50)
        label = config.get("label", source_name.upper())
        doc_type = config.get("doc_type", "geral")
        
        chunks_preparados = self._criar_chunks(texto_limpo, source_name, doc_type, label, chunk_size, overlap)

        # 4. Salvar no Banco Vetorial via Interface
        if chunks_preparados:
            await self.vector_store.salvar_chunks(chunks_preparados)
            logger.info(f"✅ Ingestão concluída: {len(chunks_preparados)} chunks salvos para '{source_name}'.")
            
        return len(chunks_preparados)

    # --- Regras de Negócio Internas (Isoladas do Infra) ---

    def _limpar_texto(self, texto: str) -> str:
        texto = re.sub(r"UNIVERSIDADE ESTADUAL DO MARANHÃO|www\.uema\.br", "", texto, flags=re.IGNORECASE)
        texto = re.sub(r"\n{3,}", "\n\n", texto)
        return texto.strip()

    def _criar_chunks(self, texto: str, source: str, doc_type: str, label: str, size: int, overlap: int) -> List[Dict[str, Any]]:
        """Gera os chunks com o cabeçalho anti-alucinação."""
        prefixo = f"[{label} | {doc_type}]\n"
        paragrafos = [p.strip() for p in re.split(r"\n{2,}", texto) if p.strip()]
        
        chunks = []
        atual = ""
        chunk_index = 0

        for paragrafo in paragrafos:
            candidato = f"{atual}\n\n{paragrafo}".strip() if atual else paragrafo
            if len(candidato) <= size:
                atual = candidato
            else:
                if atual:
                    texto_final = prefixo + atual
                    chunks.append(self._montar_dict_chunk(source, doc_type, texto_final, chunk_index))
                    chunk_index += 1
                atual = paragrafo # Fallback simplificado para o exemplo

        if atual:
            texto_final = prefixo + atual
            chunks.append(self._montar_dict_chunk(source, doc_type, texto_final, chunk_index))

        return chunks

    def _montar_dict_chunk(self, source: str, doc_type: str, content: str, index: int) -> dict:
        chunk_id = hashlib.md5(f"{source}:{index}".encode()).hexdigest()[:16]
        return {
            "chunk_id": chunk_id,
            "content": content,
            "source": source,
            "doc_type": doc_type,
            "metadata": {"chunk_index": index}
        }