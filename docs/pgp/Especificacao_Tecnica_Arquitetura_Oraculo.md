# Especificação Técnica e Arquitetura da Solução — Oráculo UEMA

> Documento de projeto (PMBOK) complementar ao Plano de Gerenciamento do
> Projeto — descreve **o que o sistema é e como está organizado hoje**,
> não como o projeto é gerenciado. Todo conteúdo aqui vem de auditoria
> direta do código-fonte, não de suposição — onde algo não pôde ser
> confirmado, está marcado como tal.
>
> Data da auditoria: 01/09/2026. Versão do sistema: Oráculo UEMA v5.1.

---

## 1. Visão Geral da Solução

O Oráculo é um sistema de inteligência artificial composto por módulos
especializados, operado via WhatsApp, que atende a comunidade acadêmica da
UEMA com respostas fundamentadas em conhecimento institucional real
(RAG), suporte a voz e um portal administrativo que permite configuração
sem alteração de código.

**Stack tecnológico confirmado:**

| Camada | Tecnologia |
|---|---|
| Linguagem/Runtime | Python 3.12 |
| API/Web | FastAPI |
| Processamento assíncrono | Celery |
| Banco relacional | PostgreSQL 16 (SQLAlchemy async) |
| Armazenamento vetorial/cache | Redis Stack (RediSearch + RedisVL) |
| Modelo de linguagem principal | Google Gemini (`google-genai`) — multi-provedor dinâmico (ver §4.3) |
| Embeddings | LangChain (uso restrito a isso) |
| Frontend administrativo | Jinja2 + HTMX + Alpine.js, sem build step |
| Gateway de WhatsApp | Evolution API (não oficial) |

O sistema **não é** um framework multiagente autônomo — é um pipeline
determinístico com IA generativa acionada em pontos específicos e bem
definidos. Essa correção de terminologia foi feita nesta mesma rodada de
auditoria (ver §9.1) porque a documentação anterior descrevia o sistema de
forma mais "vistosa" do que o código sustenta.

---

## 2. Auditoria Completa por Pasta

Levantamento direto do repositório (`find`/`grep`, 01/09/2026), pasta por
pasta, com o que cada uma faz e os arquivos mais importantes descritos.
Onde a descrição vem de leitura direta do código (não só do nome do
arquivo), isso está indicado.

### Mapa geral

```
src/
├── api/            Apresentação — FastAPI routers, SSE, middleware
├── router/         Supervisor — único ponto de decisão de rota
├── agents/         Módulos especialistas por domínio
├── capabilities/   Adapters de negócio atômicos — não decidem nada
├── application/    Orquestração — runtime, workers Celery, tasks, use cases
├── domain/         Entidades, enums, ports, regras de permissão
├── infrastructure/ Adapters técnicos — DB, Redis, LLM, Evolution, observabilidade
├── memory/         Ports + adapters de memória (cognitiva + legado)
├── rag/            Embeddings, pipeline de ingestão de documentos
├── graph/          Nós (BaseNode/NodeRegistry) do Graph Studio (Hub v2, piloto)
└── services/       Legado — migração para capabilities/ incompleta (TD-003)
```

---

### 2.1 `src/router/` — Supervisor

5 arquivos: `supervisor.py` (o roteador de 5 camadas, já detalhado em §3-4),
`gatekeeper.py` (`MessageRouter` — gate de entrada regex puro, tem o gap de
segurança do TD-013), `llm_fallback.py` (a única chamada Gemini deste
pacote, usada só na Layer 5), `contracts.py` (`ROTAS_VALIDAS`,
`RouterDecision` — o contrato de dados entre Supervisor e o resto do
sistema).

### 2.2 `src/agents/` — Módulos Especialistas por Domínio

| Subpasta | Conteúdo confirmado |
|---|---|
| `sigaa/` | `service.py` (`SigaaService`), `auth_flow.py` (fluxo HITL de CPF/senha, token no Redis — §7), `eligibility.py` (regras de quem pode acessar o quê) |
| `academic_knowledge/` | `service.py` (`RAGSearchService`), `synthesis.py`, `planning.py`, `query_transform.py`, `memory_summarizer.py`, `prompts.py` — o pipeline completo de RAG + geração de resposta |
| `tickets/` | `service.py` (`TicketService`), `rbac.py` (57 testes), `crud_tool.py`, `ticket_flow.py` |
| `conversation/` | `registration.py` (`RegistrationFunnel` — funil de cadastro sem LLM) |
| raiz | `base.py` (`BaseAgent`/`AgentContext`, contrato não conectado ao runtime — §9.1), `registry.py` (`AgentRegistry`, usado só pelo Hub admin), `bootstrap.py` (registra os 4 agentes no registry) |

