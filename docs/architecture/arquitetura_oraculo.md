

> **Fonte oficial de arquitetura técnica** (ver `docs/README.md`). Revisado
> em 2026-08-25 (tabelas de filas Celery e model-routing corrigidas contra o
> código real — ver marcações ⚠️ abaixo); **§12 (Hub Admin v2) e a cadeia de
> migrations adicionadas em 2026-08-31.** Para regras de negócio (não
> técnicas), a fonte oficial é `docs/business/regras_negocio_oraculo.md`.

---

# arquitetura_oraculo.md

## 1. Visão Geral

**Oráculo UEMA v5.1** — assistente acadêmico via WhatsApp (Evolution API) + portal admin FastAPI. Pipeline principal: **Router (Supervisor) → Agents → Capabilities** (multi-agente assíncrono sobre Celery + Redis Streams), sucessor do antigo `OracleChain` monolítico e do God Object `CognitiveOS` (decomposto na refatoração Supervisor — ver seção 3).

**Stack:** Python 3.12, FastAPI, Celery, PostgreSQL 16 (SQLAlchemy async), Redis Stack (RediSearch + RedisVL), Google Gemini (`google-genai`), LangChain (embeddings apenas). **Frontend admin:** Jinja2 + HTMX + Alpine.js, sem build step (ver §12).

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

## 3. Arquitetura de Três Camadas (Router → Agents → Capabilities) + Clean Architecture

Desde a refatoração Supervisor (`PLANO_REFATORACAO_SUPERVISOR.md`, Fases 0-7), o antigo God Object `cognitive_os.py` e as três(+uma) implementações concorrentes de roteamento foram decompostos em três pacotes de topo-nível, ortogonais às camadas Clean Architecture:

- **`router/`** — o Supervisor. Único ponto de decisão de "qual agente chamar" (5 camadas: regex → heurística → regex seeded → KNN Redis → fallback LLM). Nunca importa uma classe de agente diretamente — resolve por nome via `agents/registry.py`.
- **`agents/`** — especialistas (`academic_knowledge`, `sigaa`, `conversation`, `tickets`), cada um implementando `BaseAgent`/`AgentContext` (`agents/base.py`) e registrado em `agents/registry.py`. Contém a lógica de decisão/negócio de cada domínio.
- **`capabilities/`** — adapters de negócio atômicos e burros (scraping SIGAA, RAG/embeddings, mensageria Evolution, persistência SQL), consumidos pelos agentes. Não decidem nada.

