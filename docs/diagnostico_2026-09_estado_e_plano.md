# Oráculo — Diagnóstico Arquitetural e Plano de Recuperação

> **Status: ⚠️ PARCIALMENTE SUPERADO NO MOMENTO DA PUBLICAÇÃO. Laudo proposto,
> nada executado por ele.** A auditoria foi feita contra o commit `ca818e1` de
> uma cópia local que estava **16 commits atrás** da branch
> `hub/redesign-htmx-infra`. Ao publicar (2026-09-08) descobriu-se que a Fase
> 0-5/B do ADR 0008 já tinha sido executada. Nada aqui foi corrigido
> retroativamente — o laudo fica como estava, e a divergência está listada
> logo abaixo. **Leia esta lista antes do resto do documento.**

### O que este laudo diz que já não vale (verificado contra `e527000`)

| §  do laudo | Situação real hoje |
|---|---|
| **I-01, TD-001/002, B5** — "dois orquestradores" | **Resolvido.** `dispatcher.py` e `dispatcher_langgraph.py` **não existem mais** (`c045299`, ADR 0008). Orquestrador único. |
| **§0.3 — `src/graph/` é universo paralelo sem LangGraph** | **Obsoleto.** Virou `src/graph_studio/` (`33dbf25`), e a topologia agora alimenta o grafo real via `GraphSpec`. |
| **Trilha C inteira** — "pausar o Graph Studio, o declarativo é arriscado" | **Recomendação errada contra o código atual.** `src/application/orchestration/builder.py` monta o `StateGraph` a partir de `specs/default.json`, mas com nós e routers em **código** (`node_manifest`), state estático (`OraculoState`) e funis HITL `locked=true` — ou seja, evita exatamente o risco que a §1.2 levanta. O caminho escolhido é defensável; meu argumento partia de um código anterior. |
| **C1** — "fazer o Graph Studio espelhar o grafo de produção" | **Já feito** (`c67a571`). |
| **§0.1** — "grafo em `langgraph_experiment/graph.py`" | Movido para `src/application/orchestration/`. `langgraph_experiment/` não existe mais. |
| **§0.2** — `FEATURE_LANGGRAPH_NATIVE_ROUTES` como flag estática | Virou config dinâmica (`6edeab2`). |

### O que **continua valendo** — e é o que importa

- **B1, o portão de entrada: intacto e ainda bloqueante.** Verificado no HEAD
  atual: `process_message_task.py:327` mantém o
  `if remote_jid != settings.ALLOWED_GROUP_ID: return`, e a **linha 360** mantém
  o `if decision.target == DispatchTarget.IGNORE: decision.target = LLM`.
  Nenhum dos 16 commits tocou nisso. Continua sendo o único item que impede o
  alvo declarado (DM no WhatsApp da CTIC) e continua sendo a armadilha descrita:
  abrir a 327 sem consertar a 360 libera qualquer número para consumir tokens.
- **Toda a Trilha B de produto** (B1-B4, B6-B8): ingestão real do wiki CTIC,
  golden set de suporte técnico, groundedness ≥ 0,75, higiene operacional,
  critérios de "pronto". Nada disso foi feito.
- **Trilha A**, com ressalva: as inconsistências I-01/I-02/I-04/I-05 precisam ser
  reconferidas contra o HEAD atual antes de corrigir.
- **Fase 1 (pesquisa)** inteira, incluindo as fontes.

> A lição registrada aqui é a mesma que o laudo diagnostica: **auditei uma cópia
> local sem verificar se ela estava em dia com o remoto.** Uma re-auditoria
> contra o HEAD atual está pendente e não foi feita.

## Contexto

O projeto perdeu rastreabilidade após um pivô não combinado: em vez de shippar o
agente prometido à liderança, a energia foi para uma "central de administração"
inspirada vagamente em LangGraph Studio. O pedido é **diagnóstico + plano**, não
código.

Duas correções de premissa saíram das suas respostas e mudam o plano inteiro:

1. **O produto é um agente de suporte técnico da CTIC**, ancorado no wiki
   `ctic.uema.br/wiki` + FAQs. As rotas acadêmicas (CALENDARIO/EDITAL/CONTATOS/
   SIGAA) foram achismo, não requisito. A API do sistema integrado é futuro.
2. **O alvo de produção é o WhatsApp oficial da CTIC**, para alunos e servidores,
   com RBAC servindo a dois propósitos: segurança **e controle de gasto de
   tokens**. O `ALLOWED_GROUP_ID` de hoje é ambiente de teste, não produção.

O achado central deste laudo é que essa mudança de alvo colide com uma trava
estrutural no código (§B1) — é o item mais importante do documento.

Vale registrar uma coisa que o seu relato subestima: **a documentação deste
projeto é melhor que a média**, e a engenharia do caminho de produção é sólida.
O problema não é ausência de rastro, é **excesso de rastro sem hierarquia** —
34 `.md` em `docs/`, dos quais 9 são órfãos do índice oficial, e um `notas.md`
de 2.218 linhas cujo último registro é anterior a todo o trabalho do Hub v2.

---

# FASE 0 — LAUDO DO ESTADO REAL

Método: leitura do código, não dos `.md`. `src/` = 274 arquivos `.py`, 37.106
linhas. Suíte executada nesta sessão: **668 passed, 3 failed, 19 skipped** (as 3
falhas são falta de Redis/Postgres locais, não lógica — confirmado).

