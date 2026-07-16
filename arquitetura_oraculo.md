

---

# arquitetura_oraculo.md

## 1. Visão Geral

**Oráculo UEMA v5.1** — assistente acadêmico via WhatsApp (Evolution API) + portal admin FastAPI. Pipeline principal: **Cognitive OS** (multi-agente assíncrono sobre Celery + Redis Streams), substituindo o antigo `OracleChain` monolítico.

**Stack:** Python 3.12, FastAPI, Celery, PostgreSQL 16 (SQLAlchemy async), Redis Stack (RediSearch + RedisVL), Google Gemini (`google-genai`), LangChain (embeddings apenas).

---

## 2. Arquitetura de Cinco Camadas (Memória Cognitiva)

Implementada em `src/memory/services/redis_memory_service.py` — `CognitiveMemoryService`:


| Camada | Nome         | Storage Redis                   | TTL        | Função                                                                                      |
| ------ | ------------ | ------------------------------- | ---------- | ------------------------------------------------------------------------------------------- |
| **L1** | Conversation | `chat:{session_id}` (List)      | 30 min     | Últimos 10 turnos (20 msgs). Injetado no Synthesis e Orchestrator.                          |
| **L2** | Operational  | `op:{session_id}` (JSON)        | 30 min     | Estado transitório: `last_action`, `route_hint`, `status`. Atualizado pelo Cognitive OS.    |
| **L3** | Task History | `task_hist:{session_id}` (Hash) | 30 min     | `last_worker`, `last_result` (500 chars). Workers SIGAA/Synthesis gravam aqui.              |
| **L4** | User Memory  | `user_mem:{user_id}` (Hash)     | 7 dias     | Perfil dinâmico extraído por `LLMFactExtractor` (Gemini) + regex.                           |
| **L5** | Knowledge    | Redis Stack `idx:rag:chunks`    | permanente | RAG híbrido BM25 + HNSW (3072d, `gemini-embedding-001`). Não gerenciado pelo MemoryService. |


**Complemento legado:** `src/memory/container.py` → `MemoryService` (working + long-term + menu state) usado em `process_message_task` para persistência de turnos e extração de fatos.

---

## 3. Clean Architecture (Organização de Código)

```
Oraculo/
├── src/
│   ├── api/                    # Apresentação — FastAPI routers, SSE, middleware JWT
│   │   ├── routers/web/hub.py
│   │   ├── routers/admin/
│   │   ├── chain_sse.py
│   │   └── middleware/
│   ├── application/            # Orquestração — casos de uso, workers, pipeline IA
│   │   ├── chain/              # cognitive_os, planner, guardrails, reranker
│   │   ├── routing/            # semantic_router, llm_orchestrator, message_router
│   │   ├── workers/            # worker_*.py + registry.py (autodiscovery)
│   │   ├── tasks/              # Celery tasks (process_message, ingestion, beat)
│   │   ├── webhook/            # webhook_controller.py
│   │   ├── commands/           # Comandos admin WhatsApp (!status, !cache clear)
│   │   └── use_cases/
│   ├── domain/                 # Entidades, enums, ports (ILLMProvider, vector_store)
│   ├── infrastructure/         # Adapters concretos — DB, Redis, Gemini, Evolution
│   │   ├── adapters/           # gemini_provider, evolution_adapter, parsers
│   │   ├── database/           # models.py, session.py (async + NullPool)
│   │   ├── repositories/
│   │   ├── services/           # rag_search_service, router_service, synthesis
│   │   ├── redis_client.py     # índices RedisVL, busca_hibrida (sync p/ Celery)
│   │   ├── celery_app.py
│   │   └── message_stream.py   # Redis Streams journal
│   ├── memory/                 # Ports + adapters da memória (legado + cognitiva)
│   ├── rag/                    # embeddings, ingestion pipeline, query_transform
│   └── main.py                 # Entry point FastAPI
├── migrations/                 # Alembic (async)
├── templates/hub/              # Jinja2 admin
├── static/                     # JS/CSS hub
├── tests/                      # unit, e2e, eval
├── observability/              # prometheus.yml, alert_rules.yml
├── docker-compose.yml
└── Dockerfile
```

---

## 4. Integração FastAPI ↔ Redis Stack ↔ Gemini

### 4.1 FastAPI (camada de entrada)

```22:72:Oraculo/src/main.py
def create_app() -> FastAPI:
    ...
    app = FastAPI(title="Oráculo UEMA", version="5.1.0", ...)
    ...
    @app.on_event("startup")
    async def on_startup():
        instrumentator.expose(app, endpoint="/metrics")
        await _startup(settings)
```

**Startup (`_startup`):**

1. `inicializar_indices()` — cria `idx:rag:chunks` e `idx:tools` (HNSW, 3072 dims).
2. `IntentSeederService.seed()` — carrega intents/regex/embeddings no Redis (`router:config`, `tools:emb:`*).
3. Pré-aquecimento embeddings Gemini + autodiscovery de workers.
4. `EvolutionService.inicializar()` — gateway WhatsApp.

