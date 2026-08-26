# Oráculo — De Monólito Hardcoded a Plataforma Orientada a Configuração

> **Status: 🗄️ investigação arquitetural, decisão aberta.** Produzido em
> 2026-08-26, no mesmo espírito de `pesquisa_arquitetura_producao.md`: nada
> aqui é decisão fechada, é a base pra discutir os próximos passos. A Fase 1
> (Dynamic Configuration) já foi especificada em detalhe nesta mesma
> investigação e está pronta pra implementar assim que aprovada; as fases
> seguintes têm decisões explícitas em aberto na §L.

## Contexto

O Oráculo funciona, mas boa parte das capacidades (providers de LLM/STT/TTS/embeddings, parsers de PDF, tools, canais, Graphs LangGraph, HITL, Gatekeeper) está hardcoded no código: adicionar uma nova instância de qualquer uma delas hoje exige editar múltiplos arquivos e fazer deploy. O pedido original (flags dinâmicas via `.env`→Redis) evoluiu, durante a investigação, para uma pergunta maior: como transformar o Oráculo em uma plataforma onde **código fornece capacidades** e **Hub Admin controla registro/ativação/composição** dessas capacidades — sem reescrever o que já funciona, sem inventar abstração por abstração, e sem virar um sistema de plugins genérico demais para uma equipe pequena.

Este documento é o produto da investigação (Fases 1-5 do workflow de planejamento): 5 auditorias paralelas do código real + pesquisa de 8 referências externas. Nada foi implementado ainda — é a base para a decisão de arquitetura e sequenciamento.

**Documentos relacionados**: `docs/technical-debt.md` (TD-001, TD-003, TD-013 — referenciados abaixo), `docs/historico/pesquisa_arquitetura_producao.md` (mesmo espírito: rascunho de discussão, não decisão fechada), `docs/architecture/system-map.md`.

---

## A. Diagnóstico atual

O Oráculo já tem dois padrões maduros, comprovados em produção, que devem ser **generalizados, não substituídos**:

1. **"Redis primeiro, `settings.py` como fallback, sem restart"** — `llm_factory.py::_provider_global_ativo()` e `pricing.py::taxa_brl_ativa()`. Sem singleton, sem cache, sem pub/sub: cada leitura vai direto ao Redis (~1ms). Já resolve "sem restart" porque nunca existiu o problema de invalidação de cache que pub/sub resolveria.
2. **Postgres como fonte de verdade + Redis como espelho de leitura rápida, com auditoria** — tabelas `agentes_catalogo` (migration 005+007) e `llm_pricing` (migration 008): chave única, colunas editáveis, `atualizado_em`/`atualizado_por`, upsert via `on_conflict_do_update`, escrita em Postgres primeiro e só depois espelhada no Redis.

O resto das "capacidades" citadas no pedido original **não segue nenhum desses padrões** — cada uma tem seu próprio grau (variável) de acoplamento:

| Área | Interface real? | Seleção | Override em runtime | Registro admin-editável |
|---|---|---|---|---|
| **LLM provider** | `Protocol` (`llm_Provider.py`) | if/elif fechado (3 nomes) | Sim — Redis global + por agente | Só nome+modelo, em `agentes_catalogo` |
| **STT/TTS** | `Protocol` | if/elif (1-2 branches) | **Nenhum** | **Nenhum** |
| **Embeddings** | Nenhuma (usa classe do LangChain) | if/elif sobre `os.getenv` cru (nem usa `settings`) | **Nenhum** | **Nenhum** |
| **Parser PDF** | `ABC` real | **dict de registro** + lista de candidatos por extensão + fallback em cadeia + probe de disponibilidade | Não (tudo em código-fonte) | Não |
| **Tools** | Nenhuma | decorator + autodiscovery (`pkgutil`) | — | Página Hub só-leitura, marcada `"sem_consumidor_producao"` |
| **MCP** | Nenhuma | 3 URLs hardcoded + regex de prefixo | Não | Não (flag `FEATURE_MCP_PRODUCT` existe mas é inerte) |
| **Canais (WhatsApp)** | `IMessageGateway` existe mas é **código morto** (nomes de método nem batem com o adapter real) | Instanciação direta em 7+ pontos | Não | Não |
| **Rota→execução (dispatcher)** | — | **5 dicts/frozensets hardcoded** que precisam ficar sincronizados manualmente | Classificação (qual rota) já é data-driven; execução (qual código roda) não | `intents_router` cobre só a classificação |
| **HITL** | — | 2 implementações duplicadas (state machine legada + `interrupt()` do LangGraph) para o mesmo funil | Não | Não (TTLs/timeouts são constantes literais) |
| **Gatekeeper** | — | if/regex puro, deliberadamente estável (comentário do próprio código) | Não | Não — e TD-013 (`docs/technical-debt.md`) já documenta que toda decisão `IGNORE` é reescrita incondicionalmente pra `LLM`, deixando os filtros de segurança inertes na prática |
| **RBAC** | — | dict Python (`domain/permissions.py::_PERMISSOES`) | — | `agentes_catalogo.permissions` existe mas está **sempre vazio** — desconectado do RBAC real |

