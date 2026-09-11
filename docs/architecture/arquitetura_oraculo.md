> **Fonte oficial de arquitetura técnica.** §1, §3, §4.3, §5 e a cadeia de
> migrations foram **reescritos em 2026-09-09** (item A2 do plano de
> recuperação): descreviam `dispatcher.processar()`, `orchestrate()`, o
> Planner e a flag `FEATURE_LANGGRAPH_NATIVE_ROUTES`, nada disso existindo
> desde a ADR 0008, e paravam na migration 020.
>
> Para **escopo do produto, estado das flags e o que é morto**, a fonte é
> [`docs/ESTADO_ATUAL.md`](../ESTADO_ATUAL.md), que vence em caso de
> contradição.
>
> Para regras de negócio (não técnicas), a fonte é
> `docs/business/regras_negocio_oraculo.md` — §0 reescrita para a liderança
> (item A7); o resto é anexo histórico.
>
> Histórico: revisado em 2026-08-25 (filas Celery e model-routing conferidos
> contra o código); §12 (Hub Admin v2) e a cadeia de migrations em
> 2026-08-31.

---

# arquitetura_oraculo.md

## 1. Visão Geral

**Oráculo UEMA** — assistente da CTIC/UEMA por WhatsApp (Evolution API) mais
um portal admin em FastAPI.

**Pipeline:** webhook → Celery → **orquestrador único**
(`application/orchestration/entrypoint.py`) → `StateGraph` do LangGraph →
nó da rota → resposta. Assíncrono sobre Celery e Redis Streams.

No **v1** quem decide a rota é um **menu determinístico**
(`application/menu/`), não um classificador: navegar o menu custa zero token
e o LLM só entra na síntese de uma resposta de RAG. Escopo, flags e
checklist de produção: [`docs/ESTADO_ATUAL.md`](../ESTADO_ATUAL.md).

"Agents" aqui significa módulos especialistas por domínio, chamados
estaticamente — **não** um framework multiagente plugável. Ver §3.2.

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

## 3. Camadas e organização do código

> Reescrita em 2026-09-09 (item A2). A versão anterior descrevia
> `dispatcher.py`, o Planner e `langgraph_experiment/` — os três foram
> **deletados** pela ADR 0008 — e carregava três caixas de "⚠️ correção"
> empilhadas, cada uma desmentindo a anterior. O texto abaixo descreve o
> código que existe. Escopo do produto: `docs/ESTADO_ATUAL.md`.

O processamento de mensagem tem **um** orquestrador
(`application/orchestration/entrypoint.py`, ADR 0008) sobre um `StateGraph`
do LangGraph cuja topologia é dado (`GraphSpec`). Em volta dele, o código se
organiza em pacotes de topo ortogonais às camadas de Clean Architecture:

- **`application/orchestration/`** — o orquestrador e o grafo. Entrypoint,
  builder, nós, estado, spec, routers de aresta. É aqui que mora toda
  decisão de "para onde esta mensagem vai".
- **`router/`** — o Supervisor (`rotear()`, 5 camadas: regex → heurística →
  regex semeada no Redis → KNN → Gemini Flash) e o `gatekeeper` (pré-filtro).
  **No v1 o Supervisor sai do caminho crítico:** quem decide a rota é o motor
  de menu, e o Supervisor só roda em degradação. Ver §3.1.
- **`application/menu/`** — o motor de menu do v1: menu como dado, posição do
  usuário no Redis, e um resolver que é função pura.
- **`rag/`** — tudo de RAG: embeddings, ingestão e, em `rag/knowledge/`, a
  busca e a síntese que respondem a uma pergunta. É o único domínio no
  caminho crítico do v1.
- **`domain_services/`** — os domínios fora do v1 (`sigaa`, `tickets`,
  `conversation`), desligados por kill-switch.
- **`capabilities/`** — adapters de negócio atômicos, sem decisão: scraping
  do SIGAA, embeddings, mensageria, persistência.
- **`graph_studio/`** — biblioteca de componentes do Hub e sandbox do Graph
  Studio. **Não é o grafo de produção** e não tem consumidor no caminho de
  mensagem (TD-020, congelado).

