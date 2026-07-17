# Plano de Refatoração: God Object → Padrão Supervisor

## Projeto Oráculo — `router/` + `agents/` + `capabilities/`

> **Status:** documento de planejamento, revisão 2 (incorpora revisão crítica de nomenclatura e camadas). Nenhuma alteração de código foi feita nesta fase — apenas varredura read-only de `src/`, `docker-compose.yml` e `arquitetura_oraculo.md`.

---

## 0. Decisão Arquitetural Prévia: onde ficam `router/`, `agents/`, `capabilities/`?

**Recomendação: pastas de topo-nível dentro de `src/`, paralelas a `domain/`, `application/`, `infrastructure/` — NÃO aninhadas dentro da Clean Architecture existente.**

Justificativa:

- O padrão Supervisor é um **padrão de orquestração de runtime**, ortogonal às camadas Clean Architecture (que descrevem direção de dependência de regras de negócio, não fluxo de execução de agentes). Forçar `agents/` dentro de `application/` ou `capabilities/` dentro de `infrastructure/` reproduziria o mesmo problema atual: cada "agente" teria que decidir se é regra de negócio (application) ou IO (infrastructure), e a experiência mostra que essa linha já foi violada dezenas de vezes (`registration_funnel.py` fazendo SQL cru na application, `synthesis_service.py` com prompt de negócio na infrastructure).
- Um `src/agents/<nome>/` isolado por especialista permite que cada agente tenha sua própria micro-Clean-Architecture interna se precisar (ex: `agents/sigaa/domain.py` para regra de elegibilidade + `agents/sigaa/service.py`), sem forçar todo o projeto a essa granularidade.
- `router/` vira o único ponto de entrada de decisão "qual agente chamar" — precisa ser importável por `application/tasks/process_message_task.py` sem depender de nada de `agents/` internamente (só de contratos/enum de nomes de agente + o Registry, ver 0.2), o que é mais simples de garantir como pacote de topo-nível do que soterrado em `application/routing/`.
- **Renomeação `tools/` → `capabilities/`** (revisão pós-crítica): a pasta de topo nível **não se chama `tools/`**. Hoje praticamente todo framework de LLM (function calling, MCP, OpenAI tool calling) usa "Tool" com um significado muito específico — *LLM → tool call → executa função*. O que esta pasta contém (SQL, Playwright, Redis, mensageria) não é isso: são adapters/serviços de domínio atômicos consumidos pelos agentes, não invocados diretamente por um LLM via function-calling. Chamar isso de `tools/` cria confusão garantida no dia em que o projeto adicionar MCP ou tool-calling nativo do Gemini. Toda referência a `tools/` neste documento foi substituída por **`capabilities/`**.
- `capabilities/` de topo nível deixa claro, visualmente e por convenção de import (`from src.capabilities.sigaa import ...`), que **capabilities não têm lógica de decisão** — são a nova "infrastructure fina" convocável por qualquer agente. Isso não elimina `infrastructure/` (adapters de Evolution API, Postgres, Redis client continuam lá como portas técnicas de baixo nível); `capabilities/` é a camada de **funções de negócio atômicas e burras** que embrulham 1+ adapters de infra para uma operação específica (ex: `capabilities/sigaa/scrape_historico.py` usa Playwright + `infrastructure/scraping` por baixo).

### 0.1b Achado durante a Fase 3: `src/services/` já existe e NÃO é a camada acima

Durante a execução (não no levantamento read-only original, que nunca varreu essa pasta), descobriu-se que **`src/services/` já existe como pacote de topo nível legado**, com `email_service.py`, `evolution_service.py` (`EvolutionService`, ativamente importado por `main.py`, `application/tasks/ingestion_tasks.py` e `tasks_admin.py`) e `registration_service.py` (530 linhas, **zero consumidores** — código morto adicional não capturado no levantamento original). Isso é relevante por dois motivos:

1. **Confirma a decisão de nomear a nova camada `capabilities/` em vez de `services/`**: já existe uma colisão de nome real, e pior, `evolution_service.py` parece fazer o mesmo papel que `infrastructure/adapters/evolution_adapter.py` (ambos clientes da Evolution API) — ou seja, há uma duplicação de implementação pré-existente que o plano original também não capturou.
2. **Fica fora do escopo deste roadmap de 8 fases**: consolidar `services/evolution_service.py` com `infrastructure/adapters/evolution_adapter.py`, e decidir o destino de `services/registration_service.py` (morto) e `services/email_service.py`, é trabalho novo, não coberto pelas Fases 0-7 originais. Fica registrado aqui como item de limpeza futura — não bloqueia a Fase 6 (`capabilities/messaging/`), que consome apenas `infrastructure/adapters/evolution_adapter.py` conforme já planejado em 2.6.

Estrutura resultante:

```
src/
  router/            # Supervisor: decide o agente, sem IO pesada nem regra de negócio
  agents/
    academic_knowledge/  # RAG acadêmico + synthesis (ex "academic_rag" — RAG é a técnica, não o papel do agente)
    sigaa/                # especialista SIGAA (elegibilidade, formatação, orquestra capabilities de scraping)
    conversation/         # saudação, boas-vindas, funil de cadastro (ex "chat" — todo agente conversa, isso é a função de onboarding)
    tickets/              # GLPI/ações administrativas (ex "action" — nome genérico demais; concretamente é abertura de chamado)
    base.py               # BaseAgent (contrato comum) + AgentContext, ver 0.2
    registry.py           # Agent Registry, ver 0.2
  capabilities/
    sigaa/             # scraping cru (Playwright), sem decisão
    rag/               # embeddings, KNN Redis, RRF puro
    messaging/         # Evolution API, envio de mídia
    persistence/       # SQL/ORM atômico reutilizável
  domain/              # entidades, exceções, portas (contratos puros) — mantém-se
  application/          # runtime/orquestração fina: process_message_task, dispatch Celery, guardrails
  infrastructure/       # adapters técnicos genéricos (DB engine, Redis client, Gemini provider, Evolution adapter)
```

`application/` não desaparece: continua sendo o "sistema nervoso" que conecta webhook → router → agents → capabilities → resposta, mas deixa de conter lógica de negócio ou chamadas LLM diretas.

### 0.1 Fluxo de execução: Supervisor é o `router/`, não uma camada acima dele

Ponto de atenção levantado na revisão: **não existem dois componentes concorrentes aqui.** `router/supervisor.py` já É o Supervisor — o componente central que decide qual agente chamar. Não há benefício em inserir uma camada extra "Supervisor → Router" acima dele; isso só reproduziria a indireção que o próprio `cognitive_os.py` hoje representa (2.2). O fluxo permanece:

```
webhook → application/tasks/process_message_task.py
            ↓
          router.supervisor.decide(context)   # Supervisor: 5 camadas (regex, heurística, regex seeded, KNN Redis, fallback LLM)
            ↓  (retorna nome do agente)
          agents.registry.resolve(nome)         # Registry: nunca importado diretamente pelo router
            ↓
          agent.execute(context)                # Agent: recebe SEMPRE um único AgentContext
            ↓
          application/runtime/dispatcher.dispatch(...)  # Dispatcher: monta a chain Celery, executa
            ↓
          resposta
```

O `router/` **nunca importa uma classe de agente diretamente** — ele resolve por nome via `agents/registry.py`. Isso permite adicionar um agente novo sem tocar no router.

### 0.2 `BaseAgent`, `AgentRegistry` e `AgentContext`

Peça que faltava no desenho original: um contrato comum para todo agente, um registro central que o router consulta por nome, e um objeto de contexto único injetado em toda execução (em vez de parâmetros soltos tipo `execute(user, redis, memory, identity)`).

```python
# src/agents/base.py
class AgentContext:
    identity: ...        # quem é o usuário (matrícula, papel: aluno/servidor/professor)
    permissions: list[str]
    conversation: ...     # histórico da conversa corrente
    memory: ...           # memória de curto/longo prazo já carregada
    redis: ...            # client injetado, nunca importado direto por agente
    postgres: ...          # sessão/engine injetada
    llm: ...               # provider LLM configurado (Gemini etc.)
    config: dict           # overrides administrativos (admin:system_prompt, admin:gemini_blocked, ...)
    session_id: str

class BaseAgent(Protocol):
    name: str
    description: str
    permissions: list[str]

    def can_execute(self, context: AgentContext) -> bool: ...
    async def execute(self, context: AgentContext) -> AgentResponse: ...
```

```python
# src/agents/registry.py
class AgentRegistry:
    def register(self, agent: BaseAgent) -> None: ...
    def resolve(self, name: str) -> BaseAgent: ...
    def all(self) -> list[BaseAgent]: ...

registry = AgentRegistry()
registry.register(AcademicKnowledgeAgent())
registry.register(SigaaAgent())
registry.register(ConversationAgent())
registry.register(TicketAgent())
```

Regra de assinatura para **todo** agente novo: sempre `execute(context: AgentContext)`, nunca `execute(user)`, `execute(message)`, `execute(redis)` etc. soltos. Isso é o que permite, no futuro, adicionar um agente novo (ex.: `BibliotecaAgent`) só implementando `BaseAgent` e chamando `registry.register(...)` — sem tocar `router/`, `application/tasks/process_message_task.py` nem os workers Celery.

Esta peça entra no roadmap na **Fase 2** (junto com `router/`, que passa a depender do Registry em vez de importar agentes) e é consumida por todas as fases seguintes (3 a 6) à medida que cada agente é criado.

### 0.3 Nota sobre escopo: fora deste plano

A revisão que motivou esta reescrita também levantou uma visão de produto maior — transformar o Oráculo numa **plataforma de administração de agentes** (catálogo de agentes/tools configurável via banco, prompts editáveis sem rebuild, painel admin estilo Dify/LangSmith/N8N). É uma direção legítima e o desenho de `router/contracts.py` + `agents/registry.py` acima **não fecha essa porta** — um Registry que hoje resolve por nome fixo em código pode evoluir para ler configuração de banco depois, sem reescrever o `router/`. Mas isso é explicitamente **fora do escopo deste plano**: o Oráculo é um bot WhatsApp em produção com filas Celery ativas, e o objetivo aqui é sair do God Object atual com o menor risco operacional possível. Priorizar uma plataforma de administração completa antes de estabilizar este refactor multiplicaria o risco sem necessidade. Fica registrado como direção de evolução futura (pós Fase 7), não como parte do roadmap abaixo.

---

## 1. Mapeamento de Onde Estamos — Ranking de Severidade