### 2.3 `src/capabilities/` — Adapters de Negócio

`registry.py` + `tool_catalog.py` + `dynamic_tool_executor.py` (execução de
ferramentas cadastradas dinamicamente pelo Hub v2 — HTTP e MCP, com
revalidação de SSRF a cada chamada, não só no cadastro). Subpastas:
`persistence/` (repositórios crus — `admin_repository.py`,
`registration_repository.py` com o `salvar_pessoa` do TD-018,
`ticket_repository.py`, `redis_state.py`), `rag/` (`reranker.py`),
`sigaa/` (`browser.py` — scraping via Playwright), `messaging/`
(`evolution_tool.py`), `tools/` (3 tools de dados de estudante:
get/update email/telefone).

### 2.4 `src/application/` — Orquestração

A camada mais numerosa do projeto — 9 subpastas:

- **`runtime/`** — os dois dispatchers (`dispatcher.py` legado,
  `dispatcher_langgraph.py` de produção, já detalhados em §3) e
  `audio_intake.py` (transcrição de voz, roda antes de tudo).
- **`tasks/`** — `process_message_task.py` (entry point real do Celery),
  `ingestion_tasks.py`, `beat_nightly_memory.py` (job agendado),
  `tasks_admin.py`.
- **`workers/`** — 12 workers Celery especializados (`worker_sigaa.py`,
  `worker_rag_search.py`, `worker_action.py`, `worker_synthesis.py`,
  `worker_reranker.py`, `worker_audio_to_text.py`,
  `worker_text_to_audio.py`, `worker_media_download.py`,
  `worker_memory_manager.py`, `worker_graph_extractor.py`,
  `worker_db_connector.py`, `worker_greeting.py`) + `registry.py`
  (`WorkerRegistry`, mesmo padrão do `AgentRegistry` mas para tasks Celery).
- **`use_cases/`** — 10 casos de uso (`sigaa_use_cases.py`,
  `retrieve_context_use_case.py`, `ingest_document_use_case.py`,
  `mcp_lab_use_case.py`, `rest_lab_use_case.py`, `admin_auth.py`,
  `admin_commands.py`, `get_audit_logs.py`, `messages.py`, `user_use_case.py`).
- **`chain/`** — `guardrails.py` (prompt injection/rate limit),
  `planner.py`, `reranker.py`.
- **`commands/`** — 8 comandos administrativos via WhatsApp (`!atualizaremail`,
  `!limpar_cache`, `!crag_score`, `!feedback`, `!maintenance`,
  `!register_admin`, `!sticker`, `!youtube`).
- **`webhook/`** — `webhook_controller.py`, ponto de entrada do Evolution API.
- **`routing/`** — `command_builder.py` (único lugar onde
  `Command.execute(ctx)` — o padrão Protocol — é de fato chamado no
  runtime, diferente do `BaseAgent.execute()` que não é).
- **`graph/`** — pasta existe mas está **vazia** (sem arquivos além de
  `__pycache__`) — provavelmente resquício de uma fase anterior à criação
  de `src/graph/` (§2.9); candidata a remoção.

### 2.5 `src/domain/` — Núcleo de Negócio

`permissions.py` é o arquivo mais importante desta pasta — define a
filosofia de acesso do sistema em 4 níveis (**Público → Estudante →
Servidor/Professor → Admin/CTIC**), com uma regra explícita de "desvio
educado": quando alguém sem permissão pede algo restrito, o sistema não
nega secamente — explica o que falta e oferece caminho (ex.: oferecer
cadastro na hora, em vez de só recusar). `entities/` guarda `admin.py`,
`enums.py`, `identidade.py`. `ports/` define os contratos que a
infraestrutura implementa (`ILLMProvider`, `vector_store_port.py`,
`speech_to_text_provider.py`, `text_to_speech_provider.py`,
`message_gateway.py`, `document_parser.py`, `audit_log.py`,
`cache_lock.py`, `router_storage.py`, `tool_ports.py`) — é a fronteira
formal entre regra de negócio e implementação técnica (Clean Architecture).

### 2.6 `src/infrastructure/` — Adapters Técnicos

