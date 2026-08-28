"""ParserNode — wrapper de BaseNode sobre ParserFactory.auto()."""

import asyncio
from typing import Any, Dict, List
from src.graph.base_node import BaseNode, Port, PortType
from src.graph.execution_context import ExecutionContext


class ParserNode(BaseNode):
    """
    Nó de parsing de documento (PDF/Docx/etc).

    Delega para `ParserFactory.auto(file_path)`, que já resolve qual
    parser usar (detecção de PDF-scan, prioridade/desabilitados via
    config dinâmica) e devolve uma instância pronta. `IDocumentParser.parse()`
    é **síncrono**, então `execute()` usa `asyncio.to_thread` pra não
    bloquear o event loop.

    Entrada é `file_path` (caminho em disco), não bytes — mesma assinatura
    de `IDocumentParser.parse()`. Saída é uma string única com todo o texto
    (chunking é etapa posterior no pipeline de ingestão, fora deste nó).
    """

    @property
    def node_id(self) -> str:
        return "parser_default"

    @property
    def node_type(self) -> str:
        return "parser"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="file_path",
                type_=PortType.FILE,
                description="Caminho em disco do arquivo a parsear"
            ),
            Port(
                name="instruction",
                type_=PortType.TEXT,
                description="Instrução opcional pro parser (ex: prompt de extração)",
                required=False
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="text",
                type_=PortType.TEXT,
                description="Texto extraído do documento"
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        file_path = inputs.get("file_path")
        if not file_path:
            raise ValueError("'file_path' is required")

        instruction = inputs.get("instruction", "")

        from src.rag.ingestion.parser_factory import ParserFactory

        parser = ParserFactory.auto(file_path)
        text = await asyncio.to_thread(parser.parse, file_path, instruction)

        return {"text": text}

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "Parser de documento via ParserFactory.auto() (seleção automática, prioridade configurável)",
        }