**Achado mais importante de toda a auditoria**: o registro de tools (`capabilities/registry.py`) já é dinâmico (autodiscovery via decorator) — e é exatamente o que está morto e quebrado (importa uma classe que não existe, zero agentes o consultam). Isso é evidência direta, dentro do próprio repositório, de que "tornar dinâmico" não é automaticamente a escolha certa — o registro de agentes (`bootstrap.py`) documenta explicitamente ter preferido registro **explícito e estático** por só existirem 4 agentes hoje, adiando autodiscovery como "especulativo". Esse princípio guia várias recomendações abaixo.

---

## B. Problemas de acoplamento (arquivos a editar hoje para adicionar uma capacidade nova)

| Adicionar... | Arquivos a tocar hoje |
|---|---|
| 1 LLM provider novo | `settings.py` (novos campos) + novo adapter (se não for OpenAI-compatible) + `llm_factory.py` (`_PROVIDERS_VALIDOS` + branch em `_instanciar`) + `pricing.py` (tabela de preço) |
| 1 STT/TTS provider novo | `settings.py` + novo adapter + `stt_factory.py`/`tts_factory.py` (branch) + `audio_service.py` (branch de custo, hoje só Gemini é custeado) |
| 1 embedding provider novo | `embeddings.py` (branch inline — nem tem adapter separado) |
| 1 parser novo | 1 arquivo (adapter) + 1 linha em `_REGISTRY` — **já é o caminho mais barato hoje**, documentado no próprio docstring do módulo |
| 1 tool nova | 1 arquivo com `@tool(...)` p/ registro — mas pra ficar alcançável, precisa **também** um branch hardcoded no `service.py` do agente (não existe hoje "quais tools o agente X tem") |
| 1 conexão MCP nova | 4 arquivos (`clients.py`, `mcp_lab_use_case.py`, `tools.py`, `router.py`) — tudo hardcoded, nenhuma tabela |
| 1 canal novo (Telegram etc.) | Reescrita real: novo controller+parser, decidir se `IncomingMessage` deixa de ser JID-shaped, novo adapter, **substituir 7+ instanciações diretas de `EvolutionAdapter()`**, `process_message_task.py` tem task nomeada/parametrizada pra WhatsApp, `gatekeeper.py` usa nomenclatura JID/menção `@oraculo` |
| 1 rota/Graph novo | 5-7 arquivos: `router/contracts.py`, `router/llm_fallback.py` (prompt), `router/supervisor.py::_HINTS`, `dispatcher_langgraph.py` (2 dicts), `langgraph_experiment/graph.py` (`build_graph`), `langgraph_experiment/nodes.py`, opcionalmente `dispatcher.py::_ROTA_PARA_AGENTE` |

---

## C. Arquitetura alvo

**Não** um `ConfigService` singleton nem um plugin system genérico. Duas camadas complementares, reaproveitando exatamente o que já funciona:

```
REGISTRY LAYER (código, muda em deploy)          CONFIGURATION LAYER (dados, muda em runtime)
─────────────────────────────────────            ──────────────────────────────────────────
"quais implementações EXISTEM"                    "qual está ATIVA, com qual config"

parser_factory.py JÁ é o modelo:                  config_dinamica (Fase 1, já planejada) É o modelo:
  dict de builders lazy-import                      Postgres fonte de verdade
  + fallback em cadeia                              + espelho Redis (write-through, sem TTL)
  + probe de disponibilidade                        + get_bool/get_int/get_str genéricos
  registro EXPLÍCITO (bootstrap.py já              + upsert com auditoria (atualizado_por)
    preferiu isso a autodiscovery)                  + endpoint admin + página Hub
```