| # | Arquivo | Linhas | Severidade | Justificativa |
|---|---|---|---|---|
| 1 | `src/infrastructure/scraping/implementations/sigaa_agent.py` | 966 | CRÍTICA | Maior arquivo do projeto; mistura automação Playwright crua com decisão de negócio (elegibilidade, texto de mensagem) no mesmo método; duplica lógica com `worker_sigaa.py` |
| 2 | `src/application/chain/cognitive_os.py` | 805 | CRÍTICA | God Object central: roteamento + HITL + validação CPF/senha + dispatch Celery + IO Redis cru, tudo em uma função de ~500 linhas |
| 3 | `src/application/tasks/process_message_task.py` | 624 | CRÍTICA | Mini-orquestrador paralelo ao cognitive_os; concentra identidade, envio, memória, roteamento, guardrails, métricas numa árvore só |
| 4 | `src/application/workers/worker_sigaa.py` | 557 | ALTA | Instancia agente e formata mensagem de negócio dentro do worker Celery; duplica cálculo de elegibilidade com `sigaa_agent.py` |
| 5 | `src/infrastructure/services/rag_search_service.py` | 611 | ALTA | `QueryTransformService` chama Gemini para decidir reescrita de query dentro de um "serviço de infra"; estratégia de retrieval (RRF/step-back) é lógica de orquestração disfarçada de IO |
| 6 | Router triplicado: `application/routing/semantic_router.py` (vivo) + `domain/services/oraculo_router.py` (morto) + `domain/services/semantic_router.py` (morto, viola Clean Arch) + `infrastructure/services/router_service.py` (morto) | 381+281+239+259 | ALTA | Três a quatro fontes de verdade concorrentes para a mesma decisão de roteamento; código morto aumenta risco de manutenção e confusão de onboarding |
| 7 | `src/application/workers/worker_synthesis.py` | 408 | ALTA | Chamada Gemini + prompt engineering embutidos no worker, enquanto `SynthesisService` equivalente já existe mas é usado só por código órfão (`pipeline/workers.py`) |
| 8 | `src/infrastructure/services/synthesis_service.py` | 211 | ALTA | Prompt de política comportamental completo (`_SYSTEM_SYNTHESIS`) dentro de "infra service"; lê overrides administrativos do Redis — mistura config, prompt e infra |
| 9 | `src/application/chain/planner.py` | 361 | MÉDIA-ALTA | Planner Gemini Pro com regra de negócio (whitelist de workers) embutida no prompt; chamada LLM direta |
| 10 | `src/application/routing/llm_orchestrator.py` | 114 | MÉDIA | "Terceiro cérebro" de classificação NL, chamada Gemini direta, não conversa com `tool_registry` |
| 11 | `src/domain/tools/tool_registry.py` | 291 | MÉDIA | Código morto (zero consumidores); descriptions de tool duplicam prompt do `semantic_router` (duas fontes de verdade para intenção) |
| 12 | `src/domain/tools/crud_tools.py` | 83 | MÉDIA | Segundo mecanismo de registro de tools (dict + decorator) paralelo ao `ToolRegistry` |
| 13 | `src/application/routing/registration_funnel.py` | 95 | MÉDIA | SQL cru + chamada direta a `EvolutionAdapter` (infra) na application layer, sem boundary |
| 14 | `src/domain/tools/admin_tools.py` | 63 | MÉDIA | `asyncio.run()` dentro de contexto async (anti-pattern); toca ORM direto do domain |
| 15 | `src/application/workers/worker_memory_manager.py` | 129 | BAIXA-MÉDIA | Chamada Gemini direta para sumarização, sem passar por um serviço dedicado |
| 16 | `src/application/workers/worker_action.py` | 103 | BAIXA-MÉDIA | UPDATE SQL cru direto no worker; stub de integração GLPI |
| 17 | `src/application/chain/reranker.py` | 99 | BAIXA | Probe de rede (`urllib.request.urlopen`) embutido no carregamento de modelo — deveria ser só load puro |
| 18 | `src/application/pipeline/workers.py` (código órfão) | — | BAIXA (mas ruído) | Não segue convenção `worker_*.py`, não é autodescoberto, re-registra `"synthesis"` duplicado — candidato a exclusão pura |

**Bons modelos a preservar como referência de estilo:**

| Arquivo | Por quê |
|---|---|
| `src/application/routing/message_router.py` | Gatekeeper regex puro, sem IO/LLM |
| `src/application/chain/guardrails.py` | Isolado, só recebe Redis por injeção de dependência |
| `src/domain/tools/gmail_tool.py` | Tool "burra" — `StructuredTool` fino sobre `gmail_service` |
| `src/infrastructure/scraping/base_scraper.py` / `generic_scraper.py` | Template Method limpo (fetch/parse abstratos, pipeline fixo) |
| `src/application/workers/registry.py` | Autodiscovery via `pkgutil` + decorator, mecanismo de plugin limpo — mesmo padrão reaproveitado por `agents/registry.py` (0.2) |
| `src/application/workers/worker_rag_search.py`, `worker_db_connector.py`, `worker_graph_extractor.py` | Delegam corretamente a um service de infra, worker fica fino |

---

## 2. Nova Estrutura de Diretórios Proposta — Mapeamento Arquivo a Arquivo

### 2.1 `router/` — Consolidação das 3(+1) implementações duplicadas