```
Oraculo/
├── src/
│   ├── api/                    # Apresentação — FastAPI routers, SSE, middleware JWT
│   │   ├── routers/web/hub.py
│   │   ├── routers/admin/
│   │   ├── chain_sse.py
│   │   └── middleware/
│   ├── router/                 # Supervisor: decide o agente, sem IO pesada nem regra de negócio
│   │   ├── supervisor.py        # rotear() — 5 camadas
│   │   ├── llm_fallback.py       # fallback Gemini Flash (classificação + orchestrate)
│   │   ├── contracts.py          # ROTAS_VALIDAS, RouterDecision
│   │   └── gatekeeper.py         # MessageRouter — gate de entrada regex puro
│   ├── agents/                  # Especialistas — decisão de negócio por domínio
│   │   ├── academic_knowledge/    # RAG + synthesis + planning + memory_summarizer
│   │   ├── sigaa/                 # elegibilidade, auth_flow HITL, orquestra scraping
│   │   ├── conversation/          # saudação, onboarding, funil de cadastro
│   │   ├── tickets/               # abertura/consulta de chamados GLPI
│   │   ├── base.py                # BaseAgent (contrato) + AgentContext
│   │   └── registry.py            # AgentRegistry (resolve por nome)
│   ├── capabilities/             # Adapters de negócio atômicos, sem decisão
│   │   ├── sigaa/                  # scraping cru (Playwright)
│   │   ├── rag/                    # retrieval, embeddings, reranker
│   │   ├── messaging/               # Evolution API, Gmail tool
│   │   └── persistence/             # redis_state, admin_config, repositories SQL
│   ├── application/            # Orquestração fina — runtime, workers, pipeline IA
│   │   ├── runtime/             # dispatcher.py (processar/_despachar_workers — ex cognitive_os)
│   │   ├── chain/               # guardrails, planner (whitelist migrada p/ router/contracts.py)
│   │   ├── workers/             # worker_*.py + registry.py (autodiscovery)
│   │   ├── tasks/               # Celery tasks (process_message, ingestion, beat)
│   │   ├── webhook/              # webhook_controller.py
│   │   ├── commands/             # Comandos admin WhatsApp (!status, !cache clear)
│   │   └── use_cases/
│   ├── domain/                 # Entidades, enums, ports (ILLMProvider, vector_store)
│   ├── infrastructure/         # Adapters técnicos genéricos — DB, Redis, Gemini, Evolution
│   │   ├── adapters/           # gemini_provider, evolution_adapter, parsers
│   │   ├── database/           # models.py, session.py (async + NullPool)
│   │   ├── repositories/
│   │   ├── services/           # audio, db_connector, graph_extractor, ingestion (services de infra remanescentes)
│   │   ├── redis_client.py     # índices RedisVL, busca_hibrida (sync p/ Celery)
│   │   ├── celery_app.py
│   │   └── message_stream.py   # Redis Streams journal
│   ├── memory/                 # Ports + adapters da memória (legado + cognitiva)
│   ├── rag/                    # embeddings, ingestion pipeline
│   └── main.py                 # Entry point FastAPI
├── migrations/                 # Alembic (async)
├── templates/hub/              # Jinja2 admin
├── static/                     # JS/CSS hub
├── tests/                      # unit, e2e, eval
├── observability/              # prometheus.yml, alert_rules.yml
├── docker-compose.yml
└── Dockerfile
```

**Fluxo de decisão:** `application/tasks/process_message_task.py` → `router.supervisor.rotear()` (retorna nome do agente) → `agents.registry.resolve(nome)` → `agent.execute(context)` → `application/runtime/dispatcher.dispatch(...)` (monta chain Celery). Ver `PLANO_REFATORACAO_SUPERVISOR.md` para o histórico completo da migração (Fases 0-7).

⚠️ **Correção (2026-08-25, plano de integração LangGraph/REST/MCP, Decisão
01):** o orquestrador real de produção hoje é `application/runtime/dispatcher_langgraph.py`
(`langgraph_experiment/graph.py` — StateGraph), não `dispatcher.py`
diretamente — este último fica como motor interno (chamado por dentro do
outro pras rotas que o grafo ainda não cobre) e caminho de debug/eval
(SSE/`hub.py`/`eval_api.py`). Migração em andamento pra portar
SIGAA/MEDIA_DOWNLOAD/GREETING/CHECK_STATUS pro grafo (branch
`integration/langgraph-rest-mcp`), atrás de `settings.FEATURE_LANGGRAPH_NATIVE_ROUTES`.
Uma reescrita completa desta seção fica pra quando a migração fechar (não
antes) — ver `docs/decisions/0001-langgraph-nao-aprovado-para-main.md`.

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
- `/hub/*` — portal admin (Jinja2 + HTMX/Alpine, sem SPA). Controller
  `src/api/routers/web/hub.py`; ~90 rotas. Redesenho v2 em §12.
- `/api/admin/*` — REST admin (`src/api/routers/admin/admin_api.py`).
- `/health`, `/metrics` — observabilidade. `/static/*` com `Cache-Control:
  no-cache` (`_RevalidatingStaticFiles` em `main.py`).

### 4.2 Redis Stack (multi-tenant por DB)


| Redis DB         | Uso                                                                   |
| ---------------- | --------------------------------------------------------------------- |
| `/0`             | App: vetores RAG, memória L1–L4, locks, HITL, semantic cache, streams |
| `/1`             | Celery broker                                                         |
| `/2`             | Celery result backend                                                 |
| `/1` (Evolution) | Cache Evolution API                                                   |


