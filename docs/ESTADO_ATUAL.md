# Oráculo — Estado Atual

> **Este é o documento único de verdade sobre o que o sistema é hoje.**
> Última revisão: 2026-09-09 · Branch `hub/redesign-htmx-infra` · HEAD `e527000`.
>
> Se qualquer outro documento contradisser este aqui, **este vence** e o outro
> está desatualizado. Regra de manutenção no fim do arquivo.
>
> Escopo: (1) o que o v1 é; (2) a arquitetura real de uma mensagem; (3) o que
> está em produção, o que é laboratório e o que está morto; (4) o estado real
> de cada flag; (5) o checklist para ir ao ar.

---

## 1. O que é o v1 — um bot de menu

O v1 do Oráculo é um **bot de WhatsApp guiado por menu numérico**, não um agente
autônomo e não um "FAQ agent genérico".

- O usuário digita um número → **rota determinística**, sem LLM.
- O LLM só é chamado quando o usuário chega numa folha de "tirar dúvida"
  (RAG: uma chamada de síntese, cacheável).
- Navegar menu, ler texto fixo e pedir atendente custam **zero token**.

### Base de conhecimento do v1

Só duas taxonomias, ambas confirmadas:

| `doc_type` | Conteúdo | Rota |
|---|---|---|
| `wiki_ctic` | Wiki da CTIC — SIGAA, SIPAC, SIGUEMA, senha, e-mail, Wi-Fi, procedimentos | `WIKI` |
| `contatos` | Telefones e setores da UEMA | `CONTATOS` |

> **Atenção ao nome:** a rota `WIKI` usa `doc_type="wiki_ctic"`, não `"wiki"`.
> Está assim no seed do `route_registry` (migration 010) e é o valor que o
> índice `idx:rag:chunks` precisa conter.

### O que o bot faz

Navegar menu · responder dúvida com RAG · mostrar texto fixo (senha, Wi-Fi,
contato da CTIC) · encaminhar para atendente humano.

### O que fica fora do v1

Calendário acadêmico · edital/PAES · consulta de notas e histórico no SIGAA
(exige login por conversa) · abertura de chamado formal no GLPI (coberto por
"falar com atendente") · download de mídia · atualização de cadastro (CRUD).

Cada um volta depois como **um item de menu + um `doc_type`**, sem retrabalho
de arquitetura. Ficam desligados por kill-switch, não deletados.

### Público e linguagem

Servidor, técnico e aluno. Linguagem acessível para todas as idades.

---

## 2. Arquitetura real de uma mensagem

```
Evolution API (WhatsApp)
  → webhook FastAPI (src/application/webhook/)
  → Porteiro (Postgres) + lock por telefone (Redis, lock:msg:{phone}, TTL 90s)
  → Celery, fila default
  → router/gatekeeper.py (pré-filtro)
  → application/orchestration/entrypoint.py   ← ORQUESTRADOR ÚNICO (ADR 0008)
      → MOTOR DE MENU (passo 0.5, v1) ─┬─ menu/texto fixo → responde AQUI, sem grafo
                                       ├─ handoff  → grafo, nó human_handoff
                                       └─ pergunta → grafo, rota já decidida
  → StateGraph compilado por orchestration/builder.py a partir da GraphSpec ativa
      → classify_node   ← não faz nada quando o menu já decidiu; classifica e
                           aplica o circuit-breaker por agente quando não
      → nó terminal da rota (rag / greeting / human_handoff / ...)
  → infrastructure/adapters/llm_factory.py (só quando a rota precisa de LLM)
  → Evolution API → WhatsApp
```

### O que roda ANTES do grafo, no `entrypoint.py`

Nesta ordem, e por um motivo: precisa valer tanto para mensagem nova quanto
para o resume de um funil, e o resume não reexecuta nós anteriores.

1. Mute de atendimento humano (`handoff:session:*`).
2. Fast-path de áudio (STT).
3. Fast-path de mídia sem legenda.
4. Fast-paths dos laboratórios REST e MCP.
5. Guardrails de input + continuação do HITL legado do SIGAA.
6. Retomada de `interrupt()` pendente (funil de ticket/CRUD) — um funil em
   andamento tem prioridade sobre o menu.
7. **Motor de menu (passo 0.5).** Três dos quatro desfechos não invocam o
   grafo nem tocam num LLM.
8. Mensagem nova → invoca o grafo, já com a rota decidida.

### O que roda DENTRO do grafo

Classificação e circuit-breaker são o próprio `classify_node`, não código
anterior ao grafo (ADR 0008 Fase B). Toda rota é um nó — não existe mais
delegação condicional fora do grafo.

### A topologia é dado, não código