**Decisão: usar `src/application/routing/semantic_router.py` (381 linhas, 5 camadas, EM USO em produção) como base do novo `src/router/supervisor.py`.**

Razão: é o único caminho realmente exercitado pelo sistema hoje; reescrever do zero usando os routers mortos (mesmo sendo mais "limpos" no papel) introduziria risco de regressão sem necessidade, já que eles nunca foram testados em produção com tráfego real.

O que aproveitar dos mortos antes de deletá-los:

- **`domain/services/oraculo_router.py`** (281 linhas): arquitetura de 3 camadas async com **validação Pydantic da saída do LLM** — esse padrão de validação estruturada (evitar parsing de string livre da resposta do Gemini) deve ser **portado** para dentro do novo `router/llm_fallback.py`, substituindo o parsing atual mais frágil de `semantic_router.py` / `llm_orchestrator.py`.
- **`domain/services/semantic_router.py`** (239 linhas): padrão de **DI explícita** (redis/embeddings injetados no construtor, sem import direto de `redis.asyncio`) — aproveitar essa assinatura de construtor para `router/supervisor.py`, corrigindo a violação atual em que o router vivo faz chamada Gemini embutida diretamente.
- **`infrastructure/services/router_service.py`** (259 linhas): descartar sem aproveitamento — é uma duplicação quase 1:1 do `semantic_router.py` da application, sem melhoria adicional identificada.

Novo desenho de `router/`:

```
src/router/
  __init__.py
  supervisor.py        # decide qual AGENT_NAME chamar (via agents/registry.py); 5 camadas (regex, heurística, regex seeded, KNN Redis, fallback LLM)
  llm_fallback.py       # chamada Gemini Flash para classificação, com validação Pydantic da resposta (portado de oraculo_router.py)
  contracts.py          # Enum/dataclass de nomes de agentes + schema de decisão de rota (substitui a whitelist embutida no prompt do planner.py)
  gatekeeper.py         # ex message_router.py (regex puro, gate de entrada) — mantém-se quase igual, só muda namespace de import
```

Mapeamento de origem:

- `src/application/routing/semantic_router.py` → `src/router/supervisor.py` (refatorado: chamada Gemini extraída para `llm_fallback.py`; import de `sigaa_use_cases` removido — router não deve importar domain use-case, deve só retornar um nome de agente resolvido via `agents/registry.py`, ver 0.2)
- `src/application/routing/llm_orchestrator.py` → mesclado dentro de `router/llm_fallback.py` (elimina o "terceiro cérebro" paralelo)
- `src/application/routing/message_router.py` → `src/router/gatekeeper.py` (praticamente inalterado — é o modelo de estilo)
- `src/domain/services/oraculo_router.py` → **DELETAR** após portar o padrão de validação Pydantic
- `src/domain/services/semantic_router.py` → **DELETAR** após portar o padrão de DI
- `src/infrastructure/services/router_service.py` → **DELETAR** sem aproveitamento
- `src/domain/tools/tool_registry.py` → **DELETAR** as `description=` de tool (fonte de verdade duplicada); qualquer schema de tool reaproveitável migra para `capabilities/registry.py` (ver 2.5), mas sem texto de intenção — só assinatura/contrato de IO

### 2.2 `cognitive_os.py` — Decomposição

`cognitive_os.py` (805 linhas) se decompõe em 4 destinos:

1. **Roteamento** (decisão de qual agente/fluxo chamar) → absorvido por `router/supervisor.py` (já cobre isso hoje via chamada a `semantic_router`; `cognitive_os` hoje decide em cima do resultado do router, então essa decisão condicional também migra para dentro do supervisor como parte do contrato de saída).
2. **Dispatch de workers Celery** (`_despachar_workers`, chains/chords) → fica em `application/runtime/dispatcher.py`. Esta é a peça de "cola" que permanece em `application/` porque é puramente mecânica (monta uma chain Celery a partir de uma decisão já tomada) — não decide nada, só executa. Não vira `agents/` nem `router/`.
3. **Lógica de negócio específica** (HITL, validação de CPF/senha SIGAA) → migra para `agents/sigaa/auth_flow.py` (fluxo de autenticação/HITL é conhecimento específico do domínio SIGAA, não genérico de orquestração).
4. **IO Redis cru** (get/setex/delete/xadd/xread espalhado) → extraído para `capabilities/persistence/redis_state.py`, uma service burra de "gerenciamento de estado conversacional" (set/get/expire de estado de sessão) consumida tanto pelo dispatcher quanto pelos agentes que precisam de HITL, e exposta a todo agente via `AgentContext.redis` (0.2).

Resultado: `cognitive_os.py` deixa de existir como arquivo único; vira 4 arquivos pequenos e coesos, cada um em sua pasta correta. `application/tasks/process_message_task.py` passa a chamar `router.supervisor.decide()` → `application/runtime/dispatcher.dispatch()` diretamente, eliminando a camada extra de indireção que `cognitive_os.processar()` hoje representa.

### 2.3 SIGAA: separar `sigaa_agent.py` (966 linhas) em capabilities puras vs agente especialista