O runtime de cada fábrica (`llm_factory`, `stt_factory`, `tts_factory`, `parser_factory`, `embeddings.py`) passa a perguntar à Configuration Layer **qual** registro usar, e ao Registry Layer **como instanciá-lo** — sem fundir as duas coisas em um objeto mágico.

**Onde termina configuração e começa código**, com exemplos concretos do próprio Oráculo:

- **Configuração**: qual provider/modelo ativo; qual(is) parser(s) habilitado(s) e prioridade; TTLs/timeouts (`HITL_SESSION_TTL=300`, `RAG_CACHE_TTL_SECONDS`); a associação rota→(graph, agente pro circuit-breaker, cacheable, permite-detour) hoje espalhada em 5 dicts; o toggle de modo-beta-1-grupo do Gatekeeper.
- **Código**: como o parser efetivamente extrai texto; como o node do ticket valida categoria/CPF; a lógica do Gatekeeper em si (o próprio arquivo se autodescreve como "bom modelo de estilo, mantido quase inalterado"); a tabela `_PERMISSOES` de RBAC (papéis mudam raramente, ganham com exaustividade de enum em Python); a construção do grafo LangGraph (`build_graph`) — grafo é lógica de execução, não dado.
- **Zona cinzenta resolvida caso a caso**: os 5 dicts hardcoded de rota→execução **parecem** lógica mas são só *dados* (associações nome→nome) sem nenhum `if` de negócio dentro — candidato natural a virar 1 tabela. Já os *validadores* dentro dos nodes (`validar_categoria`, `validar_tipo`) têm regra de negócio real — ficam em código.

---

## D. Componentes

| Componente | O que é | Estado |
|---|---|---|
| **Dynamic Configuration** | Tabela `config_dinamica` + `dynamic_config.py` (get_bool/int/str) + admin API + seção no Hub | **Já totalmente especificado** — plano detalhado já produzido nesta investigação, resumido no Anexo I |
| **Route/Workflow Registry** | 1 tabela nova (ou extensão de `intents_router`) colapsando os 5 dicts de rota→execução em 1 config | Maior valor concreto identificado — não toca em `build_graph()` |
| **Provider Registry (LLM)** | Generalizar `_instanciar`'s if/elif fechado em dict de builders, no molde de `parser_factory.py` | Já tem a plumbing de override (Redis+Postgres) — só falta abrir o cadastro |
| **Parser Registry** | Já existe (`parser_factory.py`) — só falta mover prioridade/enable de hardcoded pra `config_dinamica` | Menor esforço, maior aproveitamento do que já existe |
| **Tool Registry** | `capabilities/registry.py` — hoje morto/quebrado; revivê-lo exige corrigir o bug de import **e** inventar do zero o binding "agente↔tools" que nunca existiu | Decidido revivir (ver §L) |
| **Channel Registry** | Não existe embrião nenhum — `IMessageGateway` é código morto | Decidido adiar (ver §J, Fase 7 / §L) |
| **MCP Connection Manager** | Não existe embrião — `mcp_lab` é laboratório, não produção | Decidido adiar — maior risco de segurança do roadmap todo (ver §G / §L) |
| **Policy Registry (HITL/Gatekeeper)** | Recomendo **não** construir um motor genérico — extrair só os números mágicos (TTLs/thresholds) pra config; deixar a lógica de decisão em código | Gatekeeper já é "estável por design"; HITL só tem 2 fluxos — motor genérico resolveria um problema que ainda não existe |
| **Secret Management** | Fora de escopo (decisão já tomada) | `.env` continua sendo a única fonte, texto puro, como hoje |
| **Admin Configuration API** | Extensão de `admin_api.py` — mesmas convenções (`require_admin_jwt`, `RedisAuditLog`, corpo Pydantic inline) por categoria nova | Segue padrão já estabelecido |
| **Admin Hub** | Extensão de páginas Jinja2 existentes (`/hub/config`, `/hub/agents`) + novas por categoria, convenção "1 página por assunto, fetch JS puro" | Sem SPA, sem tabs — não inventar um widget novo |

---

## E. Limites — o que fica onde