**Índices RediSearch/RedisVL:**

- `idx:rag:chunks` — prefixo `rag:chunk:`, campos text/tag/vector, taxonomia UEMA (`eixo`, `setor`, `tipo_doc`, `ano`, `campus`, `sistema`, `modulo` — os dois últimos adicionados para o wiki CTIC, ver seção 11). **Migração pendente**: os campos `sistema`/`modulo` existem no `IndexSchema` do código mas o índice em produção ainda não foi recriado (`FT.DROPINDEX idx:rag:chunks DD` + reingestão) — destrutivo, esperando autorização.
- `idx:tools` — prefixo `tools:emb:`, KNN para roteamento semântico.

**Streams:**

- `oraculo:stream:messages` — journal de mensagens (XADD/XACK, recovery XPENDING).
- `oraculo:stream:step_results` / `oraculo:stream:final_responses` — pipeline Cognitive OS.

### 4.3 Gemini (papéis no pipeline)

> ⚠️ **Correção (2026-08-25):** esta tabela descrevia um roteamento de modelo
> por componente (Flash para uns, Pro para outros) que **não existe no
> código**. O código real usa uma única `settings.GEMINI_MODEL` para todos os
> componentes abaixo — não há diferenciação Flash/Pro automática. O erro já
> tinha sido identificado em `docs/historico/analise_custo_real_llm.md` §2 e
> é citado em `notas.md` §9.8/§13; a tabela nunca tinha sido corrigida aqui
> até agora.

| Componente         | Modelo (settings)                                                               | Papel                                                    |
| ------------------ | -------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `GeminiProvider`   | `settings.GEMINI_MODEL` (uma única var, usada por todos os componentes abaixo) | Geração texto, structured output                         |
| Embeddings         | `models/gemini-embedding-001`               | 3072d, ingestão + busca vetorial                         |
| Semantic Router L5 | `GEMINI_MODEL`                       | Classificação de intent (~50 tokens)                     |
| LLM Orchestrator   | `GEMINI_MODEL`                       | `call_rag`, `call_sigaa`, `reply_direct`, `check_status` |
| Planner            | `GEMINI_MODEL` (via `planning.py`)                        | Gera DAG JSON de workers                                 |
| Synthesis Worker   | `GEMINI_MODEL`                         | Resposta final grounded no RAG                           |
| LLMFactExtractor   | `GEMINI_MODEL`                       | Extração de fatos L4                                     |