A segunda maior pasta do projeto:

- **`adapters/`** — provedores de LLM (`gemini_provider.py`,
  `openai_compatible_provider.py`, `llm_factory.py`, `llm_circuit_breaker.py`,
  `llm_provider_registry.py`/`llm_provider_store.py` — o mecanismo dinâmico
  do Hub v2), voz (`gemini_stt_provider.py`, `gtts_provider.py`,
  `kokoro_tts_provider.py`, `stt_factory.py`/`tts_factory.py`), gateway
  (`evolution_adapter.py`), busca (`redis_vector_adapter.py` — o do TD-015),
  auditoria (`redis_audit_log.py`). Subpasta `parsers/` tem **9 adapters de
  parsing de documento** confirmados: `pymupdf_adapter.py`,
  `docling_adapter.py`, `llamaparse_adapter.py`, `marker_adapter.py`,
  `rapidocr_adapter.py`, `unstructured_adapter.py`, `csv_adapter.py`,
  `txt_adapter.py`, `calendar_llm_adapter.py` — mais opções testadas do que
  as 3 mencionadas na análise de custo; nenhuma delas oficialmente adotada.
- **`database/`** — `models.py` (schema completo, ver Dicionário de Dados
  abaixo), `session.py`, `redis_connection.py`.
- **`observability/`** — `metrics.py`, `pricing.py`, `tracing.py`,
  `search_health.py`, `storage_health.py`, `system_health.py` — o backend
  de tudo que aparece nos painéis `/hub/infra/*` e `/hub/llm-custo`.
- **`repositories/`** — `route_registry_repository.py`,
  `llm_pricing_repository.py`, `agent_catalog_repository.py`,
  `dynamic_config_repository.py`, `observability_repository.py`,
  `pessoa_repository.py`.
- **`scraping/`** — infraestrutura genérica de scraping (`base_scraper.py`,
  `anti_block.py`, `retry.py`, `queue.py`, `cache.py`) e implementações
  (`implementations/dokuwiki/` — o scraper do wiki CTIC, com
  `discovery.py`/`hierarchy.py`/`media.py`/`scraper.py`/`wikitext.py` —,
  `generic_scraper.py`, `wikipedia_scraper.py`).
- **`security/`** — `ssrf_validator.py`, usado no cadastro de ferramentas
  HTTP/MCP e servidores MCP do Hub v2.
- **`services/`** — resquício de serviços de infraestrutura
  (`audio_service.py`, `db_connector_service.py`, `ingestion_service.py`,
  `intent_seeder_service.py`, `media_download_service.py`,
  `graph_extractor_service.py`, `domain_service/gmail_service.py`).
- **Raiz de `infrastructure/`** — `settings.py` (config central, ver
  feature flags abaixo), `celery_app.py`, `redis_client.py`,
  `route_registry.py`, `semantic_cache.py`, `message_stream.py`,
  `dynamic_config.py`, `logging_config.py`, `paths.py`.

**Feature flags confirmadas em `settings.py`** (todas desligadas por
padrão, exceto a primeira):

| Flag | Padrão | Efeito |
|---|---|---|
| `DEV_TEST_NO_DB_WRITE` | **`True`** | Bloqueia escrita real em `pessoas` (causa raiz do TD-018) — é o **valor de fábrica**, não uma flag esquecida ligada por acidente em um ambiente específico |
| `DEV_TEST_SKIP_REGISTRATION` | `False` | — |
| `FEATURE_LANGGRAPH_CELERY_DISPATCH` | `False` | — |
| `FEATURE_LANGGRAPH_NATIVE_ROUTES` | `False` | Controla quais rotas o LangGraph já executa nativamente |
| `FEATURE_GRAPH_EXECUTOR_PILOTO` | `False` | Piloto de execução visual de fluxo (Hub v2, Sprint 8) |
| `FEATURE_REST_PRODUCT` | `False` | — |
| `FEATURE_MCP_PRODUCT` | `False` | — |

> ⚠️ **Achado novo desta auditoria**: `DEV_TEST_NO_DB_WRITE=True` por
> padrão significa que **qualquer ambiente novo, sem `.env` customizado,
> nasce sem gravar cadastro real no banco** — isso é maior que o escopo do
> TD-018 (que só falava dos testes); vale confirmar se isso é intencional
> para todo ambiente de desenvolvimento, ou se deveria ser `False` por
> padrão e só `True` em ambientes explicitamente marcados como teste.