`GraphSpec = { version, entrypoint, nodes[], edges[] }`, resolvida na ordem
**Redis → Postgres (`graph_spec`) → `specs/default.json`** embutido. Hoje a
spec default tem 16 nós: `classify`, os 6 terminais (`rag`, `check_status`,
`greeting`, `media_download`, `sigaa`, `human_handoff`) e os 9 nós travados dos
funis de ticket e CRUD.

Detalhe completo em [`architecture/graph-studio.md`](architecture/graph-studio.md).

### O que muda no v1 (bot de menu)

| Peça | Antes | No v1 | Estado |
|---|---|---|---|
| Roteamento | `supervisor.rotear()` — 5 camadas, L5 é o Gemini Flash | Máquina de estado de menu no Redis, resolvida no passo 0.5 do `entrypoint.py` | **Feito** (B2) |
| Camada L5 (LLM) | Ativa, paga por mensagem não coberta por regex | Não roda: o grafo chega com `route` preenchido e `classify_node` não faz nada | **Feito** (B2) |
| Menu | Não existia | `application/menu/` — dado, validado, com espelho Redis | **Feito** (B2) |
| Tela "Isso ajudou?" | Não existia | Anexada a toda resposta de RAG, determinística | **Feito** (B6) |
| Reescrita de query por LLM | 2ª chamada de LLM em quase toda resposta | Desligada; só as estratégias locais | **Feito** (B6) |
| Rotas ativas | 12 | 4: `WIKI`, `CONTATOS`, `GREETING`, `ESCALAR_HUMANO` | Código **feito** (B3); falta a linha `ROTAS_ATIVAS` no `.env` |
| Conteúdo real dos menus | — | Textos da CTIC no lugar dos placeholders | **Aberto** (B4) |
| Menu editável pelo painel | — | Tabela `menu_config` (migration 025) + página `/hub/menu`, com versão, histórico e reverter | **Feito** (C2.5) |
| Funis ticket/CRUD | Nós travados no grafo, com `interrupt()` | Inalcançáveis pelo menu; kill-switch é defesa extra | **Aberto** (B3) |

O grafo LangGraph continua. O que saiu é o classificador LLM do caminho
crítico. **Resultado líquido: menos código do que existe hoje.**

### Onde o motor de menu vive

| Peça | Arquivo |
|---|---|
| Menu como dado + validação + renderização | `src/application/menu/spec.py` |
| Conteúdo do menu v1 | `src/application/menu/menus/default.json` |
| Posição do usuário (Redis, `menu:{session_id}`, TTL 30 min) | `src/application/menu/state.py` |
| A decisão (função pura, sem I/O) | `src/application/menu/resolver.py` |
| Menu ativo: Redis → Postgres → default | `src/application/menu/loader.py` |
| Edição pelo painel | `/hub/menu`, `templates/hub/menu.html`, `repositories/menu_config_repository.py` |
| Passo 0.5 e tela de feedback | `src/application/orchestration/entrypoint.py` |
| Testes | `tests/unit/application/test_menu_resolver.py`, `test_menu_entrypoint.py` |

O invariante de custo é testado, não prometido: em `test_menu_entrypoint.py` o
grafo é um dublê que **falha o teste se for invocado** numa navegação de menu.
Como toda chamada de LLM acontece dentro do grafo, navegar não pode gastar
token sem quebrar a suíte.

---

## 3. Produção · Laboratório · Morto

### 3.1 Em produção, com testes

| Peça | Onde |
|---|---|
| Pipeline WhatsApp fim a fim | `src/application/webhook/`, `src/application/tasks/` |
| Orquestrador único + grafo | `src/application/orchestration/` |
| Motor de menu do v1 (roteamento determinístico) | `src/application/menu/` |
| Classificação por Supervisor (5 camadas) — **só em degradação/rollback** | `src/router/supervisor.py`, `llm_fallback.py` |
| Armazenamento dos trechos do RAG | HASH com vetor float32 binário (15,5 KB por trecho). Era JSON com o vetor em texto, 81 KB — estourou o Redis e parou o bot em 2026-09-11. Ver TD-030 e `architecture/arquitetura_oraculo.md` §8.1 |
| RAG híbrido in-process | `redis_client.py::busca_hibrida` (2× `FT.SEARCH` HNSW+BM25 + RRF manual + rerank CrossEncoder), `rag/knowledge/service.py` |
| Síntese da resposta | `rag/knowledge/synthesis.py` |
| Cache semântico por rota | `infrastructure/semantic_cache.py` |
| Troca de provider LLM em runtime | `adapters/llm_factory.py` |
| Telemetria de custo | Postgres `metricas_llm` → `/hub/llm-custo` |
| HITL — 3 mecanismos legítimos | `interrupt()` (ticket/CRUD), state-machine Redis (SIGAA), handoff terminal (`handoff:session:*`) |
| Registries dinâmicos | `route_registry`, `graph_spec`, `config_dinamica`, `llm_providers`, `tools_catalogo` |
| Portal admin | ~15 páginas em `/hub/*`, incluindo `/hub/infra/*` |