Além de Gemini, o sistema suporta **DeepSeek e Groq** como providers
alternativos (troca em runtime via `/hub/llm-custo`, sem restart) — ver
`src/infrastructure/adapters/llm_factory.py::get_llm_provider()` e
`src/infrastructure/adapters/openai_compatible_provider.py`. Isso não estava
documentado nesta seção antes; ver `notas.md` §13 para o histórico completo.

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
application/runtime/dispatcher.processar()   # ex CognitiveOS.processar()
    │ Guardrails input
    │ HITL intercept (hitl:session:{sid}) → agents/sigaa/auth_flow.py
    │ router.llm_fallback.orchestrate() (LN) OU router.supervisor.rotear() (comandos !@$)
    │   Supervisor 5 camadas: regex L1 → heurística L2 → regex seeded L3 → KNN L4 → Flash L5
    │ SemanticCache (cosine > 0.92)
    │ Planner (agents/academic_knowledge/planning.py) → DAG JSON
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
001 observability_tables   (base: metricas_llm, audit_log, feedback, monitor_logs)
002 ltree_institutional     (EXTENSION ltree, unidades_institucionais, documentos_unidades)
003 intents_chunks          (intents_router, document_chunks + seed CALENDARIO/EDITAL/...)
004 recria_tabela_pessoas   (pessoas — identidade/RBAC)
005 agentes_catalogo   ·  006 agent_prompts  ·  007 agentes_catalogo (cols)
008 llm_pricing            (preço/1M tokens editável sem rebuild)
── Plataforma orientada a config (Plano A) ──
009 config_dinamica + config_dinamica_historico   (Fase 1: version column, read-repair)
010 route_registry + histórico                    (Fase 2: rota→execução como dado)
011 config_parser                                 (Fase 4: PARSER_PDF_PRIORIDADE/DESABILITADOS)
012 agente_tools                                  (Fase 5: vínculo agente↔capability)
── Camada de nós / Graph Studio ──
013 graph_node_config  ·  014 mcp_servers  ·  015 graph_topology
── Hub v2 (2026-08-31, ver §12) ──
016 tools_catalogo     (ferramenta HTTP/MCP criada pelo painel)
017 llm_providers      (provedor de LLM criado pelo painel; chave fica no .env)
018 canais             (instância de comunicação criada pelo painel)
019 mcp_servers +cols  (auth_tipo/auth_env/latency_ms/last_checked/tools_expostas)
020 config: FEATURE_GRAPH_EXECUTOR_PILOTO   (default false, nada lê no hot path)
```

Toda tabela de config/registro nasce com `tenant_id UUID NULL` + índice único
`(tenant_id, chave/nome)` `NULLS NOT DISTINCT` — precondição de multi-tenancy
(§M de `plataforma_orientada_a_configuracao.md`), sempre NULL hoje.

### 6.3 Tabelas Principais


| Tabela                                                             | Responsabilidade                                                 |
| ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| `pessoas`                                                          | Identidade: telefone, matrícula, centro, role, status (Porteiro) |
| `metricas_llm`, `audit_log`, `feedback_avaliacoes`, `monitor_logs` | Observabilidade (migrado do Redis)                               |
| `unidades_institucionais`                                          | Árvore ltree UEMA (Graph RAG prep)                               |
| `documentos_unidades`                                              | Mapeamento chunk ↔ unidade                                       |
| `intents_router`                                                   | Config dinâmica de roteamento (regex, exemplos, k_vector/k_text) |
| `document_chunks`                                                  | Metadados de chunks pós-ingestão                                 |
| `agentes_catalogo`, `agent_prompts`, `agente_tools`, `llm_pricing` | Catálogo admin-editável de agentes / prompts / tools / preços    |
| `config_dinamica` (+`_historico`), `route_registry` (+`_historico`)| Config e rota→execução como dado, versionadas (Plano A, §M/§N)   |
| `graph_node_config`, `graph_topology`, `mcp_servers`               | Camada de nós: toggle de componente, topologia visual, servidores MCP |
| `tools_catalogo`, `llm_providers`, `canais`                        | **Hub v2**: ferramentas / provedores de LLM / canais criados pelo painel (§12) |


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

> ⚠️ **Correção (2026-08-25):** a fila `notificacoes` nunca existiu no
> `celery_app.py` real — era um erro de transcrição (mesmo erro repetido em
> `README.md`, já corrigido lá também). `worker_graph` foi removido do
> `docker-compose.yml` em 2026-07-31 (fila `graph`/`worker_graph_extractor`
> confirmada sem chamador real em produção); o código do worker continua no
> repo, só o serviço/container foi desligado — reativar é trocar `profiles`
> de volta se algum dia houver uso real.

| Serviço            | Filas          | Status                                        |
| ------------------ | -------------- | ---------------------------------------------- |
| `worker`           | default, admin | ativo                                          |
| `worker_rag`       | rag_search     | ativo                                          |
| `worker_synthesis` | synthesis      | ativo                                          |
| `worker_media`     | media          | ativo                                          |
| `worker_graph`     | graph          | **desligado** desde 2026-07-31 (sem uso real)  |
| `beat`             | agendador      | ativo                                          |


### 7.3 Beat Schedule


| Task                       | Cron    | Ação                                           |
| -------------------------- | ------- | ---------------------------------------------- |
| `beat_nightly_memory_sync` | 02:00   | Sync memória noturna (`ENABLE_NIGHTLY_MEMORY`) |
| `stream_recovery`          | */5 min | Requeue XPENDING do Redis Stream               |
| `worker_sigaa_processos`   | 08:00   | Monitor processos seletivos SIGAA              |


### 7.4 Signals

- `worker_process_init` — pré-carrega reranker ML (CPU).
- `worker_ready` — `recover_pending_messages()` no boot.
- `worker_shutdown` — cleanup do worker (⚠️ correção 2026-08-25: esta linha
  dizia "flush Langfuse spans", mas não há nenhuma referência a Langfuse em
  `src/` — Langfuse foi avaliado e descartado, ver `README.md` §16 e
  `docs/historico/pesquisa_arquitetura_producao.md` §4.5; as chaves
  `LANGFUSE_*` em `.env` são resíduo dessa avaliação, sem consumidor).

### 7.5 Fluxo de Mensagem (durabilidade)

1. Webhook publica identidade no Stream (`XADD`).
2. Task Celery processa com `stream_id`.
3. Sucesso → `XACK`; falha/worker morto → `XAUTOCLAIM` + requeue (startup + beat).

---

## 8. RAG (Camada L5)

**Ingestão:** `src/rag/ingestion/pipeline.py` → parser (PyMuPDF/RapidOCR) → chunker → embedding Gemini → `salvar_chunk()` Redis.

**Retrieval:** `agents/academic_knowledge/service.py` (`RAGSearchService.buscar()`, decisão) + `capabilities/rag/retrieval.py` (mecânica de busca/RRF):

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
5. **Roadmap MCP & Multimodal (2026-08-12, em andamento):** STT (`AudioService.transcribe()`, Gemini áudio nativo) e TTS (`AudioService.synthesize()`, gTTS) já existiam mas eram órfãos — nenhum worker/rota real os acionava. Vision não existia. Plano completo em `C:\Users\User\.claude\plans\claude-md-arquitetura-oraculo-md-soft-moonbeam.md` (auditoria + pesquisa + fases/sprints); geração de imagem foi adiada por decisão do usuário (CPU-only inviabiliza FLUX/SDXL). Fundação de providers implementada (Sprints 1.1 + 1.2): `src/domain/ports/speech_to_text_provider.py`/`text_to_speech_provider.py` (Protocols `ISpeechToTextProvider`/`ITextToSpeechProvider`, espelhando `ILLMProvider`) + `src/infrastructure/adapters/gemini_stt_provider.py`/`gtts_provider.py` (implementações) + `stt_factory.py`/`tts_factory.py` (resolvem `settings.STT_PROVIDER`/`TTS_PROVIDER` → instância singleton). `AudioService` (`src/infrastructure/services/audio_service.py`) agora delega para a factory em vez de falar com Gemini/gTTS direto — `AudioResult`/contrato externo (consumido por `worker_audio_to_text.py`/`worker_text_to_audio.py`) ficou idêntico. Sprint 1.3 (métricas Multimodal em `infrastructure/observability/metrics.py`, alertas ajustáveis em `observability/alert_rules.yml`) e STT ligado de ponta a ponta no fluxo real (Fast-Path `-1` em `dispatcher.py::processar()`, antes de guardrails/HITL — detecta `media_type=="audioMessage"`, baixa via `EvolutionAdapter.baixar_midia_base64()`, despacha `worker_audio_to_text` no worker `media` e faz polling do resultado) já implementados. De quebra, corrigido um bug pré-existente de ordem de import: `router/supervisor.py` registrava métricas Prometheus com os mesmos nomes de `PrometheusMetrics`, sem a proteção `_get_or_create` — colidia dependendo de qual módulo carregasse primeiro em cada processo Celery. Também corrigido: regex de "baixar/baixe vídeo" (só aceitava "buscar") e um guard genérico pra mídia sem legenda (imagem/sticker/vídeo sem texto não vaza mais `message=""` até o RAG).

**Fase 3 (TTS no fluxo real) implementada** — `KokoroTTSProvider` (`src/infrastructure/adapters/kokoro_tts_provider.py`, Apache-2.0; Piper foi descartado por ter virado GPL-3.0-or-later desde a pesquisa da Fase 0), `settings.TTS_PROVIDER` default `kokoro`, modelo baked no `Dockerfile` (não testado em build real ainda). Gatilho opt-in via frase no texto digitado (`_quer_resposta_em_audio()` em `dispatcher.py`); saída via `process_message_task.py::_enviar_resposta_em_audio()`, reaproveitando `enviar_midia_base64` (mesmo padrão do vídeo YouTube). ⚠️ **Correção (2026-08-25):** este parágrafo dizia que o TTS rodava inline no worker `default` por "decisão deliberada de simplicidade" — isso mudou na mesma sessão em que foi escrito: o carregamento do Kokoro chegou a causar OOM real no worker `default` (`mem_limit: 768m`, compartilhado com Playwright/SIGAA), e `_enviar_resposta_em_audio()` foi reescrita para despachar via Celery para o worker `media` (mesmo padrão do STT), com polling e timeout de 45s. Ver `notas.md` §12 e `docs/technical-debt.md` TD-009 (risco de OOM no `worker_media` continua sem confirmação de folga suficiente).

Faltam: Vision (Fases 4-5) e dashboard Grafana (não versionado no repo — painéis vivem no volume `grafana_data`, criados manualmente).

---

## 11. Scraping — Wiki CTIC (DokuWiki)

**Estrutura** (`src/infrastructure/scraping/`):

```
scraping/
├── base_scraper.py       # BaseScraper (Template Method): fetch() → parse() → clean() → to_chunks()
├── scraping_service.py   # Registry + roteamento por domínio + fila + ingestão RAG automática
├── anti_block.py / cache.py / retry.py / queue.py
└── implementations/
    ├── wikipedia_scraper.py
    ├── generic_scraper.py       # GenericHTTPScraper — fallback genérico (qualquer domínio)
    └── dokuwiki/                # Scraper especializado para ctic.uema.br/wiki (DokuWiki)
        ├── scraper.py           # DokuWikiScraper(BaseScraper)
        ├── wikitext.py          # Conversor wikitext DokuWiki → Markdown
        ├── hierarchy.py         # Grafo pai→filho + inferência sistema/modulo
        ├── media.py             # URL de anexos (PDF vira link, não é baixado)
        └── discovery.py         # Descoberta em massa via do=index