### 2.7 `src/memory/` — Memória Cognitiva

`container.py` (`MemoryService` legado — working + long-term + menu
state), `services/redis_memory_service.py` (`CognitiveMemoryService`, as 5
camadas L1-L5 do §4.1). `ports/` define os contratos
(`working_memory_port.py`, `long_term_port.py`, `menu_state_port.py`,
`fact_extractor_port.py`); `adapters/` implementa em Redis
(`redis_working_memory.py`, `redis_long_term_memory.py`,
`redis_menu_state.py`, `llm_fact_extractor.py` — o único ponto desta pasta
que chama LLM).

### 2.8 `src/rag/` — Pipeline de Ingestão e Embeddings

`embeddings.py`, `query_transform.py` (o módulo com o import quebrado do
TD-007 — não confundir com o `rag/knowledge/query_transform.py`
que é o realmente usado), `calendar_parser.py`, `document_validator.py`.
Subpasta `ingestion/`: `pipeline.py`, `parser_factory.py` (decide qual
adapter de `infrastructure/adapters/parsers/` usar), `chunker_factory.py`.

### 2.9 `src/graph/` — Graph Studio (Hub v2, piloto)

`base_node.py`/`node_registry.py`/`node_config.py`/`node_health.py` (a
Camada 1 do roadmap — fundação de nós), `graph_executor.py` (o motor de
execução do piloto, Kahn topológico), `topology_registry.py`/
`topology_validator.py`/`reference_flows.py`, `mcp_server_registry.py`.
Subpasta `nodes/`: 8 tipos de nó implementados (`llm_node.py`,
`stt_node.py`, `tts_node.py`, `embeddings_node.py`, `parser_node.py`,
`tool_node.py`, `channel_node.py`, `trigger_node.py`) + 2 nós de
laboratório (`mcp_lab_node.py`, `rest_lab_node.py`).

### 2.10 `src/api/` — Apresentação (FastAPI)

`hub.py` (o maior arquivo do projeto — ~90 rotas do portal admin, citado
diversas vezes nesta auditoria), `chain_sse.py` (debug/SSE, um dos
consumidores do dispatcher legado), `dependencies.py`, `schemas.py`.
`routers/admin/`: `admin_api.py` (REST admin), `admin_users_api.py`,
`eval_api.py`. `routers/tools/`: `chunkviz_tools.py`. `middleware/`:
`auth_middleware.py` (JWT), `dev_guard.py`.

### 2.11 `src/services/` — Legado (TD-003, migração incompleta)

4 arquivos que deveriam ter migrado para `capabilities/` e não migraram:
`registration_service.py` (530 linhas, o maior), `channel_store.py`,
`email_service.py`, `evolution_service.py`. Confirma o TD-003 tal como
catalogado — nenhuma mudança feita aqui nesta auditoria.

### 2.12 `migrations/` — Alembic

21 arquivos de migration confirmados, cadeia linear até a `019` (head, ver
§5). Cobre a criação de todas as 19 tabelas do banco (ver Dicionário de
Dados, §2.16).

### 2.13 `templates/hub/` + `static/` — Frontend do Hub v2

22 templates Jinja2 confirmados: `_shell.html` (layout base),
`_glossario.html`/`_styleguide.html` (componentes compartilhados),
`login.html`, `index.html`, `agents.html`, `routes.html`,
`capabilities.html`, `agent_prompt.html`, `users.html`, `audit.html`,
`config.html`, `llm_custo.html`, `chunkviz.html`, `eval.html`, `chat.html`
(debugger de pipeline), `graph-nodes.html`/`graph-studio.html`,
`mcp-servers.html`, `infra-health.html`/`infra-search.html`/
`infra-storage.html`. `static/` organizado em `css/{components,pages}` e
`js/{core,components,pages,vendor}` — sem etapa de build (HTMX + Alpine
vendorizados direto).

### 2.14 `tests/` — Suíte de Testes

674 testes coletados (§9.4), organizados em `unit/` (com subpastas
espelhando `src/`: `agents/`, `api/`, `application/`, `capabilities/`,
`domain/`, `graph/`, `hub/`, `infrastructure/`, `router/`), mais `e2e/`
(inclui os 4 arquivos órfãos do TD-014), `integration/`, `eval/` (avaliação
do wiki CTIC), `debug/`, `fixtures/` (dados de teste, incl.
`ctic_wiki/`).