## 0.1 O que está funcional (fato, verificado no código)

| Componente | Evidência |
|---|---|
| **LangGraph como orquestrador real de produção** | `process_message_task.py:129`, `chain_sse.py:16`, `hub.py:266` e `hub.py:2543` **todos** importam `dispatcher_langgraph`. Grafo em `langgraph_experiment/graph.py`: 15 nós, edges condicionais, `AsyncRedisSaver`. |
| **HITL por `interrupt()` com checkpointer Redis** | Funis de ticket e CRUD quebrados em 1 `interrupt()` por nó — mitigação deliberada de um bug conhecido do `langgraph-checkpoint-redis`. Documentado em `dispatcher_langgraph.py:33-40` com links pras issues. Isso é engenharia madura, não gambiarra. |
| **Supervisor único de classificação** | `router/supervisor.py`, 5 camadas. O grafo **reusa** ele (`dispatcher_langgraph.py:396`) em vez do `classify_node` interno — o problema histórico dos "três cérebros" (`notas.md` §1) está resolvido. |
| **Rota→execução como dado** | `infrastructure/route_registry.py` + migration 010. `owner`, `entrypoint_node`, `cacheavel`, `permite_detour`, `doc_type`, `k` — editável em runtime pelo `/hub/routes`, com Postgres como verdade e espelho Redis. **Isto já é a "plataforma configurável" que você queria.** |
| **Telemetria de custo — funcionando** | `MonitoredLLMProvider` (`llm_factory.py:121`) grava `metricas_llm` (Postgres) + Prometheus a cada chamada. 2 dashboards Grafana **versionados** em `observability/grafana/provisioning/dashboards/`. Tracing OTel com semântica `gen_ai.*` em `tracing.py` (off por padrão). |
| **RAG híbrido** | `redis_client.busca_hibrida` (BM25 + KNN + RRF manual, síncrono) é o caminho quente e funciona. |
| **Scraping DokuWiki** | Subpacote `scraping/implementations/dokuwiki/` usando `do=export_raw`/`do=index` nativos. Eval com fixtures reais congeladas (`tests/eval/test_ctic_wiki_eval.py`). Tecnicamente o pedaço mais bem-feito do repo. |
| **CI real** | `.github/workflows/tests.yml` sobe Redis + Postgres e roda migrations antes dos testes. |

**Conclusão desta seção:** o núcleo é viável. Nada aqui precisa de reset.

## 0.2 Parcialmente funcional / com defeito conhecido

| Item | Fato |
|---|---|
| **Portão de entrada** | `process_message_task.py:328` — `if remote_jid != settings.ALLOWED_GROUP_ID: return`. **Toda mensagem que não venha do grupo de teste é descartada antes de qualquer coisa.** Detalhe em §B1. |
| **Gatekeeper inerte (TD-013)** | `process_message_task.py:353-354` reescreve **toda** decisão `IGNORE` para `LLM`. Os filtros de segurança do `gatekeeper.py` (grupo estranho, privado bloqueado, não-admin em comando admin, mensagem inútil) não bloqueiam nada — só mudam o motivo no log. |
| **`FEATURE_LANGGRAPH_NATIVE_ROUTES=false`** | Com a flag desligada, GREETING/SIGAA/MEDIA_DOWNLOAD/CHECK_STATUS ainda delegam pro `dispatcher.py`. **Nenhuma rota tem `owner="legacy"` por padrão** (`route_registry.py:95-106`) — essa flag é literalmente a distância inteira até aposentar o dispatcher legado. |
| **GLPI** | Stub. `tickets/service.py:47-50` loga e devolve mensagem fake; `ticket_flow.py:14` grava JSON em `dados/tmp/`. Consistente com "API depois" — não é dívida, é escopo adiado. Mas o painel e os `.md` falam de "chamado" como se existisse. |
| **Índice Redis desatualizado** | `sistema`/`modulo` existem no `IndexSchema` do código, mas o índice em produção não foi recriado. Exige `FT.DROPINDEX idx:rag:chunks DD` + reingestão (destrutivo, aguardando autorização). **Isto bloqueia o filtro por sistema/módulo do wiki CTIC — ou seja, bloqueia o produto.** |
| **`discovery.py` não agendado** | `descobrir_paginas()` só roda manual. Sem isso o FAQ envelhece silenciosamente. |
| **Testes não herméticos** | 3 testes de `tests/unit` exigem Redis vivo. O CI provê; a máquina local não. `.claude.md` chama a suíte de "sem infra externa (maioria)" e cita um baseline de **376 passed** — hoje são **690 coletados**. |
| **TD-015** | `RedisVLVectorAdapter.buscar_hibrido` emite `FT.HYBRID`, não suportado nesta versão do Redis Stack; a exceção é engolida e devolve `[]`. Fora do caminho quente hoje — armadilha para quem ligar. |

## 0.3 Código morto ou vestigial (nada apagar sem sua confirmação)