```

**Por que não BeautifulSoup sobre HTML renderizado:** o DokuWiki expõe endpoints nativos testados manualmente contra o site real:
- `doku.php?id={page}&do=export_raw` → wikitext-fonte da página, sem nav/sidebar/rodapé.
- `doku.php?do=index` → lista todos os page_ids do wiki (namespaces majoritariamente flat — hierarquia NÃO está no page_id).

`DokuWikiScraper.fetch()` busca `do=export_raw` (força `r.encoding="utf-8"` — o `Content-Type` da resposta não declara charset e o httpx adivinha errado, corrompendo acentos). `parse()` delega a `wikitext.convert()`:
- Headers `======Título======` → `# Título` (DokuWiki inverte: mais `=` = nível MAIS alto).
- Tabelas `^Cab^Cab^` / `|cel|cel|` → tabela Markdown.
- `//itálico//` → `*itálico*`; `**negrito**` já é igual.
- `[[pagina|Rótulo]]` → `[Rótulo](page_id)`, e o `page_id` normalizado (minúsculo, espaço→`_`) alimenta `internal_links`.
- `{{:arquivo.pdf|Rótulo}}` → **não é baixado nem parseado** (decisão do projeto: anexos até agora são slides de apresentação, pouco texto extraível, conteúdo já coberto pela página). Vira link Markdown clicável direto pro arquivo (`media.build_media_url()` monta a URL via `lib/exe/fetch.php?media=...`). Reavaliar só se aparecer um PDF que seja manual/texto denso.
- `{{:imagem.png}}` → vira só `[imagem: nome]` (ignorado, sem visão computacional).