### 2.15 `langgraph_experiment/`, `rest_lab/`, `mcp_lab/` — Laboratórios

`langgraph_experiment/` (`graph.py`, `nodes.py`, `state.py`) — apesar do
nome "experiment", é o grafo real usado pelo dispatcher de produção (ADR
0001 documenta essa mudança de status). `rest_lab/` e `mcp_lab/` —
mesma estrutura cada um (`clients.py`, `router.py`, `tools.py`,
`run_test.py`) — laboratórios de estudo com camada de Application própria
(`RestLabUseCase`/`McpLabUseCase`), interceptam mensagens com prefixo
próprio (`"rest "` / `"stack "`) antes de qualquer outro processamento.

### 2.16 Dicionário de Dados (PostgreSQL, `models.py`)

19 tabelas confirmadas: `pessoas` (cadastro, o mais central),
`unidades_institucionais` (árvore ltree, prep para Graph RAG),
`documentos_unidades`, `intents_router`, `agentes_catalogo`,
`llm_pricing`, `agent_prompts`, `config_dinamica` +
`config_dinamica_historico`, `route_registry` + `route_registry_historico`,
`agente_tools`, `graph_node_config`, `mcp_servers`, `canais`,
`llm_providers`, `tools_catalogo`, `graph_topology`, `document_chunks`.
Padrão recorrente: tabelas de configuração dinâmica do Hub v2 (`route_registry`,
`config_dinamica`, `llm_providers`, `mcp_servers`, `canais`,
`tools_catalogo`) quase sempre têm uma tabela `_historico` irmã —
auditoria de mudança de configuração embutida no próprio schema.

### 2.17 `observability/`, `grafana/` — Observabilidade

`observability/` tem `prometheus.yml` e `alert_rules.yml` (confirmado).
`grafana/` **existe como pasta na raiz do projeto, mas está vazia** —
confirma com mais certeza a suspeita já registrada em §8: não há dashboard
Grafana versionado, nem placeholder de configuração — a pasta em si não
tem nenhum arquivo.

### 2.18 Outras pastas de raiz

`dados/` e `evolution-api/` existem na raiz do projeto — `evolution-api/`
aparece como não rastreado pelo Git (repositório aninhado, fora do escopo
de commit deste projeto). `docs/` é a documentação (mapeada em
`docs/README.md`). `venv/`/`tmp/`/`__pycache__` são artefatos de ambiente,
sem conteúdo de projeto.

---

## 3. Fluxo de Dados de Ponta a Ponta

```
WhatsApp (usuário)
      │
      ▼
Evolution API (gateway) → Webhook → Fila Celery (assíncrono)
      │
      ▼
SUPERVISOR (router/supervisor.py) — determinístico, 5 camadas:
  L1 regex fixo → L2 heurística → L3 regex dinâmico (Redis)
  → L4 KNN vetorial (Redis) → L5 LLM fallback (minoria dos casos)
      │  (rota decidida)
      ▼
Módulo de serviço do domínio (import estático, fixo por rota):
  RAG/GERAL  → RAGSearchService   (busca vetorial + LLM gera resposta)
  SIGAA      → SigaaService       (scraping + login supervisionado HITL)
  TICKET     → TicketService      (RBAC + CRUD)
  CADASTRO   → RegistrationFunnel (perguntas sequenciais, sem LLM)
      │
      ▼
Capabilities (Redis, Postgres, Evolution, embeddings)
      │
      ▼
Resposta → (se veio de áudio) TTS → WhatsApp (usuário)
```

**Onde a IA generativa entra de fato:** fallback de classificação (L5,
minoria dos casos), síntese de resposta a partir do RAG (o núcleo do
produto), e extração de fatos para memória de longo prazo. Todo o resto é
código determinístico.

**Execução parcialmente migrada para LangGraph:** parte das rotas já
classificadas pelo Supervisor é executada por um grafo (LangGraph) em vez
do dispatcher legado — a decisão de qual rota já usa o grafo vem de
configuração (`route_registry`), não do próprio roteamento. Transcrição de
voz (STT) roda **antes** dessa classificação, como etapa própria, não como
nó do grafo.

---

## 4. Modelo de IA

### 4.1 Arquitetura de Memória (5 camadas, Redis)