**Busca híbrida — cuidado com a doc antiga:** o caminho vivo é
`redis_client.busca_hibrida()`, com RRF calculado à mão. O `RedisVLVectorAdapter`
(que emitiria `FT.HYBRID`) está **morto** — ver §3.3. Onde a documentação disser
"busca híbrida via RedisVL `HybridQuery`", ela está errada.

### 3.2 Laboratório / congelado — existe, não afeta produção

| Peça | Situação |
|---|---|
| `src/graph_studio/` (11 módulos: `base_node`, `node_registry`, `graph_executor`, `topology_*`, `mcp_server_registry`, `nodes/`) | **Segundo motor de grafo, sem consumidor de produção. CONGELADO em 2026-09-09 (C2):** banner no pacote e no `graph_executor`, aba do Hub renomeada para "Sandbox (experimental)" com aviso de que nada ali entra em produção, e `/hub/graph-nodes` agora diz que é catálogo de consulta e que o toggle não muda o comportamento do bot. Doc: [`architecture/graph-studio-sandbox.md`](architecture/graph-studio-sandbox.md). TD-020. |
| Tabelas `graph_topology`, `graph_node_config` | CRUD pelo Hub, zero efeito no fluxo de mensagem. Congeladas. |
| Tabela `menu_config` (migration 025) | **Em produção.** Nasce vazia; enquanto estiver assim, vale o menu embutido em `menus/default.json`. A primeira edição por `/hub/menu` cria a linha. |
| Tabelas `mcp_servers`, `tools_catalogo`, `canais` | Registry real, mas o hot path de mensagem continua lendo `settings.EVOLUTION_*` (ADR 0007). |
| `rest_lab/`, `mcp_lab/` | Laboratórios de pesquisa. Roteamento por prefixo de comando, nunca LLM (ADR 0004). |
| Tracing / Jaeger | Código real, desligado por default (`ENABLE_TRACING=false`). |
| Ticket / GLPI | Sem integração real — grava JSON. Fora do v1. |

### 3.3 Reorganização de 2026-09-10 — o `src/agents/` deixou de existir

Pedido do dono: o layout deveria refletir "supervisor + grafo", não um
framework de agentes que nunca existiu.

**Não foi uma remoção.** Apagar a pasta apagaria o RAG, que é o produto. O que
saiu foi a abstração; o que era serviço mudou de endereço.

| Antes | Agora | Por quê |
|---|---|---|
| `agents/base.py`, `registry.py`, `bootstrap.py` + as 4 classes `*Agent` | `domain/agentes.py` (≈30 linhas) | O `AgentRegistry` prometia resolução dinâmica e nunca resolveu nada: só o painel e uma checagem de nome o usavam, ambos querendo a lista de nomes. |
| `agents/academic_knowledge/` | `rag/knowledge/` | É RAG. Fica junto de embeddings e ingestão. |
| `agents/sigaa/`, `tickets/`, `conversation/` | `domain_services/` | Domínios fora do v1, desligados por kill-switch. |
| `tests/unit/agents/` | `tests/unit/rag/knowledge/`, `tests/unit/domain_services/` | O espelho de `src/` foi mantido. |

O painel `/hub/agents` continua funcionando: a lista de nomes vem de
`domain/agentes.py` e a descrição editável de `agentes_catalogo`, que é de
onde ela já vinha.

**"Agente" agora significa** um domínio de conhecimento que pode ser ligado ou
desligado inteiro. Não é processo autônomo, não decide ferramenta em loop.

---

### 3.4 Removido em 2026-09-09 (item A8)

Confirmado item a item com o dono antes de apagar. A suíte foi rodada antes e
depois.

| Removido | Por quê |
|---|---|
| `worker_graph_extractor`, `worker_db_connector`, `worker_memory_manager`, `worker_reranker` + `db_connector_service.py`, `graph_extractor_service.py` | Zero chamadores no repositório, mas importados no boot de todo worker. Saíram também do `include` do `celery_app.py`. TD-022. |
| `RedisVLVectorAdapter`, `RetrieveContextUseCase`, `IngestDocumentUseCase`, `IVectorStorePort`, `src/rag/query_transform.py`, `long_term_memory.bak` | Cadeia de RAG que o caminho quente nunca usou. O adapter emitia `FT.HYBRID`, não suportado nesta versão do Redis Stack. TD-015, TD-023. |
| `ticket_flow.py`, `crud_tool.py` | Funis do dispatcher deletado. A única coisa ainda importada era `SEED_CATEGORIAS`, extraída para `domain_services/tickets/constantes.py`. |
| `src/services/` (pacote inteiro) | `email_service.py` e `registration_service.py` estavam mortos; `channel_store.py` e `evolution_service.py` estavam vivos e foram movidos para `infrastructure/services/`. TD-003. |
| `tests/unit/domain/test_ingest_use_case.py`, `tests/unit/test_registration_service.py` | Exercitavam só o código removido. |