- **`agents/sigaa/`**:
  - `service.py` — orquestra o fluxo: decide QUAL scraping rodar, calcula elegibilidade de matrícula/próximo semestre (hoje duplicado entre `worker_sigaa.py` e `sigaa_agent.py` — consolidar num único lugar aqui), formata mensagens de resposta ao usuário. Implementa `BaseAgent` (0.2), registrado como `SigaaAgent` em `agents/registry.py`.
  - `auth_flow.py` — fluxo HITL de autenticação (CPF/senha), migrado de `cognitive_os.py` conforme 2.2.
  - `eligibility.py` — regra pura de negócio (cálculo de elegibilidade de semestre), extraída e testável isoladamente sem precisar mockar Playwright.
- **`capabilities/sigaa/`** (burras, atômicas, sem decisão):
  - `scrape_login.py`, `scrape_historico.py`, `scrape_turmas.py`, `scrape_biblioteca.py` — cada função recebe credenciais/parâmetros e devolve dados crus (HTML parseado / dict), sem decidir mensagem nenhuma. Extraídas método a método de `sigaa_agent.py` (ex: `fluxo_a_biblioteca` é dividido: a parte Playwright fica em `capabilities/sigaa/scrape_biblioteca.py`, a parte de decisão de mensagem vai para `agents/sigaa/service.py`).
  - Reaproveita a infraestrutura Playwright existente em `infrastructure/scraping/` (Template Method de `base_scraper.py`/`generic_scraper.py`, já limpos, permanecem como estão e são consumidos por essas capabilities).
- `src/application/workers/worker_sigaa.py` (557 linhas) → reduzido para um worker fino que só chama `agents/sigaa/service.py` e devolve o resultado ao Celery — igual ao padrão já usado em `worker_rag_search.py` (bom modelo citado na auditoria).

### 2.4 Agente "Academic Knowledge" (RAG/Synthesis) coeso

Consolidar `synthesis_service.py`, `worker_synthesis.py`, `planner.py`, `worker_memory_manager.py`, e a parte de decisão de `rag_search_service.py` num único `agents/academic_knowledge/` (renomeado de "academic_rag": RAG é a *técnica* usada internamente, não o papel do agente — amanhã pode ganhar retrieval não-RAG sem deixar de ser o mesmo agente):

```
src/agents/academic_knowledge/
  service.py          # orquestra: recebe pergunta → decide reescrita de query → retrieval → synthesis → resposta; implementa BaseAgent, registrado como AcademicKnowledgeAgent
  synthesis.py         # ex synthesis_service.py, SEM leitura de Redis embutida (prompt fica em prompts.py)
  prompts.py            # _SYSTEM_SYNTHESIS e demais prompts, extraídos como dados versionáveis, não lógica
  query_transform.py    # ex QueryTransformService de rag_search_service.py (decide reescrita de query via Gemini)
  memory_summarizer.py  # ex worker_memory_manager.py (chamada Gemini de sumarização), viabiliza reuso fora do worker
  planning.py           # ex planner.py, mas a whitelist de workers vira router/contracts.py (fonte única), não mais no prompt

src/capabilities/rag/
  retrieval.py    # RRF, KNN Redis, step-back — só a MECÂNICA de busca, recebendo já a query decidida
  embeddings.py    # geração de embeddings pura
```

- `infrastructure/services/rag_search_service.py` (611 linhas) → dividido: a parte "decide o que buscar e como combinar" (RRF/fallback step-back, `buscar()`) vira `agents/academic_knowledge/service.py`; a parte de IO pura (chamadas KNN Redis) vira `capabilities/rag/retrieval.py`.
- `worker_synthesis.py` (408 linhas) → reduzido a um wrapper Celery fino chamando `agents/academic_knowledge/service.py`, eliminando a chamada Gemini duplicada hoje existente no worker.
- `application/pipeline/workers.py` (código órfão, duplica registro `"synthesis"`) → **DELETAR** — nenhuma migração necessária, é puramente morto e um risco de conflito de registro.
- Admin overrides do Redis (`admin:gemini_blocked`, `admin:system_prompt`), hoje lidos dentro de `synthesis_service.py` → migram para `capabilities/persistence/admin_config.py` (service burra de leitura de config), injetada em `agents/academic_knowledge/service.py` via `AgentContext.config` (0.2), não lida diretamente pela camada de prompt.

### 2.5 Consolidação do registro de capabilities + Agent Registry

- `domain/tools/tool_registry.py` (morto, `StructuredTool` factory) vs `domain/tools/crud_tools.py` (dict `_TOOL_REGISTRY` + decorator `@tool`, também paralelo) → consolidar num único `capabilities/registry.py`, adotando o mecanismo de **decorator + dict** de `crud_tools.py` por ser mais simples e já ter um padrão real de uso, mas sem embutir `description=` de intenção (essa responsabilidade já migrou para `router/contracts.py`, ver 2.1). O registry serve só para permitir que agentes descubram services disponíveis por nome, análogo ao `WorkerRegistry` do Celery (autodiscovery já citado como limpo — reaproveitar o mesmo padrão de `pkgutil` + decorator).
- Esse é o mesmo padrão de autodiscovery reaproveitado por `agents/registry.py` (0.2): um `AgentRegistry` central onde cada agente se registra (`registry.register(SigaaAgent())`), e o router só resolve por nome — nunca importa a classe do agente diretamente.
- `domain/tools/admin_tools.py` (`asyncio.run()` anti-pattern, toca ORM direto) → dividir: a query ORM pura (`Pessoa`, `select()`) vira `capabilities/persistence/admin_repository.py` (função async nativa, sem `asyncio.run()`); qualquer decisão de quando/por que chamar fica em `agents/conversation/` ou num futuro `agents/administration/` se justificado por volume.
- `domain/tools/gmail_tool.py` (bom exemplo de tool burra) → migra quase inalterado para `capabilities/messaging/gmail_tool.py`, mantendo o padrão `StructuredTool` fino sobre `gmail_service` de infra.

