import logging
import os
from functools import lru_cache
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

from langchain_core.embeddings import Embeddings

class TenacityEmbeddingWrapper(Embeddings):
    def __init__(self, base_embeddings):
        self._base = base_embeddings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        from tenacity import retry, stop_after_attempt, wait_exponential
        @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
        def _exec():
            return self._base.embed_documents(texts)
        return _exec()

    def embed_query(self, text: str) -> list[float]:
        from tenacity import retry, stop_after_attempt, wait_exponential
        @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
        def _exec():
            return self._base.embed_query(text)
        return _exec()

# O Singleton perfeito que você já usava!
@lru_cache(maxsize=1)
def get_embeddings():
    """
    Fábrica de Embeddings: Decide dinamicamente se usa Google ou Local (HF)
    baseado na variável de ambiente EMBEDDING_PROVIDER.
    Agora usa um wrapper com Tenacity para rate-limiting (429).
    """
    
    # Lê do .env (Se não existir, usa 'google' como padrão agora)
    provider = os.getenv("EMBEDDING_PROVIDER", "google").lower()

    if provider == "google":
        logger.info("☁️ Iniciando modelo de Embeddings na Nuvem: Google Gemini (models/embedding-001)...")
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        
        model = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001"
            # O Langchain já puxa o GOOGLE_API_KEY do seu .env automaticamente
        )
        return TenacityEmbeddingWrapper(model)
        
    elif provider == "local":
        # === O SEU CÓDIGO BRILHANTE ANTIGO AQUI ===
        _MODELO = "BAAI/bge-m3"
        
        if settings.HF_TOKEN:
            os.environ["HF_TOKEN"] = settings.HF_TOKEN
            os.environ["HUGGING_FACE_HUB_TOKEN"] = settings.HF_TOKEN
            logger.info("🔑 HF_TOKEN configurado — download autenticado.")
            
        logger.info(f"🖥️ Carregando modelo de embedding LOCAL: {_MODELO} (CPU, ~1.3GB)...")
        from langchain_huggingface import HuggingFaceEmbeddings
        
        model = HuggingFaceEmbeddings(
            model_name=_MODELO,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        logger.info("✅ Modelo Local pronto!")
        return TenacityEmbeddingWrapper(model)

    else:
        raise ValueError(f"Provedor de embedding desconhecido: {provider}")