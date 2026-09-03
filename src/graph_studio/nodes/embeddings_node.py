"""EmbeddingsNode — wrapper de BaseNode sobre src.rag.embeddings.get_embeddings()."""

import asyncio
from typing import Any, Dict, List
from src.graph_studio.base_node import BaseNode, Port, PortType
from src.graph_studio.execution_context import ExecutionContext


class EmbeddingsNode(BaseNode):
    """
    Nó de geração de embeddings.

    Delega para `src.rag.embeddings.get_embeddings()` (provider resolvido
    por `EMBEDDING_PROVIDER` — "google" ou "local"). O wrapper subjacente
    (`TenacityEmbeddingWrapper`) é **síncrono**, então `execute()` usa
    `asyncio.to_thread` pra não bloquear o event loop.

    Aceita dois modos de entrada (pelo menos um obrigatório):
    - `texts`: lista de strings → `embed_documents()` → retorna `embeddings`
      (lista de vetores, um por texto).
    - `query`: string única → `embed_query()` → retorna `embedding` (vetor
      único). Providers como o Google podem tratar query e documento com
      task_type diferente internamente; por isso os dois modos existem
      separados, não é só "texts com 1 item".

    Atenção (acoplamento existente, não introduzido por este nó): o índice
    HNSW do Redis tem `VECTOR_DIM=3072` hardcoded (`redis_client.py`),
    calibrado para `gemini-embedding-001`. Trocar `EMBEDDING_PROVIDER` para
    "local" (bge-m3, 1024d) quebra esse índice — este nó não valida isso,
    só expõe o provider configurado.
    """

    @property
    def node_id(self) -> str:
        return "embeddings_default"

    @property
    def node_type(self) -> str:
        return "embeddings_provider"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="texts",
                type_=PortType.ARRAY,
                description="Lista de textos a converter em embeddings (modo documento)",
                required=False
            ),
            Port(
                name="query",
                type_=PortType.TEXT,
                description="Texto único de consulta (modo query)",
                required=False
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="embeddings",
                type_=PortType.ARRAY,
                description="Lista de vetores de embedding, um por texto de entrada (modo documento)"
            ),
            Port(
                name="embedding",
                type_=PortType.EMBEDDINGS,
                description="Vetor de embedding único (modo query)"
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        texts = inputs.get("texts")
        query = inputs.get("query")

        if not texts and not query:
            raise ValueError("'texts' or 'query' is required")

        from src.rag.embeddings import get_embeddings

        embeddings_model = get_embeddings()

        if query:
            vector = await asyncio.to_thread(embeddings_model.embed_query, query)
            return {"embedding": vector}

        vectors = await asyncio.to_thread(embeddings_model.embed_documents, texts)
        return {"embeddings": vectors}

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "Embeddings via src.rag.embeddings (provider configurável em EMBEDDING_PROVIDER)",
        }