```
Oraculo/
├── src/
│   ├── api/                      # FastAPI — routers web/admin, SSE, middleware JWT
│   ├── application/
│   │   ├── orchestration/        # ★ ORQUESTRADOR ÚNICO (ADR 0008)
│   │   │   ├── entrypoint.py     #   processar() — o caminho de toda mensagem
│   │   │   ├── builder.py        #   GraphSpec → StateGraph compilado
│   │   │   ├── nodes.py          #   classify_node + um nó por rota
│   │   │   ├── routers.py        #   funções de aresta condicional
│   │   │   ├── spec.py           #   GraphSpec + validate_topology()
│   │   │   ├── node_manifest.py  #   os 16 tipos de nó
│   │   │   ├── loader.py         #   spec ativa: Redis → Postgres → default
│   │   │   └── specs/default.json
│   │   ├── menu/                 # ★ MOTOR DE MENU do v1 (item B2)
│   │   │   ├── spec.py           #   menu como dado + validação + render
│   │   │   ├── resolver.py       #   a decisão, função pura sem I/O
│   │   │   ├── state.py          #   posição no Redis (menu:{session_id})
│   │   │   ├── loader.py         #   menu ativo: Redis → Postgres → default
│   │   │   └── menus/default.json  #  editável por /hub/menu (item C2.5)
│   │   ├── workers/              # worker_*.py (Celery)
│   │   ├── tasks/                # process_message, ingestão, beat
│   │   ├── webhook/              # webhook_controller.py
│   │   ├── commands/             # comandos admin por WhatsApp
│   │   └── use_cases/
│   ├── router/                   # supervisor.py, llm_fallback.py, gatekeeper.py
│   ├── domain_services/          # sigaa/, tickets/, conversation/ — fora do v1
│   ├── capabilities/             # sigaa/, rag/, messaging/, persistence/, tools/
│   ├── graph_studio/             # componentes do Hub + sandbox (NÃO é produção)
│   ├── domain/                   # entidades, enums, ports (ILLMProvider...)
│   ├── infrastructure/           # adapters técnicos, DB, Redis, Celery, observabilidade
│   ├── memory/                   # ports + adapters da memória cognitiva
│   ├── rag/                      # embeddings, ingestão
│   │   └── knowledge/            #   busca + síntese — o RAG do v1
│   └── main.py
├── migrations/                   # Alembic — 027 migrations (ver §6.2)
├── templates/hub/ · static/      # portal admin
├── tests/                        # unit, integration, e2e, eval
├── observability/                # prometheus.yml, alert_rules.yml
└── docker-compose.yml · Dockerfile
```

### 3.1 Quem decide a rota

Duas configurações, e a diferença é o v1 inteiro:

| `FEATURE_MENU_BOT` | Quem decide | Custo de decidir |
|---|---|---|
| `true` (default, v1) | `application/menu/resolver.py`, no passo 0.5 do entrypoint. O grafo é invocado já com `route` e `rota` preenchidos, e `classify_node` não faz nada. | Zero token |
| `false` (rollback) | `router/supervisor.py::rotear()`, chamado pelo `classify_node` dentro do grafo. | Até uma chamada de Gemini Flash por mensagem |

Com o menu ligado, navegar, ler um texto fixo e pedir atendente **nem chegam
ao grafo**. Só uma folha de "tirar dúvida" invoca o grafo, e aí a rota e o
`doc_type` vêm da tecla que o usuário apertou — não de um palpite sobre o
texto dele.

### 3.2 Não existe resolução dinâmica de agente

O mecanismo `AgentRegistry` / `BaseAgent.execute(AgentContext)` **foi
removido em 2026-09-10**. Ele prometia que "o roteador nunca importa uma
classe de agente, sempre resolve por nome" — e isso nunca aconteceu:
`resolve()` só era chamado pelo painel `/hub/agents` e por uma checagem de
nome no `route_registry`, ambos querendo apenas a lista de nomes válidos.

Um Protocol, um registro em memória, um bootstrap assíncrono e quatro classes
adaptadoras para entregar quatro strings custavam mais do que rendiam, e o
custo era concreto: a arquitetura *parecia* ter um framework multiagente
plugável, e a documentação repetiu isso por meses.

No lugar ficou `domain/agentes.py` — o conjunto fechado de nomes e a
descrição de partida. "Agente" aqui significa **um domínio de conhecimento
que pode ser ligado ou desligado inteiro** pelo painel, não um processo
autônomo.

Cada nó ou worker importa a classe de serviço do domínio direto:
`worker_sigaa.py` → `SigaaService`; o RAG → `RAGSearchService` e
`SynthesisService`; `process_message_task.py` → `RegistrationFunnel`.