**Rotas críticas:**

- `POST /webhook/evolution` → enfileira Celery (`processar_mensagem_whatsapp.delay()`).
- `/hub/`* — portal admin (Jinja2).
- `/api/admin/*` — REST admin.
- `/health`, `/metrics` — observabilidade.

### 4.2 Redis Stack (multi-tenant por DB)


| Redis DB         | Uso                                                                   |
| ---------------- | --------------------------------------------------------------------- |
| `/0`             | App: vetores RAG, memória L1–L4, locks, HITL, semantic cache, streams |
| `/1`             | Celery broker                                                         |
| `/2`             | Celery result backend                                                 |
| `/1` (Evolution) | Cache Evolution API                                                   |


**Índices RediSearch/RedisVL:**

- `idx:rag:chunks` — prefixo `rag:chunk:`, campos text/tag/vector, taxonomia UEMA (`eixo`, `setor`, `tipo_doc`, `ano`, `campus`).
- `idx:tools` — prefixo `tools:emb:`, KNN para roteamento semântico.

**Streams:**

- `oraculo:stream:messages` — journal de mensagens (XADD/XACK, recovery XPENDING).
- `oraculo:stream:step_results` / `oraculo:stream:final_responses` — pipeline Cognitive OS.

### 4.3 Gemini (papéis no pipeline)


| Componente         | Modelo (settings)                           | Papel                                                    |
| ------------------ | ------------------------------------------- | -------------------------------------------------------- |
| `GeminiProvider`   | `GEMINI_MODEL` (default `gemini-2.5-flash`) | Geração texto, structured output                         |
| Embeddings         | `models/gemini-embedding-001`               | 3072d, ingestão + busca vetorial                         |
| Semantic Router L5 | Flash                                       | Classificação de intent (~50 tokens)                     |
| LLM Orchestrator   | Flash                                       | `call_rag`, `call_sigaa`, `reply_direct`, `check_status` |
| Planner            | Pro (via planner.py)                        | Gera DAG JSON de workers                                 |
| Synthesis Worker   | Pro                                         | Resposta final grounded no RAG                           |
| LLMFactExtractor   | Flash                                       | Extração de fatos L4                                     |


Adapter: `src/infrastructure/adapters/gemini_provider.py` — SDK `google.genai`, retry exponencial (tenacity), implementa `ILLMProvider`.

---

## 5. Fluxo End-to-End (WhatsApp → Resposta)

```
Evolution API
    │ POST /webhook/evolution
    ▼
FastAPI (200 imediato)
    │ processar_mensagem_whatsapp.delay()
    ▼
Celery [queue: default]
    │ MessageRouter → comandos admin / funnel cadastro / chat
    │ XADD oraculo:stream:messages (durabilidade)
    ▼
processar_mensagem_task
    │ 1. Porteiro: PessoaRepository → PostgreSQL (telefone, status, RBAC)
    │ 2. Lock: lock:msg:{phone} (TTL 90s)
    │ 3. MemoryService.carregar_contexto()
    ▼
CognitiveOS.processar()
    │ Guardrails input
    │ HITL intercept (hitl:session:{sid})
    │ LLMOrchestrator (LN) OU SemanticRouter (comandos !@$)
    │   Router 5 camadas: regex L1 → heurística L2 → regex seeded L3 → KNN L4 → Flash L5
    │ SemanticCache (cosine > 0.92)
    │ Planner → DAG JSON
    │ WorkerRegistry.dispatch() → Celery workers especializados
    │ Poll Redis Stream final_responses (timeout 15s)
    │ Guardrails output
    ▼
EvolutionAdapter.enviar_mensagem()
    │ XACK stream
    ▼
WhatsApp (grupo homologado ALLOWED_GROUP_ID)
```

**Workers registrados** (`registry.py` autodiscovery `worker_*.py`):


| Worker                                                             | Fila       | Função                            |
| ------------------------------------------------------------------ | ---------- | --------------------------------- |
| `rag_search`                                                       | rag_search | Busca híbrida Redis + rerank CPU  |
| `synthesis`                                                        | synthesis  | Gemini Pro → resposta final       |
| `reranker`                                                         | rag_search | Cross-encoder local               |
| `sigaa_`*                                                          | default    | Scraping SIGAA (Playwright agent) |
| `audio_to_text`, `text_to_audio`, `ytb_download`, `insta_download` | media      | Multimídia                        |
| `graph_extractor`                                                  | graph      | Extração grafo institucional      |
| `memory_manager`, `db_connector`, `action`, `greeting`             | default    | Auxiliares                        |


---

## 6. Banco PostgreSQL + Alembic

### 6.1 Engine

- URL: `postgresql+asyncpg://...` via `settings.DATABASE_URL`.
- `NullPool` — evita conflito Celery prefork + asyncpg.
- Migrations: engine async em `migrations/env.py`, URL injetada de `settings` (ignora `alembic.ini`).