| Item | Tamanho | Situação |
|---|---|---|
| **`src/graph/` inteiro** | **2.584 linhas**, 22 arquivos | Sistema de nós **próprio**, sem nenhuma relação com LangGraph (`grep`: zero imports de `langgraph` em `src/graph/`). Consumido **exclusivamente** por `hub.py` e por `system_health.py`. `GraphExecutor` só roda em `dry_run` ou "sandbox" a partir de um botão do painel. `FEATURE_GRAPH_EXECUTOR_PILOTO` existe e **nada lê no hot path**. É um universo paralelo com 3 migrations (013/014/015) e um canvas visual. |
| `src/services/` | 4 arquivos, ~38 KB | Migração para `capabilities/` nunca terminada (TD-003). |
| `src/memory/long_term_memory.bak` | — | Preservado de propósito por causa de TD-007. |
| `src/rag/query_transform.py` | — | Import quebrado (TD-007); substituído por `agents/academic_knowledge/query_transform.py`. |
| `tests/test_wiki_scraper.py` | — | Órfão (TD-012). |
| 4 arquivos em `tests/e2e/` | — | Órfãos, imports quebrados (TD-014). |
| `worker_graph_extractor` | — | Container desligado desde 2026-07-31; código no repo. |
| Chaves `LANGFUSE_*` no `.env` | — | Resíduo de avaliação descartada, sem consumidor. |
| `pyproject.TOML` | — | Extensão maiúscula, não lido por build/Docker/CI (TD-004). |

## 0.4 Inconsistências encontradas (doc × código)

Registradas, **não resolvidas** — como você pediu.

| # | Inconsistência |
|---|---|
| **I-01** | **`dispatcher.py` não é mais "um segundo caminho ativo".** TD-001, `.claude.md`, `system-map.md` e `docs/README.md` dizem que SSE/eval/hub o chamam. Verificado: **todos** os entry points importam `dispatcher_langgraph`. Ele só é alcançável por delegação interna (4 rotas condicionais) + um helper em `eval_api.py:324`. A dívida é bem menor que a documentada. |
| **I-02** | **Dashboards Grafana estão versionados.** `arquitetura_oraculo.md` §10 afirma "não versionado no repo — painéis vivem no volume `grafana_data`, criados manualmente". Existem 2 JSON em `observability/grafana/provisioning/dashboards/`. |
| **I-03** | **Sua percepção de que "telemetria nunca funcionou" está desatualizada.** `MonitoredLLMProvider` grava custo real desde 2026-08-15 (`notas.md` §13.3). |
| **I-04** | **Baseline de testes errado.** `.claude.md` cita 376 passed / falha em `test_registration_repository.py`. Real: 690 coletados, 668 passed, e as falhas locais são outras. |
| **I-05** | **`docs/README.md` diz "TD-001 a TD-014".** `technical-debt.md` tem TD-016, mas a tabela-índice no topo dele também para em TD-014. |
| **I-06** | **9 `.md` órfãos em `docs/`** não aparecem no índice oficial `docs/README.md`: `INDEX_ROADMAP_2026`, `ROADMAP_EXECUTIVO`, `README_ROADMAP`, `SPRINT_CAMADA1_PLANEJAMENTO`, `DIA1_CAMADA1`, `COMO_RODAR_TESTES_CAMADA1`, `ARQUIVOS_CRIADOS_CAMADA1`, `CHECKLIST_PRE_FASE_6`, `decision_camada1_nodes`. Três deles pedem uma decisão ("Graph Studio: sua decisão esta semana") **que já foi tomada de fato no código**. Este conjunto é a principal fonte concreta da sua perda de rastreabilidade. |
| **I-07** | **`notas.md` para em §16 (2026-08-25).** Todo o Hub v2 (Sprints 0-8, Graph Studio, migrations 016-020, 5 commits) não tem registro de sessão — justamente o trecho em que você diz ter perdido o controle. |
| **I-08** | **Dois planos-mestres apontam para caminhos Windows inexistentes nesta máquina** (`C:\Users\User\.claude\plans\silly-percolating-ritchie.md` e `...soft-moonbeam.md`), citados como "roadmap completo" por `arquitetura_oraculo.md` §12 e §10. A fonte de verdade do Hub v2 é inacessível a partir do repo. |
| **I-09** | **Escopo declarado × escopo real.** `.claude.md` define o produto como "assistente acadêmico da UEMA". Você define como "suporte técnico da CTIC". Nenhum `.md` registra essa virada. |
| **I-10** | **`arquitetura_oraculo.md` §3 se descreve como "multi-agente assíncrono"** e mantém o diagrama Router→Agents→Capabilities como fluxo principal, com uma nota ⚠️ dizendo que na verdade é o grafo. O corpo contradiz a correção. |

---

# FASE 1 — PESQUISA (2026)

## 1.1 LangGraph vs. alternativas

LangGraph chegou à 1.0 em out/2025 e ultrapassou CrewAI em estrelas no início de
2026, puxado por adoção enterprise da arquitetura de grafo. Os concorrentes de
2026 (OpenAI Agents SDK, Microsoft Agent Framework 1.0, CrewAI, AutoGen, Strands)
resolvem problemas diferentes do seu: *handoffs* entre agentes e *crews* de
papéis — exatamente o modelo multi-agente que você já abandonou com razão.

O consenso mais útil da pesquisa: *"a diferença entre um bom e um mau sistema de
agentes quase nunca é o framework; é o pipeline de avaliação, a observabilidade e
a lógica de recuperação de falha."*