**Hierarquia (`hierarchy.py`):** como o page_id não tem namespace aninhado, a árvore Portal→Sistema→Módulo→Tutorial só existe no grafo de links. Cada página processada registra seus links `[[filho]]` como candidatos a filhos (`registrar_links()`); `resolver_taxonomia()` sobe a cadeia de pais até achar um hub conhecido em `KNOWN_SYSTEM_HUBS` (dict curado manualmente, ex.: `"almoxarifado" → ("SIPAC", "Almoxarifado")`). Sem match, cai no default `"Geral"/"Geral"`. Persistência: `InMemoryGraphStore` (testes) ou `RedisGraphStore` (chave `wiki:parent:{page_id}`, produção).

**Descoberta em massa (`discovery.py`):** `descobrir_paginas()` busca `do=index` uma vez e devolve todos os page_ids do wiki — dispensa crawler recursivo só pra achar páginas. Ainda não agendado no Celery beat (candidato natural, ver `beat_nightly_memory_sync` na seção 7.3 como padrão a seguir).

**Chunking:** `ChunkerFactory.for_doc_type("wiki_ctic")` usa o chunker `markdown` (não `semantic`) — o wikitext convertido já tem headers/tabelas reais, dispensa detecção de breakpoint semântico (mais barato, sem custo extra de embedding).

