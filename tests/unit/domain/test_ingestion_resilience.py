# tests/unit/domain/test_ingestion_resilience.py
import pytest
from unittest.mock import MagicMock, patch
from src.infrastructure.redis_client import acquire_token_bucket, get_document_hash, set_document_hash
from src.application.tasks.ingestion_tasks import processar_documento


def test_acquire_token_bucket_success():
    mock_redis = MagicMock()
    mock_redis.eval.return_value = 1
    
    with patch("src.infrastructure.redis_client.get_redis", return_value=mock_redis):
        res = acquire_token_bucket("test:limiter", capacity=5, refill_rate=0.1)
        assert res is True
        mock_redis.eval.assert_called_once()


def test_acquire_token_bucket_depleted():
    mock_redis = MagicMock()
    mock_redis.eval.return_value = 0
    
    with patch("src.infrastructure.redis_client.get_redis", return_value=mock_redis):
        res = acquire_token_bucket("test:limiter", capacity=5, refill_rate=0.1)
        assert res is False
        mock_redis.eval.assert_called_once()


def test_get_set_document_hash():
    mock_redis = MagicMock()
    mock_redis.hget.return_value = "my_hash"
    
    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis):
        h = get_document_hash("my_doc.pdf")
        assert h == "my_hash"
        mock_redis.hget.assert_called_once_with("ingest:hashes", "my_doc.pdf")
        
        set_document_hash("my_doc.pdf", "new_hash")
        mock_redis.hset.assert_called_once_with("ingest:hashes", "my_doc.pdf", "new_hash")


def test_processar_documento_retries_on_rate_limit(tmp_path):
    # Create dummy file
    doc_file = tmp_path / "test.txt"
    doc_file.write_text("Hello world" * 50)
    
    strategy_params = {
        "size": 100,
        "overlap": 10,
        "strategy": "recursive",
        "doc_type": "geral",
        "label": "TEST"
    }

    mock_embeddings = MagicMock()
    mock_embeddings.embed_documents.return_value = [[0.1] * 3072]
    
    from celery.exceptions import Retry
    
    with patch("src.application.tasks.ingestion_tasks._extrair_texto", return_value="Dummy extracted text"), \
         patch("src.rag.embeddings.get_embeddings", return_value=mock_embeddings), \
         patch("src.infrastructure.redis_client.acquire_token_bucket", return_value=False) as mock_acquire, \
         patch("src.infrastructure.redis_client.get_document_hash", return_value=None), \
         patch("src.infrastructure.redis_client.deletar_chunks_por_source") as mock_delete, \
         patch("src.application.tasks.ingestion_tasks.processar_documento.retry", side_effect=Retry()) as mock_retry:
         
         with pytest.raises(Retry):
             processar_documento.run(
                 file_path=str(doc_file),
                 strategy_params=strategy_params,
                 completed_batches=0,
                 accumulated_embeddings=[]
             )
         
         mock_acquire.assert_called_once_with("limiter:embeddings", capacity=15, refill_rate=0.25, requested=1)
         mock_retry.assert_called_once()
         mock_delete.assert_called_once()


def test_processar_documento_bypass_when_hash_matches(tmp_path):
    # Create dummy file
    doc_file = tmp_path / "test.txt"
    doc_file.write_text("Hello world" * 50)
    
    strategy_params = {
        "size": 100,
        "overlap": 10,
        "strategy": "recursive",
        "doc_type": "geral",
        "label": "TEST"
    }

    import hashlib
    file_hash = hashlib.sha256(b"Hello world" * 50).hexdigest()

    with patch("src.infrastructure.redis_client.get_document_hash", return_value=file_hash) as mock_get_hash, \
         patch("src.application.tasks.ingestion_tasks._notificar_admin") as mock_notify:
         
         res = processar_documento.run(
             file_path=str(doc_file),
             strategy_params=strategy_params,
             chat_id="12345"
         )
         
         assert res["ok"] is True
         assert res["bypassed"] is True
         mock_get_hash.assert_called_once_with("test.txt")
         mock_notify.assert_called_once_with("12345", res)