> **Recomendação: manter LangGraph, sem reavaliação.** Trocar de framework agora
> seria repetir o erro de 2025 (ReAct → Supervisor → LangGraph). Você já pagou o
> custo da migração e o grafo é a parte que funciona.

## 1.2 "Plugin architecture" / low-code agent builder — é overengineering aqui?

Como n8n, Langflow, Dify e Flowise realmente resolvem "criar nó sem hardcode":
eles **não** montam o grafo a partir de JSON arbitrário. Eles mantêm uma
biblioteca de componentes escritos em código, com metadados, e expõem um
*escape hatch* de código embutido (JS no n8n, Python por componente no Langflow,
bloco de código em sandbox no Dify). O "sem hardcode" é marketing sobre uma
biblioteca de nós hardcoded muito grande — n8n tem 400+ integrações e uma equipe
paga para mantê-las.

Sobre montar `StateGraph` dinamicamente: o `StateGraph` é um *builder* que exige
`.compile()`, e a pesquisa de produção é unânime em que **o schema do state é a
decisão mais consequente de um projeto LangGraph** — reducers `Annotated` errados
causam sobrescrita silenciosa em ramos paralelos. Um grafo montado a partir de
dados de usuário torna essa decisão dinâmica, ou seja, indepurável.

> **Recomendação: o "flowchart editável" genérico é overengineering para o
> estágio atual — mas você já construiu 80% do valor dele sem perceber.**
> `route_registry` + `agentes_catalogo` + `agent_prompts` + `llm_providers` +
> `tools_catalogo` são configuração dirigida a dados que **já toca o caminho
> quente**. `src/graph/` é um segundo sistema de nós que não toca nada. Detalhe
> na Trilha C.

## 1.3 HITL em LangGraph — estado da arte

O checkpointer durável + `thread_id` único por sessão é obrigatório; a falha
número um é compilar sem checkpointer. Você já faz o certo (`AsyncRedisSaver`,
`thread_id` por sessão).

O bug que você contornou **continua aberto**: a issue #6208 ("não re-executar um
nó que interrompeu enquanto nem todos os seus interrupts foram resumidos") segue
sem resolução, e há uma família de issues relacionadas (#6626 IDs idênticos em
interrupts paralelos, #4028 resume múltiplo, #6533 valores misturados entre
tools). **Sua mitigação — 1 `interrupt()` por nó — é exatamente a recomendação
que a comunidade converge.** Não mexa nisso.

Duas práticas que você ainda não tem:
- **Não interromper em passo reversível.** HITL custa tempo humano; grafo que
  interrompe demais treina o humano a carimbar aprovação sem ler.
- **Expiração de thread.** Consultar o checkpointer por threads com `updated_at`
  mais velho que um TTL e auto-rejeitar. Hoje seu `thread_id` é fixo por sessão e
  o checkpoint vive indefinidamente — você já sofreu com isso (o vazamento de
  `cancelado` documentado em `dispatcher_langgraph.py:42-48`).

## 1.4 Fluxo por canal sem duplicar lógica

O padrão é o clássico **Channel Adapter** (Hohpe/Woolf): o adapter converte o
protocolo do canal em uma mensagem canônica, e o grafo permanece agnóstico ao
canal. A literatura de agentes multi-canal reforça: escrever instruções do agente
**agnósticas ao canal**, com um único motor de regras, e preservar contexto
quando o usuário troca de canal.

> **Recomendação:** seu `webhook_controller` → `MensagemWhatsApp` (DTO) → task já
> é meio caminho. O que quebra a agnosticidade é o `chat_id`/JID vazando até o
> `user_context` dentro do grafo (`state.user_context`, `sigaa_node`,
> `media_download`). Para o FAQ isso **não é bloqueante** — anote como dívida a
> pagar quando o segundo canal existir de verdade. Não construa a abstração
> multi-canal antes de ter o segundo canal.

## 1.5 Observabilidade

O padrão de portabilidade de 2026 é **OpenTelemetry GenAI semantic conventions
(`gen_ai.*`)** — recomendação de tratá-lo como requisito de compra obrigatório.
LangSmith é a bancada gerenciada para quem já é LangChain/LangGraph e hoje aceita
traces OTel; Langfuse é a alternativa open-source, MIT, self-hostable, adquirida
pela ClickHouse em jan/2026. Quem já roda Prometheus/Loki/Grafana expõe métricas
de LLM via OTel Collector com o processador *genai*, que extrai contagem de
tokens e calcula custo a partir dos spans.

> **Recomendação: não trocar de stack — ligar o que existe.** Você já emite
> `gen_ai.*` (`tracing.py`), já grava custo em Postgres e Prometheus, e tem 2
> dashboards versionados. O gap não é ferramenta, é **ninguém olhando**. Ligar
> Langfuse/LangSmith agora adiciona um serviço para operar sem resolver o
> problema real. Reavalie quando precisar de *eval* contínuo com LLM-as-judge.

## 1.6 Barra de produção para RAG/FAQ