### 2.6 `registration_funnel.py` e `worker_action.py` → `agents/conversation/` e `agents/tickets/`

- `application/routing/registration_funnel.py` (SQL cru + chamada direta a `EvolutionAdapter`) → a máquina de estados de cadastro é lógica de negócio de "boas-vindas/onboarding", então migra para `agents/conversation/registration.py` (agente renomeado de "chat" para "conversation" — toda a interação é conversa, o que diferencia este agente é cuidar de saudação/onboarding/funil de cadastro); o SQL cru migra para `capabilities/persistence/registration_repository.py`; a chamada a `EvolutionAdapter` (envio de mensagem) passa a ser feita através de `capabilities/messaging/evolution_tool.py`, uma service fina que embrulha o adapter de infra já existente (`infrastructure/adapters/evolution_adapter.py` permanece como está — é o adapter técnico; a service é a camada que os agentes chamam).
- `application/workers/worker_action.py` (UPDATE SQL cru, stub GLPI) → o SQL vira `capabilities/persistence/ticket_repository.py`; a decisão de qual ação tomar (se/quando abrir chamado GLPI) migra para `agents/tickets/service.py` (renomeado de "action" para "tickets": o nome genérico "Action" não diz nada — concretamente este agente abre/consulta chamados GLPI; se amanhã ganhar outras integrações administrativas sem relação com chamados, aí sim justifica um agente novo em vez de generalizar o nome de volta).

### 2.7 Tabela-resumo (arquivo → destino)

| Arquivo Atual | Destino | Ação |
|---|---|---|
| `application/chain/cognitive_os.py` | `router/supervisor.py` + `application/runtime/dispatcher.py` + `agents/sigaa/auth_flow.py` + `capabilities/persistence/redis_state.py` | Decompor |
| `application/routing/semantic_router.py` | `router/supervisor.py` (base) | Refatorar |
| `application/routing/llm_orchestrator.py` | `router/llm_fallback.py` | Mesclar |
| `application/routing/message_router.py` | `router/gatekeeper.py` | Mover (quase inalterado) |
| `domain/services/oraculo_router.py` | (padrão Pydantic portado para `router/llm_fallback.py`) | Deletar após port |
| `domain/services/semantic_router.py` | (padrão DI portado para `router/supervisor.py`) | Deletar após port |
| `infrastructure/services/router_service.py` | — | Deletar |
| `application/chain/planner.py` | `agents/academic_knowledge/planning.py` + `router/contracts.py` (whitelist) | Decompor |
| `application/routing/registration_funnel.py` | `agents/conversation/registration.py` + `capabilities/persistence/registration_repository.py` | Decompor |
| `application/chain/guardrails.py` | `application/runtime/guardrails.py` | Mover (inalterado, bom modelo) |
| `application/chain/reranker.py` | `capabilities/rag/reranker.py` | Mover + remover probe de rede embutida |
| `domain/tools/tool_registry.py` | `capabilities/registry.py` (mecanismo) | Consolidar, deletar descriptions de intenção |
| `domain/tools/crud_tools.py` | `capabilities/registry.py` (mecanismo escolhido) | Consolidar |
| `domain/tools/admin_tools.py` | `capabilities/persistence/admin_repository.py` | Dividir, remover `asyncio.run()` |
| `domain/tools/gmail_tool.py` | `capabilities/messaging/gmail_tool.py` | Mover (inalterado) |
| `infrastructure/services/rag_search_service.py` | `agents/academic_knowledge/service.py` (decisão) + `capabilities/rag/retrieval.py` (IO) | Decompor |
| `infrastructure/services/synthesis_service.py` | `agents/academic_knowledge/synthesis.py` + `agents/academic_knowledge/prompts.py` | Decompor |
| `application/workers/worker_sigaa.py` | `application/workers/worker_sigaa.py` (fino) chamando `agents/sigaa/service.py` | Emagrecer |
| `infrastructure/scraping/implementations/sigaa_agent.py` | `capabilities/sigaa/scrape_*.py` (IO) + `agents/sigaa/service.py` + `agents/sigaa/eligibility.py` (decisão) | Decompor |
| `application/workers/worker_synthesis.py` | `application/workers/worker_synthesis.py` (fino) chamando `agents/academic_knowledge/service.py` | Emagrecer |
| `application/workers/worker_memory_manager.py` | `agents/academic_knowledge/memory_summarizer.py` + worker fino | Decompor |
| `application/workers/worker_action.py` | `agents/tickets/service.py` + `capabilities/persistence/ticket_repository.py` | Decompor |
| `application/pipeline/workers.py` | — | Deletar (código órfão) |
| `application/tasks/process_message_task.py` | `application/tasks/process_message_task.py` (emagrecido, chama router+dispatcher) | Refatorar |
| — (novo) | `agents/base.py` (BaseAgent, AgentContext) + `agents/registry.py` (AgentRegistry) | Criar |

---

## 3. Roadmap de Migração em Fases

