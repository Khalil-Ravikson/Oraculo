"""Nós do grafo — implementações concretas de BaseNode."""

from src.graph.nodes.stt_node import STTNode
from src.graph.nodes.tts_node import TTSNode
from src.graph.nodes.embeddings_node import EmbeddingsNode
from src.graph.nodes.llm_node import LLMNode
from src.graph.nodes.parser_node import ParserNode
from src.graph.nodes.tool_node import ToolNode

# Futuro (Fase 7-8): ChannelNode, MCPToolProviderNode

__all__ = [
    "STTNode",
    "TTSNode",
    "EmbeddingsNode",
    "LLMNode",
    "ParserNode",
    "ToolNode",
]