**Descrição honesta da arquitetura:** roteador determinístico (menu no v1,
Supervisor antes dele) + módulos de serviço especializados chamados
estaticamente por rota. Não há agente autônomo decidindo chamadas de
ferramenta em loop. O LLM generativo entra em três pontos, e no v1 só o
segundo continua no caminho crítico:

1. Fallback de classificação (camada 5 do Supervisor) — **fora do v1**.
2. Síntese da resposta a partir dos chunks do RAG.
3. Extração de fatos para a memória de longo prazo (job noturno, opcional).

O `AgentRegistry` continua correto para o que faz hoje: registro central para
o painel admin. O que não existe é a ponte entre ele e a execução.

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

> Reescrita em 2026-09-09 (item A2). A versão anterior tinha uma caixa de
> correção admitindo que a tabela descrevia um roteamento de modelo que não
> existia. A tabela abaixo é o código.

Um único modelo (`settings.GEMINI_MODEL`, hoje `gemini-2.5-flash`) atende
todos os componentes. Não há escolha automática Flash/Pro por papel. O que
existe é `LLM_MODEL_FAST`: quando preenchido, os passos baratos e de alto
volume usam esse modelo, e a **síntese da resposta ao aluno sempre usa o
modelo forte**. Vazio (o default) significa "o mesmo para tudo".

| Componente | Modelo | Papel | No v1 |
|---|---|---|---|
| Embeddings | `models/gemini-embedding-001` | 3072d, ingestão e busca vetorial | Ativo |
| Síntese (`SynthesisService`) | `GEMINI_MODEL` | Resposta final, ancorada nos chunks do RAG | **Ativo — é a única chamada de LLM do caminho crítico** |
| Supervisor, camada 5 | `GEMINI_MODEL` (ou `LLM_MODEL_FAST`) | Classificação de intenção, ~50 tokens | **Fora do caminho** — quem roteia é o menu |
| `QueryTransformService` (Flash) | `GEMINI_MODEL` (ou `LLM_MODEL_FAST`) | Reescreve a query antes da busca | **Desligado** (`FEATURE_QUERY_TRANSFORM_LLM=false`, item B6) |
| `LLMFactExtractor` | `GEMINI_MODEL` (ou `LLM_MODEL_FAST`) | Extração de fatos para a memória L4 | Só no job noturno, opcional |

Componentes que a tabela antiga listava e **não existem mais**: o `Planner`
(gerava um DAG JSON de workers) e o `LLM Orchestrator` (`orchestrate()`),
ambos deletados pela ADR 0008 Fase 3.

Além do Gemini, há **DeepSeek e Groq** como providers alternativos, trocáveis
em runtime por `/hub/llm-custo` sem restart — `llm_factory.py::get_llm_provider()`
e `openai_compatible_provider.py`. Providers adicionais compatíveis com a API
da OpenAI podem ser cadastrados pelo painel (ADR 0007), sem deploy.

Adapter: `adapters/gemini_provider.py` — SDK `google.genai`, retry exponencial
com tenacity, implementa `ILLMProvider`. **Não chame `genai.Client` direto:**
`get_llm_provider()` é o único ponto que grava telemetria em `metricas_llm`.

---

## 5. Fluxo End-to-End (WhatsApp → Resposta)

> Reescrito em 2026-09-09 (item A2). O fluxo anterior descrevia
> `dispatcher.processar()`, `orchestrate()`, o Planner e o polling do stream
> `final_responses` — nada disso existe desde a ADR 0008.