| Camada | Nome | Storage | TTL | Função |
|---|---|---|---|---|
| L1 | Conversation | `chat:{session_id}` (List) | 30 min | Últimos 10 turnos (20 msgs) |
| L2 | Operational | `op:{session_id}` (JSON) | 30 min | Estado transitório (última ação, dica de rota) |
| L3 | Task History | `task_hist:{session_id}` (Hash) | 30 min | Último worker/resultado executado |
| L4 | User Memory | `user_mem:{user_id}` (Hash) | 7 dias | Perfil dinâmico extraído por LLM + regex |
| L5 | Knowledge | `idx:rag:chunks` (Redis Stack) | Permanente | RAG híbrido BM25 + HNSW (3072d, `gemini-embedding-001`) |

### 4.2 RAG (Recuperação Aumentada por Contexto)

Busca híbrida (texto + vetorial) sobre índice institucional com taxonomia
própria (eixo, setor, tipo de documento, ano, campus, sistema, módulo) —
os campos de sistema/módulo foram adicionados para cobrir o wiki do CTIC,
mas a migração do índice em produção para incluí-los ainda está pendente
(ação destrutiva, aguardando autorização).

### 4.3 Camada Multi-Provedor de LLM

Provedores não são mais uma lista fixa em código — `llm_factory._providers_validos()`
lê de um registro dinâmico (`llm_provider_registry`), alimentado por
provedores cadastrados via Hub v2 (`llm_providers`, migration 017),
incluindo provedores compatíveis com OpenAI. Fallback hardcoded
(`gemini`, `deepseek`, `groq`) só é usado se o registro dinâmico falhar.

### 4.4 Roteamento (Supervisor)

Cinco camadas, da mais barata para a mais cara: regex hardcoded → heurística
básica → regex semeado dinamicamente via Redis → KNN vetorial (índice
`idx:tools`) → fallback LLM (Gemini Flash) com validação Pydantic. Métricas
de cache-hit por camada e latência expostas via Prometheus.

---

## 5. Infraestrutura

| Componente | Detalhe |
|---|---|
| Orquestração | Docker Compose — múltiplos workers Celery, hoje rodando em máquina local (ver análise de custo, `docs/pgp/PGP_Analise_Custo_Comparativa.md`) |
| Redis (multi-DB) | `/0` app (vetores RAG, memória L1-L4, locks, HITL, cache semântico, streams, checkpoints LangGraph); `/1` broker Celery + cache Evolution; `/2` result backend Celery |
| Banco relacional | PostgreSQL 16, cadeia de migrations Alembic até a 019 (head) |
| Gateway de mensageria | Evolution API — não oficial, com deduplicação por `msg_key_id` |
| Checkpointer LangGraph | `AsyncRedisSaver`, obrigatoriamente na DB `/0` (RediSearch não indexa fora dela) |

---

## 6. Painel Administrativo (Hub v2)

Redesenhado como "centro de controle operacional" (Sprints 0–8, concluído
31/08/2026) — permite configurar sem alteração de código:

- **Ferramentas** dinâmicas (HTTP + MCP), cadastradas via painel
- **Provedores de LLM** dinâmicos, chave sempre referenciada por variável de ambiente (nunca persistida em banco)
- **Canais de comunicação** dinâmicos (conectar instância já existente)
- **Servidores MCP** com validação SSRF obrigatória no cadastro
- **Graph Studio** — editor visual de fluxo, com piloto de execução real (`GraphExecutor`, `FEATURE_GRAPH_EXECUTOR_PILOTO`, desligado por padrão)
- **Páginas de infraestrutura** — armazenamento/cache, busca/índices, custo & saúde do sistema

Stack: HTMX + Alpine.js vendorizados, sem Tailwind (`utilities.css` como
ponte), sem etapa de build.

---

## 7. Segurança e Compliance

| Mecanismo | Onde vive | O que garante |
|---|---|---|
| RBAC | `domain_services/tickets/rbac.py`, `domain/permissions.py` | Controle de acesso por perfil (57 testes cobrindo o domínio) |
| HITL (Human-in-the-Loop) | `domain_services/sigaa/auth_flow.py` | Login no SIGAA supervisionado — senha nunca trafega em texto plano no payload do Celery, é referenciada por token de uso único no Redis |
| Guardrails de entrada | `application/chain/guardrails.py` | Validação/sanitização contra prompt injection e rate limit, executada antes de qualquer processamento |
| Chaves de API | Variável de ambiente | Nunca persistidas em banco — decisão de segurança travada pelo dono do projeto |
| Dados pessoais (LGPD) | Tabela `pessoas`, `audit_log` | Sistema processa dados de estudantes/servidores — exige conformidade em toda captura/armazenamento/uso (ver Restrição R2 no PGP) |

