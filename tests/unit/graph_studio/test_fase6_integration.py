"""Teste de integração da Fase 6 — os 3 nós registrados juntos, topologia válida."""

import pytest
from src.graph_studio.node_registry import NodeRegistry
from src.graph_studio.nodes import STTNode, TTSNode, EmbeddingsNode


class TestFase6Integration:
    """Valida que STT/TTS/Embeddings coexistem num registry e formam grafo coerente."""

    def test_all_three_nodes_register_without_conflict(self):
        registry = NodeRegistry()
        registry.register(STTNode())
        registry.register(TTSNode())
        registry.register(EmbeddingsNode())

        assert registry.count() == 3
        assert registry.get("stt_default") is not None
        assert registry.get("tts_default") is not None
        assert registry.get("embeddings_default") is not None

    def test_list_nodes_exposes_all_metadata(self):
        registry = NodeRegistry()
        registry.register(STTNode())
        registry.register(TTSNode())
        registry.register(EmbeddingsNode())

        nodes = registry.list_nodes()
        ids = {n["id"] for n in nodes}
        assert ids == {"stt_default", "tts_default", "embeddings_default"}

        stt_entry = next(n for n in nodes if n["id"] == "stt_default")
        assert stt_entry["type"] == "stt_provider"
        assert any(p["name"] == "audio_bytes" for p in stt_entry["input_ports"])
        assert any(p["name"] == "text" for p in stt_entry["output_ports"])

    def test_stt_to_embeddings_connection_valid(self):
        """
        Fluxo realista: STT produz 'text' (TEXT) → Embeddings consome via
        'query' (TEXT). Tipos devem casar (ambos TEXT).
        """
        registry = NodeRegistry()
        registry.register(STTNode())
        registry.register(EmbeddingsNode())

        is_valid, error = registry.validate_connection(
            "stt_default", "text",
            "embeddings_default", "query"
        )
        assert is_valid is True, error

    def test_embeddings_to_stt_connection_invalid_type_mismatch(self):
        """
        Conexão sem sentido: embeddings (ARRAY/EMBEDDINGS) → stt audio_bytes
        (AUDIO). Tipos não casam — deve ser rejeitada.
        """
        registry = NodeRegistry()
        registry.register(STTNode())
        registry.register(EmbeddingsNode())

        is_valid, error = registry.validate_connection(
            "embeddings_default", "embedding",
            "stt_default", "audio_bytes"
        )
        assert is_valid is False
        assert "Type mismatch" in error

    def test_tts_output_is_file_type(self):
        registry = NodeRegistry()
        registry.register(TTSNode())

        nodes = registry.list_nodes()
        tts_entry = next(n for n in nodes if n["id"] == "tts_default")
        audio_path_port = next(
            p for p in tts_entry["output_ports"] if p["name"] == "audio_path"
        )
        assert audio_path_port["type"] == "file"