```
Evolution API
    │ POST /webhook/evolution
    ▼
FastAPI — responde 200 imediatamente
    │ processar_mensagem_whatsapp.delay()
    ▼
Celery [fila: default]
    │ XADD oraculo:stream:messages (durabilidade)
    ▼
process_message_task
    │ 1. Porteiro: PessoaRepository → Postgres (telefone, status, RBAC)
    │ 2. Lock por telefone: lock:msg:{phone}, TTL 90s
    │ 3. MemoryService.carregar_contexto() — histórico L1 + fatos L4
    ▼
application/orchestration/entrypoint.py::processar()   ← ORQUESTRADOR ÚNICO
    │ -3. Sessão em atendimento humano? (handoff:session:*) → silêncio
    │ -2. Fast-path de áudio (STT)
    │ -1. Fast-path de mídia sem legenda · labs REST/MCP
    │  0a. Guardrails de entrada · HITL legado do SIGAA
    │  0b. Retomada de interrupt() pendente (funil de ticket/CRUD)
    │  0.5 MOTOR DE MENU (v1) ─┬─ menu ou texto fixo → responde aqui, 0 token
    │                          ├─ handoff → grafo, nó human_handoff
    │                          └─ pergunta → grafo, rota e doc_type já decididos
    ▼
StateGraph (builder.py, topologia = GraphSpec ativa)
    │ classify_node — não faz nada quando o menu já decidiu;
    │                 classifica + aplica circuit-breaker quando não
    ▼
nó terminal da rota
    │ rag → busca híbrida (2× FT.SEARCH + RRF + rerank) → síntese
    │ greeting · human_handoff · check_status · media_download · sigaa
    │ funis de ticket/CRUD (nós travados, com interrupt())
    ▼
entrypoint anexa a tela "Isso ajudou?" quando a resposta veio do RAG
    │ Guardrails de saída
    ▼
EvolutionAdapter.enviar_mensagem() → XACK → WhatsApp
```

**Onde o LLM entra:** só no nó `rag`, na síntese. Todo o resto do diagrama é
determinístico.

**Workers Celery e suas filas** (containers em `docker-compose.yml`):

| Worker | Fila | Função | Estado |
|---|---|---|---|
| `worker_rag_search` | `rag_search` | Busca híbrida + rerank | Ocioso — `FEATURE_LANGGRAPH_CELERY_DISPATCH=false`, o RAG roda in-process |
| `worker_synthesis` | `synthesis` | Síntese da resposta | Ocioso, mesmo motivo |
| `worker_sigaa` | `default` | Scraping do SIGAA (Playwright) | Fora do v1 |
| `worker_audio_to_text`, `worker_text_to_audio` | `media` | STT e TTS | Ativos |
| `worker_media_download` | `media` | Download de mídia | Fora do v1 |
| ~~`worker_graph_extractor`, `worker_db_connector`, `worker_memory_manager`, `worker_reranker`~~ | — | — | **Deletados** em 2026-09-09 (item A8b) — não tinham chamador nenhum |

O worker `graph` (fila `graph`) está desligado desde 2026-07-31.

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
── Orquestrador único (ADR 0008) ──
021 graph_topology_gatilho          (coluna de gatilho na topologia do Graph Studio)
022 route_registry: ESCALAR_HUMANO  (rota + nó terminal human_handoff, Fase 2)
023 orquestrador_unico_langgraph    (Fase 3: owner='langgraph' em TODAS as rotas;
                                     remove route_registry.planner_steps — o DAG do
                                     Planner — e a flag FEATURE_LANGGRAPH_NATIVE_ROUTES)
024 graph_spec                      (Fase 5: topologia do grafo como dado, com
                                     versão, histórico e revert)
── Bot de menu (v1) ──
025 menu_config                     (o menu do bot como dado: telas, opções e
                                     respostas prontas, editáveis por /hub/menu,
                                     com versão, histórico e revert)
026 config_rate_limit               (RATE_LIMIT_MSGS / RATE_LIMIT_WINDOW_S na
                                     config dinâmica — limite por pessoa
                                     ajustável sem reiniciar)
027 telemetria_llm                  (metricas_llm ganha tokens de cache/
                                     reasoning, custo aberto por componente em
                                     numeric, origem do preço, status do custo
                                     e request_id idempotente)