**Gap conhecido e catalogado (TD-013):** o Gatekeeper reescreve
incondicionalmente toda decisão `IGNORE` para `LLM` — os filtros de
segurança de entrada (grupo estranho, texto vazio, comando admin indevido)
não bloqueiam nada hoje, só mudam o motivo registrado em log. Risco ativo,
não corrigido nesta rodada.

---

## 8. Observabilidade

- **Métricas**: Prometheus + `observability/alert_rules.yml`
- **Painéis no Hub v2**: `/hub/llm-custo` (custo/latência/cache por provedor e por assunto, com preço editável por modelo), `/hub/infra/health` (agrega provedores/componentes/MCP/bancos/filas/flags), `/hub/infra/search`, `/hub/infra/storage`
- **Disjuntor de falha (circuit breaker)**: monitora provedores de LLM — gap catalogado (TD-016): ainda ignora provedores dinâmicos cadastrados pelo painel na visão agregada, embora o disjuntor funcione corretamente para eles individualmente
- **Dashboards externos (Grafana)**: não encontrados versionados no repositório — confirmar se existem fora do controle de versão

---

## 9. Estado Real vs. Estado Documentado (achados desta auditoria)

Esta seção existe porque a auditoria encontrou divergências reais entre o
que a documentação técnica anterior descrevia e o que o código faz — e
elas foram corrigidas nesta mesma sessão de trabalho.

### 9.1 "Arquitetura multiagente" — parcialmente incorreto

A documentação descrevia um framework multiagente plugável
(`AgentRegistry`/`BaseAgent.execute(context)`). Auditoria de código
confirmou: esse mecanismo **existe**, mas `AgentRegistry.resolve()` só é
chamado pelo painel administrativo (`hub.py`) — **nenhum consumidor do
pipeline de mensagem real o utiliza**. Os próprios arquivos de serviço
documentam isso em docstring própria. Na prática, cada worker/nó importa e
chama a classe de serviço do domínio diretamente e estaticamente.
Descrição corrigida: **sistema de IA composto por módulos especializados,
orquestrados por um roteador determinístico** — não um framework
multiagente de ponta a ponta. Correção já aplicada em
`docs/architecture/arquitetura_oraculo.md` (seção 3).

### 9.2 "LangGraph orquestra tudo, inclusive voz" — incorreto

O roteamento (classificação) é **sempre** feito pelo Supervisor, nunca
pelo LangGraph. O LangGraph é só um dos dois backends de **execução**
depois de já roteado (o outro é o dispatcher legado), escolhido por
configuração por rota. A transcrição de voz roda antes de tudo isso, como
etapa própria — não é um nó do grafo.

### 9.3 Débito técnico como termômetro de estado real

18 itens catalogados (TD-001 a TD-018, `docs/technical-debt.md`), cada um
com evidência de código e impacto — inclui desde risco operacional ativo
(modelo LLM em versão *preview*, TD-010) até testes desatualizados após
melhorias de segurança (TD-017). Ver matriz de risco completa em
`docs/pgp/PGP_Custo_Detalhado_e_Controle.md` §6.

### 9.4 Suíte de testes — estado medido, não estimado

674 testes coletados, 652 passando, 19 pulados, 3 falhando — as 3 falhas
são débito técnico de teste já catalogado (TD-017/TD-018), não bugs de
produção (medição de 01/09/2026, `pytest tests/unit`).

---

## 10. Documentos Relacionados (não duplicados aqui)

| Assunto | Onde está |
|---|---|
| Débito técnico completo (18 itens) | `docs/technical-debt.md` |
| Decisões arquiteturais (ADRs) | `docs/decisions/` |
| Análise de custo comparativa (LLM, hospedagem, gateway) | `docs/pgp/PGP_Analise_Custo_Comparativa.md` |
| Orçamento, matriz de risco, EVM | `docs/pgp/PGP_Custo_Detalhado_e_Controle.md` |
| EAP, stakeholders, RACI, marcos | `docs/pgp/PGP_Oraculo_secoes_IX_a_custo.md` |
| Regras de negócio (RBAC/HITL para leitura não-técnica) | `docs/business/regras_negocio_oraculo.md` |