Métricas de partida: `recall@5` para retrieval, **groundedness** para qualidade
da resposta, taxa de citação para confiança. Limiar de referência: **groundedness
≥ 0,75 para ir a produção; abaixo disso o usuário encontra alucinação; acima de
0,85 é confiavelmente ancorado.** Golden set inicial de **30 a 50 exemplos**,
incluindo perguntas comuns, casos de borda de permissão, **casos sem resposta** e
falhas reais de produção passadas. Checklist: groundedness não pode cair >5%,
nenhum exemplo de alto risco pode falhar, nenhum vazamento entre tenants,
latência dentro do limite.

**Fontes**

- [The best AI agent frameworks in 2026 — LangChain](https://www.langchain.com/resources/ai-agent-frameworks)
- [Agent Orchestration Frameworks 2026: 6 Best Compared](https://fp8.co/articles/AI-Agent-Frameworks-Complete-Guide-2026)
- [5 AI Agent Orchestration Frameworks Compared (2026)](https://shreyans.tech/blog/ai-agent-orchestration-frameworks-compared-2026)
- [Interrupts — Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Human-in-the-Loop Workflows with LangGraph: Interrupts, Approvals, and Async Execution](https://www.abstractalgorithms.dev/langgraph-human-in-the-loop)
- [Issue #6626 — interrupt() em tools paralelas gera IDs idênticos](https://github.com/langchain-ai/langgraph/issues/6626)
- [Issue #4028 — Unable to resume multiple interrupts from a single graph invoke](https://github.com/langchain-ai/langgraph/issues/4028)
- [LangGraph's HITL Has a Double Execution Problem](https://blog.raed.dev/posts/langgraph-hitl/)
- [Application structure — Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/application-structure)
- [LangGraph Production Configuration: 5 Patterns That Scale](https://markaicode.com/best/best-langgraph-configuration-production-guide/)
- [n8n vs Dify vs Flowise — 2026 UX Review for AI Agents](https://rapidclaw.dev/blog/low-code-ai-agent-platforms-compared-2026)
- [The 7 Best Low-Code AI Agent Platforms in 2026 — Botpress](https://botpress.com/blog/low-code-ai-agent-platforms)
- [Channel Adapter — Enterprise Integration Patterns](https://www.enterpriseintegrationpatterns.com/patterns/messaging/ChannelAdapter.html)
- [AI Agent Architecture Patterns — Redis](https://redis.io/blog/ai-agent-architecture-patterns/)
- [AI Agent Observability in 2026: OpenAI Agents SDK, LangSmith, and OpenTelemetry](https://dev.to/chunxiaoxx/ai-agent-observability-in-2026-openai-agents-sdk-langsmith-and-opentelemetry-3ale)
- [Top LLM Observability and Evaluation Platforms in 2026 — MarkTechPost](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/)
- [RAG Evaluation in Production: Groundedness, Faithfulness, and Retrieval Quality — Openlayer](https://www.openlayer.com/blog/rag-pipeline-evaluation-groundedness-faithfulness)
- [RAG Evaluation Checklist for AI SaaS](https://dev.to/jackm-singularity/rag-evaluation-checklist-for-ai-saas-catch-bad-answers-before-users-do-3hlo)

---

# FASE 2 — PLANO DE AÇÃO

Legenda de esforço (solo): **S** ≈ 1 dia · **M** ≈ 2-5 dias · **L** ≈ 1-2+ semanas.

---

## TRILHA A — Arrumar a casa

*Objetivo: você voltar a conseguir responder "o que existe e por quê" em 5
minutos. Nenhuma mudança de arquitetura.*

### A1 — `ESTADO_ATUAL.md` na raiz — **M** — sem dependências

Um documento, versionado, que é a única coisa que alguém precisa ler para saber
onde o projeto está. Seções obrigatórias:

- **O que o produto é hoje**: agente de suporte técnico da CTIC sobre o wiki +
  FAQs, no WhatsApp da CTIC. Resolve I-09.
- **O que está em produção agora** vs. **o que está atrás de flag** vs. **o que
  existe mas nada chama** (as três colunas de §0.1/§0.2/§0.3 deste laudo).
- **Tabela de feature flags** e o que cada uma liga de verdade.
- **As 5 fontes de verdade** e o que perguntar a cada uma.
- **Data + commit** do último *sign-off*, para envelhecer de forma visível.

Regra de manutenção: uma linha só — *este arquivo é atualizado no mesmo commit
que muda o que ele descreve.*

### A2 — Sanear `docs/` — **S** — sem dependências

- Mover para `docs/historico/` **com banner de status**: `SPRINT_CAMADA1_PLANEJAMENTO`,
  `DIA1_CAMADA1`, `COMO_RODAR_TESTES_CAMADA1`, `ARQUIVOS_CRIADOS_CAMADA1`,
  `CHECKLIST_PRE_FASE_6`, `decision_camada1_nodes`.
- Fundir os três roadmaps (`INDEX_ROADMAP_2026`, `ROADMAP_EXECUTIVO`,
  `README_ROADMAP`) em **um** `docs/historico/roadmap_2026_graph_studio.md`, com
  banner dizendo que a decisão que eles pedem já foi tomada de fato — e qual foi.
- Atualizar `docs/README.md` para que **nenhum** `.md` fique fora do índice.
  Resolve I-06.

### A3 — Corrigir as inconsistências pontuais — **S** — depende de A1

Uma passada, itens já localizados neste laudo:

| Corrigir | Onde | Resolve |
|---|---|---|
| `dispatcher.py` não é caminho ativo de nenhum consumidor externo | TD-001, `.claude.md`, `system-map.md` | I-01 |
| Dashboards Grafana **são** versionados | `arquitetura_oraculo.md` §10 | I-02 |
| Baseline de testes → 690 coletados / 668 passed, e as 3 falhas exigem Redis | `.claude.md` | I-04 |
| Índice de TD vai até TD-016 | `technical-debt.md`, `docs/README.md` | I-05 |
| §3 se descreve como "multi-agente assíncrono" no corpo | `arquitetura_oraculo.md` | I-10 |

### A4 — Fechar a lacuna do Hub v2 — **S** — depende de A2

`notas.md` §17 cobrindo Sprints 0-8, migrations 016-020 e o Graph Studio: **o
que foi construído, o que ficou ligado, o que ficou inerte**. Substituir as
referências aos planos `C:\Users\User\...` por conteúdo real no repo ou por uma
nota honesta de "fonte perdida". Resolve I-07 e I-08.

### A5 — Inventário de código morto — **S** — sem dependências

Produzir `docs/inventario_codigo_morto.md` a partir da tabela §0.3, com o
comando de verificação de cada item. **Nada é apagado nesta trilha** — a decisão
por item fica sua, e a de `src/graph/` fica para a Trilha C.

---

## TRILHA B — Estabilizar o agente de suporte técnico para produção

*Prioridade máxima. Em qualquer conflito de tempo, B ganha de C.*

### B1 — 🔴 O portão de entrada (o item mais importante deste plano) — **L** — bloqueante

**O fato.** Hoje existem duas travas empilhadas e ambas estão erradas para o seu
alvo:

```
process_message_task.py:328   if remote_jid != settings.ALLOWED_GROUP_ID: return
process_message_task.py:353   if decision.target == IGNORE: decision.target = LLM
```

A primeira descarta **toda** mensagem que não venha do grupo de teste. Um aluno
mandando DM para o número da CTIC é ignorado em silêncio. A segunda desativa
**todos** os filtros do `gatekeeper.py` para o que passa da primeira.

**A consequência que importa para você.** O caminho intuitivo — "é só apagar a
linha 328" — é exatamente o cenário que você descreveu como inaceitável:
com a 328 removida e a 353 no lugar, **qualquer número do mundo que mande
mensagem para a CTIC chega ao RAG e queima tokens**, porque o
`privado_bloqueado_no_beta` do gatekeeper (`gatekeeper.py:73`) é reescrito para
`LLM` três linhas depois. As duas travas precisam ser trocadas **na mesma
mudança**, nunca uma sem a outra.

**Passos ordenados:**

1. Entender por que o override da 353 foi introduzido (arqueologia em `git log -S`
   + `notas.md`) — pode haver um caso legítimo escondido. **Não remover às cegas.**
2. Definir a política de admissão explicitamente, com a liderança se possível,
   mas com um default seguro se não: *quem pode consumir token?* O `pessoas` +
   `Porteiro` + `domain/permissions.py` já existem e já foram desenhados para
   isso — a política é escolha de dados, não de código novo.
3. Substituir a linha 328 por esse gate de identidade, mantendo o grupo de
   homologação como um caso permitido a mais, não como *o* filtro.
4. Remover o override da 353 e deixar `IGNORE` significar `IGNORE`.
5. Reabilitar o funil de cadastro real (`DEV_TEST_NO_DB_WRITE=false`,
   `DEV_TEST_SKIP_REGISTRATION=false` — já estão `false` no `.env`, confirmar que
   o funil grava em `pessoas` de verdade).
6. Rate limit por telefone **antes** da chamada LLM (o `InputGuardrail` já roda
   em `dispatcher_langgraph.py:316`; confirmar que o limite é por pessoa e não
   global).
7. Teste de admissão: número desconhecido → resposta de "não autorizado", **zero
   tokens gastos**, uma linha em `audit_log`.

### B2 — Ingestão real do wiki CTIC — **M** — depende de B1 (não tecnicamente; por prioridade)

1. Recriar o índice com `sistema`/`modulo` (`FT.DROPINDEX idx:rag:chunks DD` +
   reingestão). **Destrutivo — precisa da sua autorização explícita e de um
   snapshot antes.** É a pendência #1 de `arquitetura_oraculo.md` §11.
2. Rodar `discovery.descobrir_paginas()` contra o wiki real e ingerir a base
   inteira (hoje só houve ingestão pontual).
3. Curar `KNOWN_SYSTEM_HUBS` (`hierarchy.py`) com os sistemas reais da CTIC —
   sem isso tudo cai em `"Geral"/"Geral"` e o filtro por sistema não serve.
4. Agendar `descobrir_paginas` no Celery beat, no padrão de
   `beat_nightly_memory_sync`.
5. Guardrail de tag: um teste que falha se um chunk for gravado com
   `tipo_doc` default. **Este é o bug do `notas.md` §4** — `salvar_chunk()`
   defaultava `tipo_doc = doc_type.capitalize()` e o worker filtrava por
   `"Contatos"` contra chunks gravados como `"Geral"` → zero chunks, sempre.
   Foi corrigido por retag pontual, nunca por proteção estrutural. Vai voltar
   na reingestão se ninguém travar.

### B3 — Golden set de suporte técnico + eval — **M** — depende de B2

O `EVAL_DATASET` em `eval_api.py` é acadêmico (calendário, matrícula de
veteranos) — não serve para o produto. Construir 30-50 casos de FAQ de suporte
técnico da CTIC incluindo, conforme a prática de 2026: perguntas comuns, bordas
de permissão, **casos sem resposta** (o agente precisa saber dizer "não sei"), e
qualquer falha real observada.

Portões: **groundedness ≥ 0,75** (mirar 0,85), nenhum caso de alto risco falhando,
latência p95 dentro do limite que você definir. Rodar no CI junto do
`test_ctic_wiki_eval.py` que já existe.

### B4 — Reduzir a superfície do lançamento — **M** — paralelo a B2

Você disse que as rotas vieram de achismo. Para o lançamento, **menos rotas é
mais confiável**, e o `route_registry` permite isso sem apagar código:

- **Manter ligadas:** `WIKI`, `GERAL`, `GREETING`, `CHECK_STATUS`.
- **Desligar para o lançamento:** `SIGAA` (Playwright + coleta de CPF/senha via
  HITL — o maior risco de segurança e a maior superfície do repo),
  `MEDIA_DOWNLOAD` (YouTube/Instagram não é suporte técnico),
  `TICKET_ABERTURA`/`CRUD` (GLPI é stub — abrir chamado falso é pior que não
  oferecer), e as acadêmicas `CALENDARIO`/`EDITAL`/`CONTATOS` até que alguém
  confirme que são requisito.
- Cada uma volta trocando uma linha no `/hub/routes`. **Nada é apagado.**

Isso também é o que torna B5 barato.

### B5 — Ligar `FEATURE_LANGGRAPH_NATIVE_ROUTES` e aposentar o dispatcher legado — **M** — depende de B4

Com SIGAA e MEDIA_DOWNLOAD fora do lançamento, sobram GREETING e CHECK_STATUS
como rotas condicionais — as duas mais simples do grafo. Ligar a flag, validar em
homologação, e `dispatcher.py` deixa de ser chamado. Resolve TD-001 e TD-002 de
fato, não no papel. `docs/historico/aposentadoria_dispatcher_legado.md` já mapeou
os pré-requisitos de código; falta a janela de validação.

### B6 — Higiene operacional — **S** — paralelo a tudo

- **TD-010:** fixar `GEMINI_MODEL` em versão estável, não *preview*.
- `DEV_MODE=false` no `.env` de produção (hoje `true`; expõe `/api/docs`).
- **TD-009:** medir o pico de RSS do `worker_media` sob STT/TTS e ajustar
  `mem_limit` — hoje 768m, o mesmo valor que já causou OOM-kill.
- **TD-005:** decidir se `langgraph`/`mcp`/`kokoro` continuam na imagem.
- Tornar os 3 testes não herméticos explícitos (`@pytest.mark.integration`) para
  a suíte local voltar a ser verde sem Docker.

### B7 — Fechar o laço da telemetria — **S** — depende de B1

A telemetria funciona; ninguém olha. Falta:
- Um painel de **custo por rota e por pessoa** (os dados já estão em
  `metricas_llm`) — é o instrumento do controle de gasto que você pediu.
- Um alerta de gasto diário em `observability/alert_rules.yml`.
- **TD-016:** `llm_circuit_breaker.status()` itera uma tupla hardcoded e ignora
  provedores dinâmicos — trocar por `llm_provider_registry.registrados()`.

### B8 — Critérios objetivos de "pronto para produção"

Lançar quando **todos** forem verdadeiros:

1. Número não autorizado recebe recusa e gasta **zero token** (teste automatizado).
2. Todo acesso concedido tem linha em `audit_log` com identidade resolvida.
3. Base do wiki CTIC ingerida por inteiro, com `sistema`/`modulo` populados; um
   teste falha se um chunk sair com `tipo_doc` default.
4. Golden set de 30-50 casos com **groundedness ≥ 0,75** e nenhum caso de alto
   risco falhando.
5. O agente responde "não sei / abra um chamado" em vez de inventar, verificado
   nos casos sem resposta do golden set.
6. `GEMINI_MODEL` estável, `DEV_MODE=false`, `DEV_TEST_*=false` — confirmados no
   container em execução, não só no `.env`.
7. Custo diário visível em painel + alerta configurado.
8. Rota de rollback escrita: como desligar o agente em < 5 minutos.
9. Suíte `tests/unit` verde no CI; `test_ctic_wiki_eval` verde.

**Fica para depois sem quebrar compromisso:** API do sistema integrado / GLPI
real, SIGAA, Vision, multi-canal, multi-tenancy, e toda a Trilha C.

---

## TRILHA C — Decisão sobre o "hub" / plugin system

### Recomendação: **pausar `src/graph/` como construtor de fluxo; manter o Hub e aproveitar a UI.**

**Por que pausar, e não continuar.** Não é sobre a qualidade do código — é sobre
onde ele se conecta. `src/graph/` é um sistema de nós **paralelo ao LangGraph**,
sem um único import da biblioteca. Para ele um dia significar alguma coisa, o
`GraphExecutor` teria que **substituir** o `StateGraph` — jogando fora
checkpointing, `interrupt()`/resume e todo o HITL que finalmente funciona — ou o
`StateGraph` teria que ser montado a partir das topologias, o que a pesquisa de
2026 aponta como a decisão mais arriscada de um projeto LangGraph (schema de
state dinâmico = sobrescrita silenciosa, indepurável). Nenhum dos dois caminhos
é defensável agora. Hoje são 2.584 linhas + 3 migrations + um canvas cujo único
efeito no mundo é um botão de teste.

**E não é descartar trabalho — é reconhecer que a parte que valeu já está em
produção.** A "plataforma orientada a configuração" que você queria **existe e
funciona**: `route_registry` (rota→execução como dado, editável em runtime),
`agentes_catalogo`, `agent_prompts`, `llm_providers` (trocar provedor sem
restart), `tools_catalogo`, `llm_pricing`. Isso é low-code de verdade, no caminho
quente. O Graph Studio é a versão que parece com a ideia mas não toca nela.

### C1 — Reposicionar o Graph Studio de "editor" para "espelho" — **M** — depende de A1

A UI é boa e o inventário de nós tem valor real. Mudar o que ela **afirma**:

- Deixar de vender "construir pipeline" e passar a mostrar **o grafo de produção
  que existe** — os nós reais de `langgraph_experiment/graph.py` e as edges
  condicionais, em leitura. Isso é útil de verdade: hoje ninguém consegue ver o
  grafo sem ler Python.
- Manter `graph-nodes` como catálogo/health de componentes (já é consumido por
  `system_health.py` — esse consumo é legítimo).
- Rotular explicitamente o canvas como **experimental, sem efeito em produção**.
  Hoje um operador clica "Testar" e não tem como saber que aquilo não é o
  sistema.
- Congelar `FEATURE_GRAPH_EXECUTOR_PILOTO`; não implementar `modo="producao"`.

### C2 — Investir a energia do "configurável" onde ela já rende — **S/M** — depende de B5

Se quiser continuar a ideia de central configurável, o caminho de maior retorno é
**aprofundar o `route_registry`**, não construir um segundo motor: expor no Hub
o `k`, `doc_type`, `cacheavel` e o prompt por rota; mostrar o efeito da mudança
no eval do golden set. Isso é "editar o fluxo pelo painel" com risco baixo,
porque o grafo permanece estático e só os parâmetros mudam.

### C3 — Decisão sobre as 2.584 linhas — **S** — depende de C1 e da sua decisão

Depois de C1, decidir por item de A5: manter isolado, ou remover
`graph_executor`/`topology_*`/`reference_flows` mantendo `base_node`/
`node_registry`/`node_health`/`mcp_server_registry` (que têm consumidor real).
**Nenhuma remoção sem sua confirmação explícita.**

---

## Dependências entre trilhas

```
A1 ─┬─> A3        (documentar depois de ter a verdade)
    ├─> A4
    └─────────────────────────> C1   (reposicionar exige o escopo declarado)
A2 ──> A4
A5 ─────────────────────────────> C3

B1 (bloqueante) ──> B7
B2 ──> B3
B4 ──> B5 ──> C2

B6 corre em paralelo a tudo.
Trilha C não bloqueia nada em B. B tem prioridade sobre C sempre.
```

**Caminho crítico até produção:** B1 → B2 → B3 → B8. As demais são
paralelizáveis.

**Ordem sugerida se você tiver pouco tempo:** A1 (meio dia, destrava tudo) →
B1 → B2 → B3 → B6 → B8. Trilha C depois do lançamento.

---

## Verificação

Como confirmar que cada trilha funcionou, sem depender de opinião:

**Trilha A** — `find docs -name '*.md'` cruzado com o índice de `docs/README.md`
não deve deixar órfão. Um leitor novo (ou uma sessão de IA sem contexto) lê só o
`ESTADO_ATUAL.md` e responde: qual o produto, o que está em produção, o que está
atrás de flag, o que é inerte.

**Trilha B** — com o stack no ar (`docker compose up -d` com
`COMPOSE_PROFILES=core,monitoring,app,gateway`):
1. `docker compose exec api pytest tests/unit -q` → verde.
2. `pytest tests/eval -q` → verde, incluindo o novo golden set.
3. Mensagem de número **não** cadastrado ao número da CTIC → recusa, `metricas_llm`
   sem linha nova, `audit_log` com linha nova.
4. Mensagem de número cadastrado com pergunta do wiki → resposta ancorada, com o
   trecho de origem; `/hub/llm-custo` mostra o custo daquela chamada.
5. Pergunta fora da base → "não sei / abra um chamado", não invenção.
6. `docker compose logs worker | grep -i "🧪 \[LANGGRAPH\]"` mostra a rota e o nó
   escolhidos.

**Trilha C** — abrir `/hub/graph-studio` e o rótulo deve deixar claro, sem ler
código, que o canvas não é o sistema em produção.

---

## FASE 3 — Aguardando aprovação

Nada foi editado. Preciso saber **quais trilhas e itens você aprova para
execução**, e em particular duas autorizações destravantes:

- **B2 passo 1** é destrutivo (`FT.DROPINDEX ... DD` + reingestão). Só executo com
  autorização explícita e depois de um snapshot.
- **B1 passo 4** (remover o override `IGNORE → LLM`) muda comportamento de
  segurança. Quero fazer a arqueologia do "por quê" antes e te mostrar o achado.
