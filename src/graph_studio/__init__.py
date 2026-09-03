"""
src/graph_studio/
=================
Biblioteca de componentes de infraestrutura (`BaseNode`: LLM/STT/TTS/Parser/
Tool/Channel/MCP/REST/Embeddings/Trigger) + executor sandbox do Graph Studio
(`GraphExecutor`, `topology_*`).

NÃO é o grafo de orquestração de produção — esse vive em
`src/application/orchestration/` (ADR 0008). Este pacote alimenta a paleta do
Graph Studio (`/hub/graph-studio`) e o catálogo `/hub/graph-nodes`; nada aqui
roda no caminho de uma mensagem real.
"""

from src.graph_studio.base_node import BaseNode, Port, PortType, NodeHealthStatus
from src.graph_studio.execution_context import ExecutionContext
from src.graph_studio.node_registry import NodeRegistry, get_registry, reset_registry

__all__ = [
    "BaseNode",
    "Port",
    "PortType",
    "NodeHealthStatus",
    "ExecutionContext",
    "NodeRegistry",
    "get_registry",
    "reset_registry",
]