### 6.2 Cadeia de Migrations

```
001_observability_tables  (base)
    ↓
002_ltree_institutional   (CREATE EXTENSION ltree, unidades_institucionais, documentos_unidades)
    ↓
003_intents_chunks        (intents_router, document_chunks + seed CALENDARIO/EDITAL/...)
    ↓
004_recria_tabela_pessoas (pessoas — identidade/RBAC)
```

### 6.3 Tabelas Principais


| Tabela                                                             | Responsabilidade                                                 |
| ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| `pessoas`                                                          | Identidade: telefone, matrícula, centro, role, status (Porteiro) |
| `metricas_llm`, `audit_log`, `feedback_avaliacoes`, `monitor_logs` | Observabilidade (migrado do Redis)                               |
| `unidades_institucionais`                                          | Árvore ltree UEMA (Graph RAG prep)                               |
| `documentos_unidades`                                              | Mapeamento chunk ↔ unidade                                       |
| `intents_router`                                                   | Config dinâmica de roteamento (regex, exemplos, k_vector/k_text) |
| `document_chunks`                                                  | Metadados de chunks pós-ingestão                                 |


**ORM:** `src/infrastructure/database/models.py` — enums do domínio (`RoleEnum`, `CentroEnum`, etc.).

**Deploy:** container `migration` executa `alembic upgrade head` antes da API.

---

## 7. Celery — Configuração e Fluxo

### 7.1 App

```27:31:Oraculo/src/infrastructure/celery_app.py
celery_app = Celery(
    "bot_tasks",
    broker  = REDIS_URL.replace("/0", "/1"),
    backend = REDIS_URL.replace("/0", "/2"),
)
```

- `task_acks_late=True`, `worker_prefetch_multiplier=1`.
- Timezone: `America/Sao_Paulo`.

### 7.2 Containers (docker-compose)


| Serviço            | Filas                        |
| ------------------ | ---------------------------- |
| `worker`           | default, admin, notificacoes |
| `worker_rag`       | rag_search                   |
| `worker_synthesis` | synthesis                    |
| `worker_media`     | media                        |
| `worker_graph`     | graph                        |
| `beat`             | agendador                    |


### 7.3 Beat Schedule


| Task                       | Cron    | Ação                                           |
| -------------------------- | ------- | ---------------------------------------------- |
| `beat_nightly_memory_sync` | 02:00   | Sync memória noturna (`ENABLE_NIGHTLY_MEMORY`) |
| `stream_recovery`          | */5 min | Requeue XPENDING do Redis Stream               |
| `worker_sigaa_processos`   | 08:00   | Monitor processos seletivos SIGAA              |


### 7.4 Signals

- `worker_process_init` — pré-carrega reranker ML (CPU).
- `worker_ready` — `recover_pending_messages()` no boot.
- `worker_shutdown` — flush Langfuse spans.

### 7.5 Fluxo de Mensagem (durabilidade)

1. Webhook publica identidade no Stream (`XADD`).
2. Task Celery processa com `stream_id`.
3. Sucesso → `XACK`; falha/worker morto → `XAUTOCLAIM` + requeue (startup + beat).

---

## 8. RAG (Camada L5)

**Ingestão:** `src/rag/ingestion/pipeline.py` → parser (PyMuPDF/RapidOCR) → chunker → embedding Gemini → `salvar_chunk()` Redis.

**Retrieval:** `RAGSearchService.buscar()`:

1. Query transform (Gemini Flash, opcional).
2. `busca_hibrida()` — BM25 + KNN + RRF.
3. Filtros metadata (`ano=2026`, `tipo_doc`).
4. Rerank cross-encoder local (CPU).
5. Registro opcional em `document_chunks` (Postgres).

---

## 9. Infra Docker (resumo)

```
postgres:16        → 172.18.0.40
redis-stack        → 172.18.0.50 (porta 8001 RedisInsight)
api (uvicorn:9000) → FastAPI
worker × 5 + beat  → Celery
evolution_api      → WhatsApp gateway → webhook api:9000
prometheus/grafana → métricas
migration          → alembic upgrade head (one-shot)
```

---

## 10. Pontos de Atenção Técnicos

1. **Modelo Gemini:** `.env.example` usa `gemini-2.5-flash-lite`; README referencia `gemini-2.0-flash`. Código default: `settings.GEMINI_MODEL = "gemini-2.5-flash"`.
2. **Sync vs Async Redis:** funções em `redis_client.py` são síncronas para Celery; async (`redis.asyncio`) só no FastAPI/Cognitive OS.
3. **Grupo WhatsApp:** webhook filtra `ALLOWED_GROUP_ID` — ambiente homologado.
4. **Identidade obrigatória:** usuário não cadastrado/inativo é bloqueado antes de qualquer chamada LLM (economia de tokens).

---