**Sobrou vivo, apesar da aparência:** `rag/knowledge/query_transform.py`
(usado por `RAGSearchService`; o homônimo em `src/rag/` é que era morto) e
`get_async_chunks_index()`/`get_async_tools_index()` em `redis_client.py`, que
ficaram sem consumidor com a saída do adapter e estão marcadas para a próxima
varredura.

**Não é removível por `git`:** os `.pyc` órfãos (restos de `dispatcher.py`,
`langgraph_experiment/`, `semantic_router.py`) **não estão versionados** — são
artefatos locais de build. Apagar é higiene de máquina.

---

## 4. Estado real de cada flag

Três fontes competem: o default em `settings.py`, o valor no `.env` da máquina,
e o override em runtime na tabela `config_dinamica`. **Vence o último que
existir.**

| Flag | `settings.py` | `.env` atual | Consumidor real |
|---|---|---|---|
| `DEV_TEST_NO_DB_WRITE` | `True` | `true` | Bloqueia escrita no Postgres. **Tem que ir para `false` antes de produção (B1).** |
| `DEV_TEST_SKIP_REGISTRATION` | `False` | `true` | Pula o cadastro obrigatório. **Decidido em 2026-09-09: o v1 NÃO exige cadastro** — o usuário manda "oi" e já vê o menu. O `true` do `.env` passa a ser o comportamento oficial, não um atalho de dev. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | `gemini-2.5-flash` | Já é modelo estável, não-preview, nos três lugares (settings, `.env.example`, seed da migration 009). Falta conferir o valor gravado em `config_dinamica` em runtime. |
| `LLM_MODEL_FAST` | `""` | ausente | Vazio = usa o mesmo `GEMINI_MODEL`. |
| `FEATURE_LANGGRAPH_CELERY_DISPATCH` | `False` | `false` | Consumido em `orchestration/nodes.py:136`. Com `true`, o RAG faz fan-out para as filas `rag_search`/`synthesis` em vez de rodar in-process. Estava `true` no `.env` (o laudo dizia o contrário); **desligada em 2026-09-09** para o v1 rodar no caminho in-process, que é o default do código e o que os testes cobrem. |
| `FEATURE_GRAPH_EXECUTOR_PILOTO` | `False` | ausente | Nada lê no pipeline de produção. Congelada (C2.4). |
| `FEATURE_REST_PRODUCT` | `False` | `true` | Fast-path do `rest_lab` no entrypoint. |
| `FEATURE_MCP_PRODUCT` | `False` | `true` | Fast-path do `mcp_lab` no entrypoint. |
| `ENABLE_TRACING` | `False` | `false` (no `.env.example`) | Jaeger. Ligar em staging (B7). |
| `FEATURE_LANGGRAPH_NATIVE_ROUTES` | **não existe** | removida | Chave morta desde a ADR 0008. Removida do `.env` em 2026-09-09 (B1). |
| `FEATURE_MENU_BOT` | `True` | ausente (usa o default) | **Liga o bot de menu** (B2). O `entrypoint.py` resolve a rota pelo menu e o Supervisor não roda. Interruptor de **rollback**, não configuração permanente: sai junto com o código que desliga, depois da validação B8. |
| `FEATURE_QUERY_TRANSFORM_LLM` | `False` | ausente (usa o default) | Reescrita da query de busca por Gemini Flash antes do RAG. **Desligada no v1** (B6) — o gatilho casava com quase toda pergunta real, o que fazia dela uma 2ª chamada de LLM por resposta. As estratégias locais (regex, sinônimos, step-back) continuam ligadas. |

`COMPOSE_PROFILES=core,monitoring,app,gateway` é obrigatório no `.env` — sem
ele `docker compose up -d` não sobe nada.

### Rotas e o kill-switch

Seed do `route_registry` (migrations 010, 022, 023), todas com
`owner="langgraph"` desde a migration 023:

| Rota | `entrypoint_node` | `doc_type` | No v1 |
|---|---|---|---|
| `WIKI` | `rag` | `wiki_ctic` | **Ligada** |
| `CONTATOS` | `rag` | `contatos` | **Ligada** |
| `GREETING` | `greeting` | — | **Ligada** (vira o menu) |
| `ESCALAR_HUMANO` | `human_handoff` | — | **Ligada** |
| `GERAL` | `rag` | `geral` | A decidir em B3 |
| `CALENDARIO` | `rag` | `calendario` | Desligada |
| `EDITAL` | `rag` | `edital` | Desligada |
| `SIGAA` | `sigaa` | — | Desligada |
| `TICKET_ABERTURA` | `ticket` | — | Desligada |
| `CRUD` | `crud` | — | Desligada |
| `MEDIA_DOWNLOAD` | `media_download` | — | Desligada |
| `CHECK_STATUS` | `check_status` | — | Desligada |

Existem **dois** kill-switches, e a diferença importa:

| Switch | Onde | Granularidade | Limitação |
|---|---|---|---|
| Circuit-breaker por agente | `/hub/agents`, aplicado no `classify_node` | Agente | `WIKI`, `CONTATOS`, `CALENDARIO`, `EDITAL` e `GERAL` **dividem o agente `academic_knowledge`** — desligar o calendário por aqui derrubaria a wiki junto. `GREETING`, `MEDIA_DOWNLOAD`, `CHECK_STATUS` e `ESCALAR_HUMANO` têm `agente=NULL` e nunca passam pelo breaker. |
| `settings.ROTAS_ATIVAS` (v1, B3) | `.env`, aplicado no `classify_node` | **Rota** | Default vazio (sem restrição). A lista do v1 é configuração de deploy, não default de código — ver o checklist. |

É por isso que o v1 precisou de um switch por rota: o de agente não consegue
expressar "wiki sim, calendário não". Com o menu ligado, as rotas fora do v1
já são inalcançáveis; `ROTAS_ATIVAS` é a segunda tranca, para o caminho de
degradação e para os fast-paths que não passam pelo menu.

### Observabilidade — o que realmente coleta

| Peça | Estado |
|---|---|
| `metricas_llm` (Postgres) + `/hub/llm-custo` | **Funciona.** É a observabilidade oficial do v1. |
| Painéis `/hub/infra/storage`, `/hub/infra/health`, `/hub/infra/search` | **Funcionam.** |
| Prometheus | **Nunca coletou os workers.** O `prometheus.yml` tem três alvos: `oraculo_api`, `redis_exporter` e ele mesmo. Os containers Celery não expõem `/metrics` e não estão em nenhum `scrape_config`. Como quase todo `oraculo_*` é emitido dentro de um worker, nada disso chega ao Prometheus. |
| Grafana | Provisionamento historicamente não montado. |
| Alertmanager | Não existe. Um alerta que dispara fica visível na UI do Prometheus e em nenhum outro lugar. |
| `alert_rules.yml` | **Reescrito em 2026-09-09 (B7).** Tinha sete alertas, dos quais **seis** consultavam métricas inexistentes (`oraculo_tokens_total`, `oraculo_stt_requests_total`, `oraculo_tts_requests_total`, `oraculo_vision_*`). Sobraram três que podem realmente disparar: `RedisDown`, `ApiDown` e `RedisMemoriaAlta`, todos de infraestrutura. |
| Alerta de circuit-breaker | **Ligado em 2026-09-09 (B7).** Circuito aberto agora manda WhatsApp para `SUPPORT_GROUP_JID`, com silêncio de 30 min por provider. Antes só produzia um `logger.error` e uma linha de auditoria. Não passa pelo Prometheus de propósito: o código roda no worker, que não é coletado. |
| Jaeger | Código real, desligado. |

### Higiene de checkpoint (B10)

O `thread_id` é fixo por sessão e o `AsyncRedisSaver` mantém o checkpoint
indefinidamente, inclusive depois de `__end__`. A task `beat_checkpoint_gc`
(diária, 03:30) aplica TTL nas chaves `checkpoint:*` que ainda não têm um, e
deixa o Redis expirar. **Ela nunca apaga chave** — `SCAN` seguido de `DEL` num
prefixo é a forma exata do incidente registrado na ADR 0007. Desligada por
padrão: `CHECKPOINT_TTL_HORAS=0`.

### Integração contínua (B9)

`.github/workflows/tests.yml` roda em todo pull request e em push para `main`,
com Redis e Postgres reais como services: `alembic upgrade head`, depois
`pytest tests/unit`, depois o eval do scraper da wiki da CTIC. O eval do SIGAA
fica de fora, coerente com o SIGAA estar fora do v1.

---

## 4.5 Estado real da base de conhecimento (medido em 2026-09-10)

O índice `idx:rag:chunks` **existe e o schema já tem os campos que o v1
precisa** (`doc_type`, `sistema`, `modulo`). O que falta é conteúdo:

| Medida | Valor |
|---|---|
| Documentos indexados | 37 chunks, 5 fontes |
| `doc_type=wiki_ctic` | **0** |
| `doc_type=contatos` | **0** |
| `doc_type=geral` | 37 (fontes com nome de hash — material de teste) |

