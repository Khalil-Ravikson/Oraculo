"""
tests/conftest.py
-----------------
Configuração central do pytest. Todos os fixtures compartilhados ficam aqui.

CONVENÇÕES:
  - Fixtures de scope="session" → inicializados uma vez por sessão (custosos)
  - Fixtures de scope="function" → re-criados a cada teste (estado limpo)
  - Mocks de infra (Redis, DB, LLM) são sempre function-scoped para isolamento
  - Fixtures reais (Redis em processo) são session-scoped com cleanup

COMO RODAR:
  # Todos os testes (sem Docker):
  pytest tests/ -v --tb=short

  # Apenas unitários (sem IO):
  pytest tests/unit/ -v

  # RAG eval (precisa Redis + PDFs ingeridos):
  pytest tests/eval/ -v -m rag_eval

  # E2E (precisa uvicorn rodando):
  pytest tests/e2e/ -v -m e2e

MARKERS:
  @pytest.mark.unit         → testes sem IO externo
  @pytest.mark.integration  → precisa Redis local
  @pytest.mark.rag_eval     → avaliação de qualidade RAG
  @pytest.mark.e2e          → precisa servidor rodando
  @pytest.mark.llm          → chama LLM real (caro, rodar com -m llm)
  @pytest.mark.slow         → testes lentos (>5s)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# PATH e ENV
# ─────────────────────────────────────────────────────────────────────────────

# Garante que src/ está acessível sem instalar o pacote
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Variáveis de ambiente para testes (não precisam de valores reais)
os.environ.setdefault("GEMINI_API_KEY",            "test-key-fake")
os.environ.setdefault("EVOLUTION_BASE_URL",        "http://localhost:8080")
os.environ.setdefault("EVOLUTION_API_KEY",         "test-evo-key")
os.environ.setdefault("EVOLUTION_INSTANCE_NAME",   "test-instance")
os.environ.setdefault("WHATSAPP_HOOK_URL",         "http://localhost:9000/webhook")
os.environ.setdefault("REDIS_URL",                 "redis://localhost:6379/15")  # DB 15 = testes
os.environ.setdefault("DATABASE_URL",              "postgresql+asyncpg://user:pass@localhost:5433/oraculo_test")
os.environ.setdefault("ADMIN_API_KEY",             "test-admin-key")
os.environ.setdefault("ADMIN_NUMBERS",             "5598000000001")
os.environ.setdefault("STUDENT_NUMBERS",           "5598000000002")
os.environ.setdefault("DEV_MODE",                  "True")
os.environ.setdefault("DEV_WHITELIST",             "5598000000001,5598000000002")
os.environ.setdefault("EMBEDDING_PROVIDER",        "local")


# ─────────────────────────────────────────────────────────────────────────────
# Markers
# ─────────────────────────────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "unit: testes sem IO externo")
    config.addinivalue_line("markers", "integration: requer Redis local")
    config.addinivalue_line("markers", "rag_eval: avaliação de qualidade RAG")
    config.addinivalue_line("markers", "e2e: requer servidor uvicorn rodando")
    config.addinivalue_line("markers", "llm: chama LLM real")
    config.addinivalue_line("markers", "slow: teste lento (>5s)")


# ─────────────────────────────────────────────────────────────────────────────
# Event loop (para testes async)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Event loop compartilhado para testes async na sessão."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# Redis fake (in-memory, sem conexão real)
# ─────────────────────────────────────────────────────────────────────────────

class FakeRedis:
    """Redis in-memory para testes unitários. Thread-safe, sem dependência externa."""

    def __init__(self):
        self._store: dict = {}
        self._ttls: dict = {}

    def get(self, key: str):
        return self._store.get(key)

    def set(self, key: str, value, **kwargs):
        self._store[key] = value
        return True

    def setex(self, key: str, ttl: int, value):
        self._store[key] = value
        self._ttls[key] = ttl
        return True

    def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
        return len(keys)

    def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    def incr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    def expire(self, key: str, ttl: int) -> bool:
        self._ttls[key] = ttl
        return True

    def ttl(self, key: str) -> int:
        return self._ttls.get(key, -1)

    def lpush(self, key: str, *values):
        if key not in self._store:
            self._store[key] = []
        for v in values:
            self._store[key].insert(0, v)
        return len(self._store[key])

    def lrange(self, key: str, start: int, end: int) -> list:
        lst = self._store.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]

    def ltrim(self, key: str, start: int, end: int):
        lst = self._store.get(key, [])
        self._store[key] = lst[start:end + 1]

    def llen(self, key: str) -> int:
        return len(self._store.get(key, []))

    def hset(self, key: str, field=None, value=None, mapping=None):
        if key not in self._store:
            self._store[key] = {}
        if mapping:
            self._store[key].update(mapping)
        elif field is not None:
            self._store[key][field] = value
        return 1

    def hget(self, key: str, field: str):
        return self._store.get(key, {}).get(field)

    def hgetall(self, key: str) -> dict:
        return dict(self._store.get(key, {}))

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        if key not in self._store:
            self._store[key] = {}
        current = int(self._store[key].get(field, 0))
        self._store[key][field] = current + amount
        return current + amount

    def rpush(self, key: str, *values):
        if key not in self._store:
            self._store[key] = []
        self._store[key].extend(values)
        return len(self._store[key])

    def scan(self, cursor: int, match: str = "*", count: int = 100):
        import fnmatch
        pattern = match.replace("\\.", ".").replace("\\-", "-")
        matched = [k for k in self._store.keys() if fnmatch.fnmatch(k, pattern)]
        return 0, matched

    def ping(self) -> bool:
        return True

    def pipeline(self, transaction: bool = True):
        return FakePipeline(self)

    def lock(self, name: str, timeout=None, blocking_timeout=None):
        return FakeLock(self, name)

    def json(self):
        return FakeJsonCommands(self)

    def ft(self, index_name: str):
        return FakeSearchIndex()

    def info(self, section: str = "server") -> dict:
        return {"redis_version": "7.0.0-test", "used_memory": 1024 * 1024}


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._queue: list = []

    def incr(self, key: str):
        self._queue.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int):
        self._queue.append(("expire", key, ttl))
        return self

    def execute(self) -> list:
        results = []
        for op in self._queue:
            if op[0] == "incr":
                results.append(self._redis.incr(op[1]))
            elif op[0] == "expire":
                results.append(self._redis.expire(op[1], op[2]))
        self._queue.clear()
        return results


class FakeLock:
    def __init__(self, redis: FakeRedis, name: str):
        self._redis = redis
        self._name = name

    def acquire(self) -> bool:
        return True

    def release(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeJsonCommands:
    def __init__(self, redis: FakeRedis):
        self._redis = redis

    def set(self, key: str, path: str, value):
        self._redis._store[key] = value
        return True

    def get(self, key: str, path: str = "$"):
        val = self._redis._store.get(key)
        if val is None:
            return None
        return [val] if path == "$" else val


class FakeSearchIndex:
    def info(self) -> dict:
        return {"num_docs": 0, "num_terms": 0, "indexing": 0}

    def create_index(self, *args, **kwargs):
        pass

    def search(self, query, query_params=None):
        result = MagicMock()
        result.docs = []
        result.total = 0
        return result

    def aggregate(self, request):
        result = MagicMock()
        result.rows = []
        return result


@pytest.fixture
def fake_redis() -> FakeRedis:
    """Redis em memória para testes unitários."""
    return FakeRedis()


@pytest.fixture
def fake_redis_text() -> FakeRedis:
    """Redis em memória com decode_responses=True (strings)."""
    return FakeRedis()


# ─────────────────────────────────────────────────────────────────────────────
# Mock LLM Provider
# ─────────────────────────────────────────────────────────────────────────────

class MockLLMProvider:
    """LLM falso para testes. Retorna respostas configuráveis."""

    def __init__(self, default_response: str = "Resposta mock do LLM."):
        self.default_response = default_response
        self.calls: list[dict] = []

    def gerar_resposta_sincrono(self, prompt: str, temperatura: float = 0.2, max_tokens: int = 1024):
        self.calls.append({"type": "text", "prompt": prompt[:100]})
        result = MagicMock()
        result.conteudo = self.default_response
        result.sucesso = True
        result.input_tokens = len(prompt.split())
        result.output_tokens = len(self.default_response.split())
        result.tokens_total = result.input_tokens + result.output_tokens
        return result

    async def gerar_resposta_async(self, prompt: str, **kwargs):
        return self.gerar_resposta_sincrono(prompt, **kwargs)

    async def gerar_resposta_estruturada_async(self, prompt: str, response_schema=None, **kwargs):
        self.calls.append({"type": "structured", "prompt": prompt[:100]})
        # Retorna instância do schema com valores mock
        if response_schema is not None:
            try:
                fields = response_schema.__fields__
                mock_data = {}
                for fname, finfo in fields.items():
                    annotation = str(finfo.annotation)
                    if "list" in annotation.lower():
                        mock_data[fname] = ["mock item 1", "mock item 2"]
                    elif "str" in annotation.lower():
                        mock_data[fname] = f"mock {fname}"
                    else:
                        mock_data[fname] = None
                return response_schema(**mock_data)
            except Exception:
                return None
        return None


@pytest.fixture
def mock_llm() -> MockLLMProvider:
    return MockLLMProvider()


@pytest.fixture
def mock_llm_hyde() -> MockLLMProvider:
    """LLM mock que retorna documentos hipotéticos realistas."""
    llm = MockLLMProvider(
        "A matrícula de veteranos da UEMA ocorre tipicamente no início do semestre letivo, "
        "com prazo determinado pelo Calendário Acadêmico. Os alunos veteranos devem realizar "
        "a rematrícula pelo sistema SIGAA dentro do período estabelecido pela PROG."
    )
    return llm


# ─────────────────────────────────────────────────────────────────────────────
# Mock Evolution API
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_evolution():
    svc = AsyncMock()
    svc.enviar_mensagem = AsyncMock(return_value=True)
    svc.verificar_instancia = AsyncMock(return_value="open")
    svc.inicializar = AsyncMock()
    return svc


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures de domínio
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_phone() -> str:
    return "5598999990001"


@pytest.fixture
def sample_chat_id(sample_phone) -> str:
    return f"{sample_phone}@s.whatsapp.net"


@pytest.fixture
def sample_identity(sample_phone, sample_chat_id) -> dict:
    return {
        "sender_phone": sample_phone,
        "chat_id": sample_chat_id,
        "body": "quando é a matrícula?",
        "has_media": False,
        "msg_type": "conversation",
        "push_name": "Aluno Teste",
    }


@pytest.fixture
def sample_fatos() -> list[str]:
    return [
        "Aluno do curso de Engenharia Civil, turno noturno",
        "Centro CECEN, veterano semestre 2026.1",
    ]


@pytest.fixture
def sample_raw_query(sample_fatos) -> "RawQuery":
    from src.rag.query.protocols import RawQuery
    return RawQuery(
        text="quando é a matrícula de veteranos?",
        user_id="test_user",
        fatos_usuario=sample_fatos,
    )


@pytest.fixture
def sample_chunks() -> list[dict]:
    return [
        {
            "content": "[CALENDÁRIO ACADÊMICO UEMA 2026 | calendario]\nEVENTO: Matrícula de veteranos | DATA: 03/02/2026 a 07/02/2026 | SEM: 2026.1",
            "source": "calendario-academico-2026.pdf",
            "doc_type": "calendario",
            "rrf_score": 0.045,
        },
        {
            "content": "[EDITAL PAES 2026 | edital]\nCATEGORIA: BR-PPI | NOME: Ampla Concorrência Pretos, Pardos e Indígenas",
            "source": "edital_paes_2026.pdf",
            "doc_type": "edital",
            "rrf_score": 0.032,
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI test client
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app_client():
    """Cliente de teste para a FastAPI app (sem uvicorn)."""
    try:
        from httpx import AsyncClient
        from src.main import app

        async def _client():
            async with AsyncClient(app=app, base_url="http://test") as client:
                yield client

        return _client
    except ImportError:
        pytest.skip("httpx não instalado: pip install httpx")