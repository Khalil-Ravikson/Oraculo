from src.infrastructure.observability.metrics import get_metrics


def test_observe_stt_atualiza_counters_e_histogram():
    metrics = get_metrics()
    metrics.observe_stt(provider="gemini", ms=120, sucesso=True)
    metrics.observe_stt(provider="gemini", ms=500, sucesso=False)

    output, _ = metrics.generate_latest_output()
    text = output.decode()

    assert 'oraculo_stt_requests_total{provider="gemini",resultado="sucesso"}' in text
    assert 'oraculo_stt_requests_total{provider="gemini",resultado="falha"}' in text
    assert "oraculo_stt_latency_ms_bucket" in text


def test_observe_tts_atualiza_counters_e_histogram():
    metrics = get_metrics()
    metrics.observe_tts(provider="gtts", ms=80, sucesso=True)

    output, _ = metrics.generate_latest_output()
    text = output.decode()

    assert 'oraculo_tts_requests_total{provider="gtts",resultado="sucesso"}' in text
    assert "oraculo_tts_latency_ms_bucket" in text


def test_observe_vision_e_confidence():
    metrics = get_metrics()
    metrics.observe_vision(provider="gemini", ms=2000, sucesso=True)
    metrics.set_vision_confidence(0.87)

    output, _ = metrics.generate_latest_output()
    text = output.decode()

    assert 'oraculo_vision_requests_total{provider="gemini",resultado="sucesso"}' in text
    assert "oraculo_vision_confidence_last 0.87" in text