**Consequência direta:** toda pergunta do menu hoje responde "não encontrei".
As duas opções de "tirar dúvida" apontam para `wiki_ctic` e `contatos`, e não
há nada nessas taxonomias.

**Por que ficou assim (achado em 2026-09-10):** a página de ingestão não
tinha seletor de assunto. O JavaScript fixava `geral` e nada na tela mudava
isso, então *todo* documento entrava fora do alcance das buscas do menu. Não
havia defeito na busca — faltava conteúdo alcançável. Corrigido: `/hub/chunkviz`
agora tem o campo **Assunto**, com a wiki da CTIC e contatos no topo. Ver
TD-029.

**Isso reduz o item B5.** O plano previa recriar o índice
(`FT.DROPINDEX … DD`, destrutivo) porque faltariam os campos `sistema`/`modulo`
— eles já estão lá. O que falta é **ingerir a wiki da CTIC e os contatos com o
`doc_type` correto**, o que não exige derrubar índice nenhum. Confirmar antes
de executar; os 37 chunks de teste podem ser descartados junto ou deixados.

---

## 4.6 Achado de 2026-09-10 — o filtro de taxonomia se anulava em silêncio

Encontrado exercitando o pipeline real, não em teste.

**O que acontecia:** `RAGSearchService.buscar()` filtrava por `doc_type` e,
quando nada casava, **descartava o filtro e mantinha todos os resultados**.
Uma pergunta feita pela tecla "SIGAA" do menu buscava na wiki, não achava
nada, caía sobre os 37 chunks de teste, rodava rerank e síntese, e o modelo
produzia a recusa que está no prompt — **1747 tokens (US$ 0,00057) para dizer
"não encontrei"**, em 23 segundos.

Isso contrariava a premissa central do v1: a tecla apertada deveria tornar a
busca mais precisa e mais barata, e era justamente ela que se perdia.

**Correção:** parâmetro `taxonomia_estrita`, ligado pelo `rag_node` quando o
menu está ativo e o `doc_type` não é `geral`. Nesse caso, sem resultado no
assunto pedido, a busca devolve vazio e o LLM **não é chamado**. Quem escolhe
o `doc_type` por palpite (o Supervisor, em rollback) mantém o retorno amplo de
antes — errar a taxonomia ali é comum e cair na busca ampla salva a resposta.

**Medido depois:** mesma pergunta, 2 segundos, zero token. Travado por
`tests/unit/rag/knowledge/test_taxonomia_estrita.py`.

---

## 4.7 Achado de 2026-09-10 — o atendimento humano era um beco sem saída

**O que acontecia:** pedir um atendente silencia o bot naquela conversa por
24h (`handoff:session:*`). Havia duas saídas antes do prazo: o TTL e o comando
`$voltar <jid>`. O comando só funciona pelo WhatsApp, porque depende do
gatekeeper de comandos de admin.

**O beco:** quem testava pelo **simulador de chat do painel** ficava presa. O
simulador não passa pelo gatekeeper, então a única saída era inalcançável de
lá. Aconteceu de verdade: `web_session_admin_uema_khalil` ficou muda por 22
horas, e a instrução de saída viajava dentro do aviso mandado ao suporte — que
o operador pode nunca ter visto.

**Correção:** página **`/hub/handoffs`** ("Atendimento humano"). Lista as
conversas pausadas, separa as do WhatsApp (alguém esperando) das do simulador
(quase sempre resíduo de teste), mostra quanto falta para voltarem sozinhas, e
devolve qualquer uma ao bot com um botão. O comando do WhatsApp continua
existindo; a página é a saída que não exige decorar um identificador de
sessão.

O aviso mandado ao suporte agora cita as duas saídas.

Travado por `tests/unit/hub/test_handoffs.py`.

---

## 4.8 Achado de 2026-09-10 — o limite de mensagens punia quem navegava

**O que acontecia:** o guardrail de entrada corta quem manda mensagens demais
(8 por minuto). Três problemas juntos:

1. A configuração era decorativa — o método lia constantes do módulo, não os
   campos do próprio objeto, e procurava um atributo com nome errado.
2. A contagem incluía as próprias recusas, então insistir mantinha o bloqueio
   de pé sozinho (observado: `9, 10, 9, 9, 9`, sem destravar).
3. **Navegar o menu contava.** O limite existe para proteger gasto de IA, e
   apertar `1`, `2`, `9`, `0` custa zero.

**Correção:** o limite virou `RATE_LIMIT_MSGS` / `RATE_LIMIT_WINDOW_S`,
ajustáveis pelo painel sem reiniciar; só a mensagem que passa é contada; e a
cobrança acontece no passo 0.5 do entrypoint, quando já se sabe que a mensagem
vira **pergunta**. Tamanho e injeção continuam checados sempre. Com o menu
desligado, a cobrança volta a valer para toda mensagem, porque aí não há como
saber o custo antes de classificar.