**Taxonomia no Redis:** `sistema`/`modulo` (calculados por `hierarchy.py`) + `setor="CTIC"`/`tipo_doc="Manual"` (fixos) somam-se à taxonomia UEMA existente (seção 4.2) em `idx:rag:chunks`. `ScrapingService._ingest_to_rag()` propaga esses campos de `document.metadata` para `salvar_chunk()` — **atenção**: chunk-level metadata (`chunk.metadata`, ex. `header_context`) e document-level metadata (taxonomia) são coisas diferentes; um bug real (corrigido) fazia só o primeiro chegar no Redis.

**Decisão de arquitetura — índice único, não banco separado:** avaliado e descartado criar um agente/Redis DB dedicado só para o wiki CTIC. Mantém-se `idx:rag:chunks` único com filtro por tag (`sistema`, `setor`) e o agente `academic_knowledge` existente — alinhado com a prática recomendada de RAG multi-fonte (single collection + metadata filter, "Pool" em vez de "Silo") e com a separação Router→Agents→Capabilities já adotada (scraping de nova fonte = nova capability, não novo agente). Reavaliar só se o volume de uma fonte específica prejudicar p95 de latência — não é o caso hoje.

**Pendências conhecidas** (ver `notas.md` seção 6 para o histórico completo):
1. Migração do schema Redis (`sistema`/`modulo` já no código, índice em produção ainda não recriado — `FT.DROPINDEX idx:rag:chunks DD` + reingestão, destrutivo, esperando autorização).
2. `discovery.py::descobrir_paginas()` não está agendado (Celery beat) — só rodado manualmente/pontual até agora.

**Testes:** `tests/eval/test_ctic_wiki_eval.py` (9 casos) + fixtures reais congeladas em `tests/fixtures/ctic_wiki/*.txt` (baixadas 1x via `do=export_raw`) — cobre conversão wikitext→Markdown, hierarquia, propagação de taxonomia, fidelidade do chunker `markdown`.

---

## 12. Hub Admin v2 (2026-08-31)

Redesenho do portal `/hub/*` de "painel de toggles" para centro de controle
operacional. Sem framework novo: **Jinja2 + HTMX + Alpine.js vendorados**
(`static/js/vendor/`), design system próprio em `static/css/` (tokens +
componentes), zero build step. Fatiado em sprints — o roadmap completo e o
estado de cada sprint vivem no plano
`C:\Users\User\.claude\plans\silly-percolating-ritchie.md`.

