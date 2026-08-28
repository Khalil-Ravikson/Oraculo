"""Testes de STTNode e TTSNode — mocka AudioService, não chama provider real."""

import pytest
from unittest.mock import AsyncMock, patch
from src.graph.nodes.stt_node import STTNode
from src.graph.nodes.tts_node import TTSNode
from src.graph.execution_context import ExecutionContext


class _FakeAudioResult:
    def __init__(self, ok=True, text="", audio_path="", error=""):
        self.ok = ok
        self.text = text
        self.audio_path = audio_path
        self.error = error


class TestSTTNode:
    """Testes de STTNode."""

    def test_node_identity(self):
        node = STTNode()
        assert node.node_id == "stt_default"
        assert node.node_type == "stt_provider"

    def test_input_ports(self):
        node = STTNode()
        names = {p.name for p in node.input_ports}
        assert "audio_bytes" in names
        assert "mime_type" in names

    def test_output_ports(self):
        node = STTNode()
        names = {p.name for p in node.output_ports}
        assert "text" in names
        assert "ok" in names

    @pytest.mark.asyncio
    async def test_execute_missing_audio_raises(self):
        node = STTNode()
        with pytest.raises(ValueError):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_success(self):
        node = STTNode()
        fake_service = AsyncMock()
        fake_service.transcribe.return_value = _FakeAudioResult(ok=True, text="ola mundo")

        with patch(
            "src.infrastructure.services.audio_service.get_audio_service",
            return_value=fake_service
        ):
            result = await node.execute(
                {"audio_bytes": b"fake-audio", "mime_type": "audio/ogg"},
                ExecutionContext()
            )

        assert result["text"] == "ola mundo"
        assert result["ok"] is True
        fake_service.transcribe.assert_awaited_once_with(b"fake-audio", "audio/ogg")

    @pytest.mark.asyncio
    async def test_execute_default_mime_type(self):
        node = STTNode()
        fake_service = AsyncMock()
        fake_service.transcribe.return_value = _FakeAudioResult(ok=True, text="x")

        with patch(
            "src.infrastructure.services.audio_service.get_audio_service",
            return_value=fake_service
        ):
            await node.execute({"audio_bytes": b"abc"}, ExecutionContext())

        fake_service.transcribe.assert_awaited_once_with(b"abc", "audio/ogg")

    @pytest.mark.asyncio
    async def test_execute_provider_failure_raises(self):
        node = STTNode()
        fake_service = AsyncMock()
        fake_service.transcribe.return_value = _FakeAudioResult(ok=False, error="provider down")

        with patch(
            "src.infrastructure.services.audio_service.get_audio_service",
            return_value=fake_service
        ):
            with pytest.raises(RuntimeError, match="provider down"):
                await node.execute({"audio_bytes": b"abc"}, ExecutionContext())


class TestTTSNode:
    """Testes de TTSNode."""

    def test_node_identity(self):
        node = TTSNode()
        assert node.node_id == "tts_default"
        assert node.node_type == "tts_provider"

    def test_input_ports(self):
        node = TTSNode()
        names = {p.name for p in node.input_ports}
        assert "text" in names
        assert "lang" in names

    def test_output_ports(self):
        node = TTSNode()
        names = {p.name for p in node.output_ports}
        assert "audio_path" in names
        assert "ok" in names

    @pytest.mark.asyncio
    async def test_execute_missing_text_raises(self):
        node = TTSNode()
        with pytest.raises(ValueError):
            await node.execute({}, ExecutionContext())

    @pytest.mark.asyncio
    async def test_execute_success(self):
        node = TTSNode()
        fake_service = AsyncMock()
        fake_service.synthesize.return_value = _FakeAudioResult(ok=True, audio_path="/tmp/out.mp3")

        with patch(
            "src.infrastructure.services.audio_service.get_audio_service",
            return_value=fake_service
        ):
            result = await node.execute(
                {"text": "ola", "lang": "pt"},
                ExecutionContext()
            )

        assert result["audio_path"] == "/tmp/out.mp3"
        assert result["ok"] is True
        fake_service.synthesize.assert_awaited_once_with("ola", "pt")

    @pytest.mark.asyncio
    async def test_execute_default_lang(self):
        node = TTSNode()
        fake_service = AsyncMock()
        fake_service.synthesize.return_value = _FakeAudioResult(ok=True, audio_path="/tmp/x.mp3")

        with patch(
            "src.infrastructure.services.audio_service.get_audio_service",
            return_value=fake_service
        ):
            await node.execute({"text": "ola"}, ExecutionContext())

        fake_service.synthesize.assert_awaited_once_with("ola", "pt")

    @pytest.mark.asyncio
    async def test_execute_provider_failure_raises(self):
        node = TTSNode()
        fake_service = AsyncMock()
        fake_service.synthesize.return_value = _FakeAudioResult(ok=False, error="tts crashed")

        with patch(
            "src.infrastructure.services.audio_service.get_audio_service",
            return_value=fake_service
        ):
            with pytest.raises(RuntimeError, match="tts crashed"):
                await node.execute({"text": "ola"}, ExecutionContext())