Princípio geral: **desacoplar o Roteador primeiro**, manter o sistema sempre executável (strangler fig / branch by abstraction), cada fase termina com testes verdes e é revertível via `git revert` isolado por fase (commits por fase, sem squash). O sistema é um bot WhatsApp ativo com filas Celery em produção — **cada fase gera um PR isolado**, nunca duas fases no mesmo PR.

### Fase 0 — Preparação e rede de segurança (sem mudança funcional)
- **Objetivo**: garantir cobertura de teste mínima antes de mexer.
- **Arquivos tocados**: nenhum arquivo de produção; apenas `tests/`. Reforçar teste de smoke E2E (`tests/e2e/`) cobrindo: mensagem de saudação, mensagem acadêmica (RAG), mensagem SIGAA, mensagem HITL (CPF/senha).
- **Critério de sucesso**: suite completa (`pytest tests/`) passa 100% antes de qualquer refatoração; smoke E2E manual do fluxo webhook→resposta documentado como baseline.
- **Reversível**: sim (só adiciona testes).

### Fase 1 — Deletar código morto (risco zero)
- **Objetivo**: reduzir ruído e confusão antes de mover código vivo.
- **Arquivos tocados**: deletar `domain/services/oraculo_router.py`, `domain/services/semantic_router.py`, `infrastructure/services/router_service.py`, `domain/tools/tool_registry.py` (após copiar os trechos a portar), `application/pipeline/workers.py`.
- **Critério de sucesso**: `grep -r` confirma zero imports externos antes de deletar; suite de testes inalterada (100% verde); nenhuma rota Celery deixa de ser registrada (autodiscovery do `WorkerRegistry` não reclama).
- **Reversível**: sim (git revert simples, arquivos não tinham consumidores).

### Fase 2 — Criar `router/` + `agents/base.py`/`registry.py`, migrar o roteamento (Supervisor primeiro)
- **Objetivo**: extrair `router/` como pacote de topo-nível, com `supervisor.py`, `llm_fallback.py`, `gatekeeper.py`, `contracts.py`, portando os padrões de Pydantic/DI dos routers mortos. Criar também `agents/base.py` (`BaseAgent`, `AgentContext`) e `agents/registry.py` (`AgentRegistry`) vazios/mínimos nesta fase — ainda sem agentes reais registrados, só o mecanismo, para que `router/supervisor.py` já nasça resolvendo por nome via Registry em vez de importar módulos de agente diretamente. `application/routing/semantic_router.py` e `message_router.py` passam a ser **shims de compatibilidade** (thin wrapper re-exportando do novo `router/`) por 1 release, para não quebrar imports não mapeados.
- **Arquivos tocados**: novo `src/router/*`; novo `src/agents/base.py`, `src/agents/registry.py`; `application/routing/semantic_router.py`, `application/routing/message_router.py`, `application/routing/llm_orchestrator.py` viram shims; `application/tasks/process_message_task.py` e `application/chain/cognitive_os.py` atualizam imports para `router.supervisor`.
- **Critério de sucesso**: testes unitários de router passam contra o novo `router/` (adaptar paths de import); smoke E2E das 4 categorias de mensagem da Fase 0 continua roteando para o mesmo agente de destino (comparação de log antes/depois).
- **Reversível**: sim — shims permitem rollback trocando só o import de volta.

### Fase 3 — Decompor `cognitive_os.py` (dispatcher + HITL + estado Redis)
- **Objetivo**: eliminar o God Object central. Criar `application/runtime/dispatcher.py`, `capabilities/persistence/redis_state.py`, e mover HITL/validação CPF-senha para `agents/sigaa/auth_flow.py` (mesmo que `agents/sigaa/` ainda não exista completo, criar o esqueleto mínimo aqui, já implementando `BaseAgent`/`AgentContext` de 0.2).
- **Arquivos tocados**: `application/chain/cognitive_os.py` (esvaziado/deletado ao final da fase), `application/tasks/process_message_task.py` (passa a chamar `router.supervisor` + `application.runtime.dispatcher` diretamente), novo `agents/sigaa/auth_flow.py`, novo `capabilities/persistence/redis_state.py`.
- **Critério de sucesso**: testes de `cognitive_os` migrados para `test_dispatcher.py` cobrindo os mesmos cenários; fluxo HITL de CPF/senha testado end-to-end (mock de Evolution API) confirma que a sessão de autenticação sobrevive ao redesenho do estado Redis; smoke E2E completo.
- **Reversível**: parcialmente — é a fase de maior risco por tocar estado de sessão ativo em produção (Redis). Recomenda-se **deploy em canário** (feature flag ou % de tráfego) antes de rollout completo, já que há usuários com sessões HITL possivelmente "em voo" no Redis com o formato de chave antigo. Prever script de migração/compatibilidade de chaves Redis, ou aceitar que sessões em andamento no momento do deploy expiram e usuário reinicia o fluxo (aceitável dado TTL curto de HITL).