```

`alembic upgrade head` deve chegar em **027**. A listagem anterior parava em
020, o que dava a impressão de que as migrations da ADR 0008 não existiam.

`menu_config` nasce **vazia**, de propósito: enquanto ninguém editar o menu
pelo painel, vale o JSON embutido em `application/menu/menus/default.json`.
Um seed ali viraria uma "versão 1 oficial" competindo com o arquivo a cada
atualização de código.

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

**Retrieval:** `rag/knowledge/service.py` (`RAGSearchService.buscar()`, decisão) + `capabilities/rag/retrieval.py` (mecânica de busca/RRF):

1. Query transform (Gemini Flash, opcional).
2. `busca_hibrida()` — BM25 + KNN + RRF.
3. Filtros metadata (`ano=2026`, `tipo_doc`).
4. Rerank cross-encoder local (CPU).
5. Registro opcional em `document_chunks` (Postgres).

### 8.1 Como um trecho é guardado — e por que o formato importa

> Achado de 2026-09-11, durante a primeira ingestão real da wiki da CTIC.
> Registrado com detalhe porque o sintoma não apontava para a causa, e a
> conta que resolveu é reaproveitável.

**Cada trecho é um HASH do Redis**, com o texto, a taxonomia e o embedding
como **float32 binário** (3072 dimensões × 4 bytes = 12.288 bytes exatos). O
índice `idx:rag:chunks` declara `storage_type: hash`.

#### O sintoma

A ingestão da wiki (1383 páginas) foi ao ar e, por volta da página 600, o
Redis começou a recusar toda escrita:

```
redis.exceptions.OutOfMemoryError:
command not allowed when used memory > 'maxmemory'
```

O efeito foi bem além da ingestão: **o bot parou**. Sem escrita no Redis não
há posição de menu, não há cache, não há checkpoint de conversa. Uma tarefa de
carga de conteúdo derrubou o produto.

Antes disso, um sintoma mais sutil já tinha aparecido e sido mal interpretado:
o índice de busca "esvaziou" sozinho depois de um restart. A leitura correta é
que a pressão de memória com a política `volatile-lru` derruba o que tem
prazo de validade, e o índice ficou inconsistente com o keyspace.

#### A causa

O `salvar_chunk` gravava o documento com `r.json().set()`, e o embedding ia
como **array JSON de 3072 números em texto**. Cada número vira algo como
`0.023841857910156250` — cerca de 20 caracteres. O vetor sozinho ocupava
aproximadamente 60 KB de texto para representar 12 KB de dados.

Medido no ambiente real, com amostragem completa (`MEMORY USAGE ... SAMPLES 0`;
a amostragem padrão subestima HASH com campos de tamanhos muito diferentes):

| formato | por trecho | wiki inteira (~16.500 trechos) |
|---|---|---|
| JSON (antes) | **81,2 KB** | ~1,34 GB |
| HASH binário (agora) | **15,5 KB** | ~256 MB |

**Redução de 81%.** O índice vetorial em si nunca foi o problema: ele já
guardava float32 e custava ~98 MB para 7.169 trechos. O desperdício estava
inteiro no documento de origem.

#### Por que a troca é segura

A dúvida legítima era se a busca do menu sobreviveria: ela faz KNN vetorial
**com filtro de TAG** (`doc_type`, e `sistema` para separar SIGAA de SIPAC).
Antes de migrar, os dois formatos foram medidos lado a lado em índices
paralelos, com a mesma consulta — `(@doc_type:{wiki_ctic} @sistema:{SIPAC})
=>[KNN 5 @embedding $v]` — e devolveram resultados equivalentes. O RediSearch
trata vetor binário em HASH como cidadão de primeira classe.

#### O que mudou junto

* `maxmemory` do Redis: 768 MB → **1536 MB**, e o `mem_limit` do container
  1 GB → 2 GB. Não é o conserto — é a folga que faltava depois dele. Com o
  formato antigo, aumentar memória só adiaria o mesmo estouro.
* `volatile-lru` foi **mantida**. A política está certa: sob pressão, evictar
  só o que tem TTL protege os trechos do RAG e os checkpoints. Foi ela que
  transformou o problema num erro explícito em vez de corrupção silenciosa.

#### A lição, em uma frase

Representação de vetor não é detalhe de implementação: em base vetorial, é a
diferença entre caber e não caber. E `MEMORY USAGE` sem `SAMPLES 0` mente
sobre HASH com campos heterogêneos — foi o que quase fez esta análise
subestimar o ganho.

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

> ⚠️ **Esta seção é um registro cronológico, não uma descrição do sistema
> atual.** Vários itens abaixo citam `dispatcher.py::processar()` — o arquivo
> foi deletado pela ADR 0008 e o que ele fazia hoje está em
> `application/orchestration/entrypoint.py`. O achado de cada item continua
> válido; só o endereço mudou. Para o estado atual, ver §3, §5 e
> [`docs/ESTADO_ATUAL.md`](../ESTADO_ATUAL.md).

1. **Modelo Gemini:** hoje os três lugares concordam em `gemini-2.5-flash`
   (default de `settings.py`, `.env.example`, seed da migration 009). O item
   original apontava divergência entre `.env.example` (`gemini-2.5-flash-lite`)
   e o README (`gemini-2.0-flash`); foi resolvido. Falta só conferir o valor
   gravado em runtime em `config_dinamica`, que tem precedência — ver TD-010.
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