Ver TD-028.

---

## 4.9 A wiki da CTIC é maior que SIGAA e SIPAC (medido em 2026-09-11)

A wiki tem **1383 páginas** e se organiza, pela própria página inicial, em
**sete áreas de topo**. O menu do v1 cobre duas delas explicitamente.

| área | como o menu trata hoje |
|---|---|
| SIPAC — administrativo | opção própria |
| SIGAA — acadêmico | opção própria |
| **SIGRH — servidores** | sem caminho visível |
| **Office (Microsoft)** | sem caminho visível |
| **LibreOffice** | sem caminho visível |
| Tira-dúvida | sem caminho visível |
| Arquivos importantes | sem caminho visível |

Classificar por nome de página cobre só 40%: SIGAA 19%, SIPAC 14%, Office 4%,
e **60% não dizem no título a que sistema pertencem**. É por isso que a
taxonomia é deduzida do grafo de links, não do nome.

**A capacidade já existe, a descoberta é que não.** A opção "Não sei qual
sistema / outro assunto" busca na wiki inteira, sem filtro — uma pergunta
sobre Excel seria respondida por ali hoje. O que falta é a pessoa saber que
pode perguntar, e isso é rótulo de menu, não código.

**O que mudou nesta rodada:** o mapa de taxonomia foi de 10 para 43 hubs,
cobrindo os seis sistemas, e a normalização de identificador passou a remover
acento — sem isso, cinco dos oito módulos do SIPAC perdiam a taxonomia
(TD-032). O efeito só aparece numa reingestão.

**Restrição de produto registrada:** o público inclui pessoas que não têm
familiaridade com tecnologia. Os rótulos do menu precisam ser simples e
explícitos, não jargão de sistema.

---

## 5. Checklist "pronto para produção"

Nada vai ao ar com um item aberto. Detalhamento do item B8 do plano.

### Configuração
- [ ] Ajustar `RATE_LIMIT_MSGS` / `RATE_LIMIT_WINDOW_S` se 8 por minuto for
      apertado para o uso real (editável em `/hub/config`, vale na hora).
- [ ] `DEV_TEST_NO_DB_WRITE=false` — **único item de flag ainda aberto.** Sem
      isso nada é gravado no Postgres em produção.
- [x] `DEV_TEST_SKIP_REGISTRATION` decidido: fica `true`, o v1 não pede cadastro.
- [x] `FEATURE_LANGGRAPH_NATIVE_ROUTES` removida do `.env`.
- [x] `FEATURE_LANGGRAPH_CELERY_DISPATCH=false` — RAG in-process no v1.
- [ ] `ROTAS_ATIVAS=WIKI,CONTATOS,GREETING,ESCALAR_HUMANO` no `.env` — o
      código não restringe nada por default, então **sem essa linha o
      kill-switch de rota não existe** em produção.
- [ ] `FEATURE_MENU_BOT=true` confirmado (é o default do código, mas explicitar
      no `.env` deixa o rollback óbvio para quem estiver de plantão).
- [ ] `GEMINI_MODEL` confirmado em `config_dinamica` (runtime), não só no arquivo.
- [ ] Rotas fora do v1 desligadas em `/hub/agents`.

### Dados
- [x] `/hub/chunkviz` permite escolher o assunto do documento (sem isso, tudo
      entrava como `geral` e ficava fora das buscas do menu).
- [x] `idx:rag:chunks` já tem os campos `sistema` e `modulo` — **não precisa
      recriar** (medido em §4.5; o item B5 encolheu).
- [ ] Wiki da CTIC e contatos reingeridos; contagem de chunks confere em `/hub/infra/search`.
- [ ] Backup do Postgres e snapshot do Redis feitos **antes** da recriação do índice.

### Comportamento

> Os itens marcados foram validados em 2026-09-10 pelo **orquestrador real
> dentro do container** (`entrypoint.processar()`, o mesmo caminho de uma
> mensagem do WhatsApp depois do gatekeeper), não por teste unitário. Falta
> repetir por WhatsApp de verdade, que é o que fecha o item.

- [x] Menu principal aparece na primeira mensagem e em `menu`.
- [x] Cada um dos 3 submenus abre e navega.
- [x] `9` volta um nível; `menu` volta ao início.
- [ ] Pergunta real sobre SIGAA responde com fonte. **Bloqueado por §4.5** —
      não há conteúdo `wiki_ctic` no índice; hoje responde "não encontrei".
