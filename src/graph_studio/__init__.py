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

⛔ **CONGELADO** (2026-09-09, item C2 do plano de recuperação). Continua
funcionando; não recebe investimento novo. Funcionalidade nova de fluxo vai
para `application/orchestration/`, que é onde "topologia como dado" foi
resolvido de verdade (`GraphSpec`, ADR 0008 Fase 5).

Contexto e a diferença entre os dois motores de grafo:
`docs/architecture/graph-studio-sandbox.md`. Registrado como TD-020.
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