| Fica em... | Exemplos concretos do Oráculo |
|---|---|
| **Código** | Adapters (como falar com Gemini/Evolution), validadores de node, `build_graph()`, lógica do Gatekeeper, tabela `_PERMISSOES` de RBAC |
| **Configuração operacional** (Postgres+Redis, admin-editável) | Provider/modelo ativo, parser habilitado+prioridade, feature flags, TTLs/timeouts, associações rota→execução, toggle beta-1-grupo |
| **Metadata** (identidade, campos que o sistema calcula) | Nome/descrição/versão de um registro; campos como `status`/`last_verified` — nunca editáveis por admin, só pelo sistema (padrão do MCP Registry oficial) |
| **Policy** (regra configurável, não motor genérico) | Whitelist de detour do HITL, thresholds de confiança do Supervisor — dados, não um DSL |
| **Segredo** (só `.env`, fora de escopo) | `DATABASE_URL`, `REDIS_URL`, `ADMIN_JWT_SECRET`, todas as API keys |

---

## F. Fluxos (visão conceitual, sem código)

1. **Adicionar provider LLM**: dev cria adapter implementando `ILLMProvider`, registra 1 entrada no dict de builders (como já se faz hoje pra parser) → aparece no Hub como "disponível, não ativo" → admin ativa e configura modelo/credencial-por-referência via Hub → `_instanciar` resolve pelo registro em vez de if/elif.
2. **Adicionar parser**: já funciona quase assim hoje (só falta o passo de habilitar/priorizar via Hub em vez de editar `_EXT_TO_PARSERS`).
3. **Adicionar tool**: dev registra a tool, admin associa a um agente via Hub; agente passa a consultar o registro em vez de branch hardcoded (Fase 5).
4. **Conectar MCP**: **fora de escopo até haver necessidade concreta** (§J Fase 8) — quando acontecer, fluxo passa obrigatoriamente por validação SSRF antes de qualquer probe.
5. **Adicionar canal**: **fora de escopo até haver necessidade concreta** (§J Fase 7).
6. **Registrar Graph**: dev escreve o Graph normalmente em LangGraph; associa via 1 linha em uma tabela `route_registry` (rota → módulo do graph, agente pro circuit-breaker, cacheable, permite-detour) em vez de editar 5-7 arquivos.
7. **Configurar HITL**: TTLs/timeouts editáveis via Hub (extensão da Fase 1); a lógica de quando entrar em HITL continua em código (não é uma "regra de negócio configurável" hoje, é fluxo).
8. **Configurar Gatekeeper**: só o toggle beta-1-grupo vira config; o resto permanece como está, por design.
9. **Alterar configuração em runtime**: idêntico ao que já foi especificado na Fase 1 — Postgres commit → espelho Redis → próxima leitura já reflete, sem restart.

---

## G. Segurança

- **Todo novo endpoint admin** usa `require_admin_jwt` + `RedisAuditLog` — nenhum mecanismo de auth novo.
- **SSRF é o risco concreto mais alto de todo o roadmap**, especificamente para um futuro MCP Connection Manager: a pesquisa externa encontrou uma CVE real do `stacklok/toolhive` (SSRF em descoberta de auth de servidor MCP remoto, corrigida na v0.31.0) — o padrão de bug é "config admin aponta pra uma URL, o host processa/sonda essa URL sem validar IP privado/loopback/redirect, *antes* de qualquer sandbox". Se/quando o MCP Connection Manager for construído, validação contra RFC1918/loopback/link-local e recusa de seguir redirects para espaço privado é **obrigatória desde o primeiro commit**, não um "depois adicionamos".
- **Segredos continuam fora desta rodada** — mas vale registrar como dívida real: o `/hub/config` atual já permite reescrever `GEMINI_API_KEY`/`ADMIN_JWT_SECRET`/etc. no `.env` em texto puro via browser. Nada nesta iniciativa piora isso, mas nada resolve também.
- **Validação por allowlist + tipo**, nunca aceitar chave arbitrária — mesmo padrão já desenhado na Fase 1 (`ALLOWED_DYNAMIC_KEYS`).
- **Degradação sem exceção**: toda config nova deve cair pro default hardcoded em qualquer falha de leitura (Postgres fora, Redis fora, valor malformado) — nunca derrubar uma resposta ao usuário por causa da camada de config. Mesma filosofia já documentada em `pricing.py`.
- **RBAC em código é uma escolha de segurança, não só simplicidade** — `_PERMISSOES` como dict Python não pode ser adulterado por um painel admin comprometido. Recomendo **não** mover pra banco sem uma razão concreta (ex: múltiplas instituições com papéis diferentes).