- [ ] Pergunta real sobre SIPAC responde com fonte. **Mesmo bloqueio.**
- [ ] Pergunta de contato responde. **Mesmo bloqueio** (`contatos` vazio).
- [x] `0` faz o handoff e silencia o bot na sessão.
- [x] A conversa pausada pode voltar ao bot **antes das 24h**, pelo painel
      (`/hub/handoffs`) ou por `$voltar` no WhatsApp. Testado nas duas.
- [ ] Texto solto fora de um passo vira pergunta e cai no RAG.
- [ ] Texto não reconhecido dentro de um menu devolve a mensagem de fallback.

### Custo
- [x] Navegar o menu inteiro gera **zero** token novo. Medido: conversa de 8
      mensagens (menu, 3 submenus, `9`, `menu`, texto fixo, handoff) → 0
      chamadas e US$ 0,00 em `metricas_llm`. Navegação leva 1–2 ms porque não
      chega ao grafo.
- [ ] Pergunta repetida cai no cache semântico e não gera token.

### Segurança
- [x] Guardrail de tamanho (>1200 caracteres). Verificado no sistema real:
      1500 caracteres bloqueados com mensagem clara, 300 passam.
- [x] Guardrail de jailbreak. **Estava burlável por omissão de acento** —
      corrigido e travado em teste (TD-031). Verificado com e sem acento, em
      maiúsculas, e contra seis perguntas legítimas.
- [x] Censura de CPF e e-mail na saída. Verificado no sistema real e o
      desenho é **deliberado**: CPF (com e sem pontuação) e e-mail externo são
      substituídos; e-mail `@uema.br`/`@aluno.uema.br` e telefone **passam de
      propósito** — a rota de contatos existe justamente para informá-los.
      Censurá-los quebraria o produto.

### Qualidade e operação
- [ ] `pytest tests/unit tests/eval` verde local e no CI.
- [ ] `/eval` acima do limiar acordado com a liderança.
- [x] Circuit-breaker testado. Abre em 5 falhas na janela, loga o aviso e
      **não troca de provedor sozinho** (a decisão é do operador). O botão de
      reset em `/hub/llm-custo` fecha o circuito — testado nos dois sentidos.
      Falta só derrubar o provider de verdade para confirmar o alerta
      chegando a alguém.
- [ ] `SUPPORT_GROUP_JID` preenchido no `.env` — sem ele o alerta de
      circuito aberto cai no primeiro `ADMIN_NUMBERS`, e sem nenhum dos dois
      não vai a lugar nenhum.
- [ ] Um alerta real (circuit-breaker aberto) chega a uma pessoa: derrube o
      provider e confirme a mensagem chegando no WhatsApp.
- [ ] `CHECKPOINT_TTL_HORAS` decidido (0 = crescimento sem teto no Redis).
- [ ] Runbook mínimo escrito: como reiniciar, como reverter, quem avisar.

---

## 6. Manutenção deste documento

- Este arquivo descreve **o que é**, nunca o que se pretende fazer. Plano é
  outro documento.
- Ao mudar arquitetura, flag ou escopo, atualize aqui **na mesma mudança**.
- Qualquer afirmação sobre flag, fila ou modelo tem que ser verificável por
  `grep` no código. Se não for, não entra.
- Documentos superados vão para `historico/` com banner, não são apagados.

### Estado da documentação (Trilha A, 2026-09-09)

| Documento | Estado |
|---|---|
| `.claude.md` | Atualizado — inclui o motor de menu e o congelamento do `graph_studio` |
| `architecture/arquitetura_oraculo.md` | **Reescrito** (A2): §1, §3, §4.3, §5 e a cadeia de migrations até 024. §10 é registro cronológico, com aviso |
| `README.md` | **Reescrito** (A3): §1, §2, §4, §5, §11, §12, §16 |
| `technical-debt.md` | **Revisado** (A4): 7 dívidas fechadas, TD-019 a TD-024 novas |
| `business/regras_negocio_oraculo.md` | **§0 reescrita** (A7) para a liderança; §1 em diante virou anexo técnico histórico |
| `architecture/system-map.md` | **Atualizado** — inclui `application/menu/` e o fluxo do v1 |
| `architecture/graph-studio.md` · `graph-studio-sandbox.md` | Atuais |
| `decisions/0007`, `decisions/0008` | Atuais |
| Roadmaps da raiz de `docs/` | **Arquivados** (A5) em `historico/roadmap-2026-superado/`, com banner |
| Banners de `historico/` | **Corrigidos** (A6): `arquitetura_nos_declarativa.md` e `estado_e_roteiro_planos.md` |
| Docstrings enganosas | **Corrigidas** (A9): `redis_client` (dizia SVS-VAMANA, é HNSW), `agents/registry`, `supervisor`, `graph_executor`, `ticket_flow`, `crud_tool`, `contracts`, `models` |