### Fase 4 — Extrair `agents/academic_knowledge/` e `capabilities/rag/`
- **Objetivo**: consolidar RAG/Synthesis/Planner/Memory num agente coeso, registrado no Agent Registry como `AcademicKnowledgeAgent`.
- **Arquivos tocados**: `infrastructure/services/rag_search_service.py`, `infrastructure/services/synthesis_service.py`, `application/chain/planner.py`, `application/workers/worker_memory_manager.py`, `application/workers/worker_synthesis.py` (emagrecido), `application/chain/reranker.py` → `capabilities/rag/reranker.py`.
- **Critério de sucesso**: testes de RAG existentes (unit + e2e) continuam verdes; comparação de qualidade de resposta (mesmas perguntas de avaliação em `tests/eval/`) antes/depois não regride; worker Celery `worker_synthesis` mantém mesmo contrato de entrada/saída (schema de payload) para não quebrar `router/contracts.py`/dispatcher já migrados nas Fases 2-3.
- **Reversível**: sim, camada é isolada — rollback por commit de fase, worker mantém assinatura Celery estável durante toda a fase (troca só a implementação interna).

### Fase 5 — Extrair `agents/sigaa/` e `capabilities/sigaa/`
- **Objetivo**: maior arquivo do projeto (`sigaa_agent.py`, 966 linhas) decomposto em capabilities de scraping puras + agente de decisão/elegibilidade, registrado no Agent Registry como `SigaaAgent`.
- **Arquivos tocados**: `infrastructure/scraping/implementations/sigaa_agent.py` (deletado ao final), `application/workers/worker_sigaa.py` (emagrecido), novo `agents/sigaa/service.py`, `agents/sigaa/eligibility.py`, `capabilities/sigaa/scrape_*.py`.
- **Critério de sucesso**: testes de avaliação SIGAA passam; teste de regressão específico comparando cálculo de elegibilidade antigo (dos dois lugares duplicados) vs novo cálculo único, usando dados de fixture reais de histórico/turmas; smoke test de scraping em ambiente de homologação do SIGAA (não em produção, dado custo/risco de automação Playwright contra site real) antes de promover a mudança.
- **Reversível**: sim, mas é a fase de maior esforço de teste manual (Playwright contra site externo é frágil por natureza) — recomenda-se rodar em paralelo (shadow mode: nova implementação roda e loga resultado sem responder ao usuário) por alguns dias antes do cutover final.

### Fase 6 — Consolidar `capabilities/registry.py`, `capabilities/messaging/`, `capabilities/persistence/`, `agents/conversation/`, `agents/tickets/`
- **Objetivo**: fechar as últimas duplicações (`crud_tools.py` vs `tool_registry.py`, `registration_funnel.py`, `worker_action.py`, `admin_tools.py`), registrando `ConversationAgent` e `TicketAgent` no Agent Registry.
- **Arquivos tocados**: `domain/tools/crud_tools.py`, `domain/tools/admin_tools.py`, `domain/tools/gmail_tool.py`, `application/routing/registration_funnel.py`, `application/workers/worker_action.py`.
- **Critério de sucesso**: testes de cadastro/registro passam; nenhuma service duplicada registrada duas vezes (checar log de startup do `capabilities/registry.py` autodiscovery); smoke test de fluxo completo de cadastro de novo usuário via WhatsApp (staging).
- **Reversível**: sim.

### Fase 7 — Limpeza final e remoção de shims
- **Objetivo**: remover os shims de compatibilidade criados na Fase 2 (`application/routing/*.py` como re-exports), remover `domain/tools/` e `infrastructure/services/{rag_search_service,synthesis_service}.py` originais, atualizar `arquitetura_oraculo.md` para refletir o novo desenho de 3 camadas (router/agents/capabilities) substituindo a descrição de "5 camadas cognitivas" antiga.
- **Critério de sucesso**: `grep -r "application.routing\|application.chain.cognitive_os\|infrastructure.services.rag_search_service\|infrastructure.services.synthesis_service" src/` retorna vazio (fora de testes históricos a serem também limpos); suite completa verde; `arquitetura_oraculo.md` atualizado e revisado.
- **Reversível**: tecnicamente sim via git, mas é a fase "ponto sem volta" lógico — só executar depois de todas as fases anteriores estáveis em produção por período de bake (recomendado mínimo 1-2 semanas de observação sem incidentes por fase antes de avançar para a próxima).

---

## Observações Finais

- Cada fase deve gerar um PR isolado com testes verdes antes de merge — nunca fazer duas fases no mesmo PR, dado que o sistema é um bot WhatsApp ativo com filas Celery em produção.
- Fases 3 e 5 são as de maior risco operacional (estado Redis de sessão ativa; automação Playwright contra site externo real) — merecem canário/shadow mode explícito, diferente das demais que são refatoração estrutural de baixo risco funcional.
- Critério "não quebrou nada" recorrente em todas as fases: (1) `pytest tests/` 100% verde, (2) smoke E2E das 4 categorias de mensagem definidas na Fase 0, (3) paridade de payload/contrato Celery entre worker antigo e novo durante a transição (permite rollback de uma única fase sem afetar as demais).
- **Fora de escopo deste plano** (ver 0.3): plataforma de administração de agentes (config via banco, painel admin, catálogo editável sem rebuild). Direção de evolução válida para depois da Fase 7, não faz parte deste roadmap.
- **Limite desta execução automatizada**: as tarefas de canário/shadow-mode em produção, deploy real e período de "bake" de 1-2 semanas mencionados nas Fases 3, 5 e 7 são passos operacionais que dependem de infraestrutura de produção real e não podem ser executados dentro desta sessão — o que esta sessão entrega é o código de cada fase, commitado isoladamente, com testes locais verdes; o rollout gradual em produção continua sendo responsabilidade humana.