---

## H. Persistência

| Camada | O que guarda | Por quê |
|---|---|---|
| `.env` | Segredos + `DATABASE_URL`/`REDIS_URL`/`ADMIN_JWT_SECRET` | Bootstrap — precisa existir antes de qualquer banco/Redis estar disponível |
| Postgres | Fonte de verdade de todo registro/config admin-editável novo (mesma forma de `agentes_catalogo`/`llm_pricing`/`config_dinamica`) | Durável, sobrevive a um `FLUSHALL` do Redis, auditável (`atualizado_por`) |
| Redis | Espelho de leitura rápida, write-through, sem TTL — nunca fonte de verdade | Já comprovado suficiente pra "sem restart", sem cache/pub-sub |
| Secret Manager | **Não introduzido nesta rodada** | Decisão já tomada — mas ver dívida registrada em §G |

---

## I. Padrões de Engenharia

**Já em uso, generalizar**: Registry (`parser_factory.py`), Factory (`llm_factory`/`stt_factory`/`tts_factory`), Ports & Adapters (`ILLMProvider`/`IDocumentParser`/etc.), Strategy (implícito na seleção de provider).

**Introduzir com moderação**: Configuration-driven architecture (o núcleo desta iniciativa), Policy Pattern só para a tabela rota→execução (não um motor genérico de HITL/Gatekeeper).

**Deliberadamente NÃO introduzir**: Event-driven/pub-sub (Celery+Redis já resolve assincronia), Plugin architecture com carregamento dinâmico genérico (o próprio `capabilities/registry.py` autodiscovered e morto é a prova viva de que isso não é grátis), Dependency Injection generalizada (só vale a pena introduzir de fato quando o Channel Registry for construído — antes disso, DI ampla seria abstração por abstração), microserviços (não solicitado, não justificado pelo tamanho da equipe).

---

## J. Migração — sequência recomendada

Ordem por risco crescente e reaproveitamento decrescente do que já existe:

| Fase | O quê | Risco | Por quê nessa posição |
|---|---|---|---|
| **1** | Dynamic Configuration (`config_dinamica`, já 100% especificada) | Baixo | Já pronta para implementar, generaliza padrão comprovado |
| **2** | Route/Workflow Registry — colapsar os 5 dicts hardcoded numa tabela | Médio (toca dispatcher, precisa teste cuidadoso) | Maior valor concreto identificado, não mexe em `build_graph()` |
| **3** | Provider Registry (LLM) — abrir `_instanciar` em dict de builders | Baixo-médio | LLM já tem toda a plumbing de override; só falta abrir o cadastro |
| **4** | Parser: mover prioridade/enable de hardcoded pra config | Baixo | Reaproveita `parser_factory.py` quase integralmente |
| **5** | Tool Registry — revivo + binding real agente↔tools | Médio-alto (mudança de comportamento por agente) | Decidido revivir (§L); também toca TD-003 (migração `services/`→`capabilities/` incompleta) |
| **6** | STT/TTS/Embeddings — mesmo tratamento do LLM | Baixo urgência | Só 1-2 providers cada hoje, sem demanda de negócio concreta ainda |
| **7** | Channel abstraction | Alto esforço | **Adiado** — WhatsApp é único canal hoje, abstrair agora seria "abstração por abstração" |
| **8** | MCP Connection Manager | Alto risco (SSRF) | **Adiado** — `FEATURE_MCP_PRODUCT` inerte, sem consumidor de produção hoje |

HITL/Gatekeeper/RBAC **não são uma fase própria** — os ganhos fáceis (extrair TTLs/timeouts, o toggle beta-1-grupo) entram como itens pequenos dentro das Fases 1-2; não recomendo motor de política genérico para nenhum dos três.

---

## K. Impacto no código atual (por fase)