**Camada de tradução (glossário).** `templates/hub/_glossario.html` (macros
server-side) + `static/js/core/glossario.js` (`window.Glossario`, espelho para
conteúdo montado via fetch). Converte termo de backend → rótulo humano;
nenhuma página imprime identificador de código, nome de tabela, migration ou
`.py` fora de `data-tech`/tooltip.

**Registries dinâmicos — adicionar pelo painel, não no código.** Postgres é
fonte de verdade; espelho Redis para o caminho quente síncrono (mesmo padrão
de `agentes_catalogo`/`llm_pricing`):

| Recurso | Tabela | Módulo | Espelho Redis | Execução |
|---|---|---|---|---|
| Ferramenta HTTP/MCP | `tools_catalogo` (016) | `src/capabilities/tool_catalog.py` | — | `dynamic_tool_executor.py` (SSRF revalidado na chamada; MCP via sessão de vida curta). `capabilities/registry.py::executar_tool` cai aqui se o nome não está no registro de código. |
| Provedor de LLM | `llm_providers` (017) | `src/infrastructure/adapters/llm_provider_store.py` | `admin:llm_providers` | `llm_provider_registry` lê seed de código + linhas `openai_compat` do espelho → `OpenAICompatibleProvider`. `llm_factory._providers_validos()` virou função (provedor novo é selecionável sem restart). Chave de API **nunca** no banco — `api_key_env` guarda só o nome da variável. |
| Canal (WhatsApp/Evolution) | `canais` (018) | `src/services/channel_store.py` | `admin:canais` | Só "conectar instância existente" (status/QR/webhook via Evolution). **Hot path de envio/recebimento continua lendo `settings.EVOLUTION_*`** — a tabela seeda com os mesmos valores; migrar o hot path é follow-up. |
| Servidor MCP | `mcp_servers` (014 + 019) | `src/graph/mcp_server_registry.py` | — | "Testar Conexão" abre sessão MCP real (mede latência, lista tools); "Sincronizar Ferramentas" insere as tools em `tools_catalogo` (tipo `mcp`). |

**Painéis de infraestrutura** (`src/infrastructure/observability/`):

- `/hub/infra/storage` — `storage_health.py`: Redis `INFO`/`MODULE LIST`/
  `SLOWLOG`/persistência + Postgres (conexões, tamanho, `pg_stat_statements`).
  Ação segura "Recriar índices" (idempotente). **Sem FLUSHDB** — removido após
  incidente (apagava índices RediSearch + chunks de RAG, que não se
  reconstroem sozinhos). Ação destrutiva de infra só se cirúrgica.
- `/hub/infra/search` — `search_health.py`: índices RediSearch (`FT._LIST`/
  `FT.INFO` → campos tipados + params HNSW) + teste de busca híbrida
  interativo (usa o caminho **síncrono** `redis_client.busca_hibrida`; o
  `HybridQuery` do RedisVL emite `FT.HYBRID`, não suportado nesta versão do
  Redis Stack — dívida, ver `technical-debt.md`).
- `/hub/infra/health` — `system_health.py`: agrega circuit breakers dos
  provedores, saúde dos componentes (`node_health.py`), latência MCP, estado
  de Redis/Postgres/filas Celery, flags de laboratório ativas.

**GraphExecutor (MVP).** `src/graph/graph_executor.py` executa uma topologia
de `graph_topology`: valida (reusa `topology_validator`), ordem topológica
(Kahn), passa saída→entrada por aresta, respeita `graph_node_config` (nó
desabilitado = pulado). `dry_run=True` (padrão) **não chama `node.execute()`**
— o botão "Testar" do Graph Studio usa isso para destacar o caminho no canvas.
Execução real atrás de `FEATURE_GRAPH_EXECUTOR_PILOTO` (migration 020, default
`false`) — **nada lê essa flag no pipeline de produção ainda**. Não é o
dispatcher; é o degrau que prova que registry + topologia + toggle executam
um trecho de ponta a ponta.

---