- **Fase 1**: `migrations/versions/009_config_dinamica.py` (novo), `src/infrastructure/dynamic_config.py` (novo), `src/infrastructure/repositories/dynamic_config_repository.py` (novo), `models.py`, `admin_api.py`, `llm_factory.py`, `semantic_cache.py`, `capabilities/rag/reranker.py`, `templates/hub/config.html`.
- **Fase 2**: `router/contracts.py`, `router/supervisor.py`, `dispatcher_langgraph.py`, `dispatcher.py`, nova tabela (ou extensão de `intents_router`), novo endpoint admin, nova seção Hub.
- **Fase 3**: `llm_factory.py` (reestruturação de `_instanciar`), `settings.py` (sem novos campos obrigatórios), `pricing.py` (tabela de preço por registro).
- **Fase 4**: `parser_factory.py`, nova(s) linha(s) em `config_dinamica`.
- **Fase 5**: `capabilities/registry.py` (corrigir bug), `capabilities/tools/*.py`, `agentes_catalogo` (nova coluna ou tabela de junção), `service.py` de cada agente.
- **Fases 6-8**: não detalhadas neste documento — Fases 7-8 adiadas indefinidamente (§L).

---

## L. Decisões

Já decididas nesta rodada:

- **Tools registry → revivido na Fase 5.** Corrigir o import quebrado (`PostgresUserRepository`), construir o binding real agente↔tools que nunca existiu, migrar agentes pra consultar o registro.
- **Channel abstraction (Fase 7) → adiada indefinidamente.** Não há segundo canal concreto no horizonte; construir a abstração agora seria especulativo. Revisitar só quando houver necessidade real.
- **MCP Connection Manager (Fase 8) → adiado indefinidamente.** `mcp_lab` continua como laboratório. Não assumir o risco de SSRF sem necessidade de produto real.
- **`dispatcher.py` legado → avaliar aposentadoria dentro da Fase 2.** Sem razão forte pra manter os dois caminhos duplicados; a Fase 2 passa a incluir investigar se dá pra consolidar tudo em `dispatcher_langgraph.py` antes de colapsar os 5 dicts de rota→execução. Já é TD-001 em `docs/technical-debt.md` (prioridade "Alta") — esta iniciativa não descobre o problema, dá o gatilho pra resolvê-lo.

Ainda em aberto — decidir antes de implementar a fase correspondente:

1. **Nomenclatura das chaves de config dinâmica**: `GEMINI_MODEL` (flat, igual ao `Settings`, recomendado) vs. `rag.cache_ttl_seconds` (dotted, proposta original). Puramente uma troca de string, mas precisa ser decidida antes da migration da Fase 1.
2. **`agentes_catalogo.permissions`**: hoje sempre vazio, desconectado do RBAC real (`_PERMISSOES`). Com a Fase 5 confirmada (tools registry revivido), faz sentido reaproveitar esta coluna pro binding agente↔tools, ou ela deve virar uma tabela de junção separada e a coluna `permissions` ser removida como vestígio do RBAC não-conectado?
3. **Skill de projeto**: nenhuma skill existente cobre "arquitetura orientada a registro" para este projeto. Vale criar uma skill `oraculo-extensible-architecture` documentando os padrões deste documento, pra sessões futuras não precisarem re-derivar tudo isso?

---

## Anexo I — Fase 1 (Dynamic Configuration), plano já detalhado nesta investigação

Tabela `config_dinamica` (migration 009) com 7 chaves iniciais seedadas (`DEV_TEST_NO_DB_WRITE`, `DEV_TEST_SKIP_REGISTRATION`, `FEATURE_LANGGRAPH_NATIVE_ROUTES`, `FEATURE_LANGGRAPH_CELERY_DISPATCH`, `GEMINI_MODEL`, `RAG_CACHE_TTL_SECONDS`, `RAG_RERANKER_ENABLED`); repositório com upsert; `dynamic_config.py` com `get_bool/get_int/get_str` (Redis→Postgres→default, sem cache, sem singleton); endpoints `GET/POST /api/admin/config`; extensão de `/hub/config`. Detalhamento completo (assinaturas de função, SQL de seed, testes) disponível — a implementar como Fase 1 assim que este documento for aprovado.

---

## Verificação (quando a implementação começar)

- Fase 1: `pytest tests/unit/infrastructure/test_dynamic_config*.py`, testar toggle via `/hub/config` num ambiente local, confirmar que Celery worker lê o valor novo sem restart.
- Fase 2: testar que uma nova rota associada via tabela chega ao Graph certo sem editar `dispatcher_langgraph.py`; rodar suíte de testes de `router/`/`dispatcher` existente antes/depois pra garantir zero regressão nas 11 rotas atuais.
- Cada fase subsequente: mesma disciplina — testes unitários da fábrica/registro tocado + teste manual de ponta a ponta de pelo menos 1 fluxo real antes de considerar a fase concluída.
