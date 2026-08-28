# Oráculo — De Monólito Hardcoded a Plataforma Multi-Tenant Orientada a Configuração

> **Status: 🗄️ investigação arquitetural, decisão aberta — v2.** Produzido em
> 2026-08-25/26 no mesmo espírito de `pesquisa_arquitetura_producao.md`: nada
> aqui é decisão fechada, é a base pra discutir os próximos passos. Esta
> revisão (v2, 2026-08-26) parte da v1 (5 auditorias paralelas do código real
> + 8 referências externas) e adiciona uma segunda rodada de pesquisa
> (multi-tenancy, concorrência, resiliência de provider, segredos
> enterprise-grade, feature flags, GitOps, auditoria à prova de compliance)
> respondendo a uma pergunta explícita: **e se o Oráculo deixasse de ser só
> o bot da UEMA e virasse um produto que outras instituições/empresas
> pagariam para operar?** Isso muda o que "arquitetura orientada a
> configuração" precisa suportar — não porque a UEMA vá virar multi-tenant
> amanhã, mas porque **decisões de schema tomadas errado agora custam uma
> migração dolorosa depois**, enquanto acomodá-las agora custa pouco. A Fase
> 1 (Dynamic Configuration) continua especificada em detalhe e pronta pra
> implementar; o que muda nesta revisão é: (a) hardening de concorrência que
> faltava na v1 (§N), (b) o desenho já nasce com uma coluna e um índice que
> tornam a virada pra multi-tenant um `ALTER TABLE`, não uma reescrita
> (§M), e (c) dívidas que a v1 tratava como "fora de escopo permanente"
> (segredos, §P) agora têm um caminho explícito, mesmo que não implementado
> ainda.

## Contexto

O Oráculo funciona, mas boa parte das capacidades (providers de LLM/STT/TTS/embeddings,
parsers de PDF, tools, canais, Graphs LangGraph, HITL, Gatekeeper) está
hardcoded no código: adicionar uma nova instância de qualquer uma delas hoje
exige editar múltiplos arquivos e fazer deploy. O pedido original (flags
dinâmicas via `.env`→Redis) evoluiu, na v1 desta investigação, para uma
pergunta maior: como transformar o Oráculo em uma plataforma onde **código
fornece capacidades** e **Hub Admin controla registro/ativação/composição**
dessas capacidades — sem reescrever o que já funciona, sem inventar
abstração por abstração, e sem virar um sistema de plugins genérico demais
para uma equipe pequena.

Esta v2 mantém esse princípio (nada de over-engineering especulativo) mas o
aplica a um horizonte maior: **hoje é 1 instituição, 1 grupo WhatsApp, 1
banco** — mas nada no desenho de v1 impedia, também nada o preparava, para
um cenário de múltiplos clientes isolados na mesma infraestrutura. A
diferença prática entre "documento de engenharia interna" e "arquitetura de
produto" não é reescrever tudo — é responder com disciplina a 4 perguntas
que hoje ninguém tinha perguntado: quem é o dono de cada linha de config
(§M), o que acontece quando dois admins editam a mesma chave ao mesmo tempo
(§N), o que acontece quando um provider externo degrada em produção às 3h da
manhã sem ninguém olhando (§O), e como uma empresa grande audita e confia
no sistema antes de assinar contrato (§P, §R).

**Documentos relacionados**: `docs/technical-debt.md` (TD-001, TD-003, TD-013),
`docs/historico/pesquisa_arquitetura_producao.md` (mesmo espírito: rascunho
de discussão, não decisão fechada), `docs/architecture/system-map.md`,
`docs/business/regras_negocio_oraculo.md` (regra 17: hub e WhatsApp já têm
dois mecanismos de "ser admin" separados — relevante pra §M e §R).

---

## A. Diagnóstico atual

O Oráculo já tem dois padrões maduros, comprovados em produção, que devem
ser **generalizados, não substituídos**:

1. **"Redis primeiro, `settings.py` como fallback, sem restart"** —
   `llm_factory.py::_provider_global_ativo()` e `pricing.py::taxa_brl_ativa()`.
   Sem singleton, sem cache, sem pub/sub: cada leitura vai direto ao Redis
   (~1ms). Já resolve "sem restart" porque nunca existiu o problema de
   invalidação de cache que pub/sub resolveria.
2. **Postgres como fonte de verdade + Redis como espelho de leitura rápida,
   com auditoria** — tabelas `agentes_catalogo` (migration 005+007) e
   `llm_pricing` (migration 008): chave única, colunas editáveis,
   `atualizado_em`/`atualizado_por`, upsert via `on_conflict_do_update`,
   escrita em Postgres primeiro e só depois espelhada no Redis.

O resto das "capacidades" citadas no pedido original **não segue nenhum
desses padrões** — cada uma tem seu próprio grau (variável) de acoplamento:

| Área | Interface real? | Seleção | Override em runtime | Registro admin-editável |
|---|---|---|---|---|
| **LLM provider** | `Protocol` (`llm_Provider.py`) | if/elif fechado (3 nomes) | Sim — Redis global + por agente | Só nome+modelo, em `agentes_catalogo` |
| **STT/TTS** | `Protocol` | if/elif (1-2 branches) | **Nenhum** | **Nenhum** |
| **Embeddings** | Nenhuma (usa classe do LangChain) | if/elif sobre `os.getenv` cru (nem usa `settings`) | **Nenhum** | **Nenhum** |
| **Parser PDF** | `ABC` real | **dict de registro** + lista de candidatos por extensão + fallback em cadeia + probe de disponibilidade | Não (tudo em código-fonte) | Não |
| **Tools** | Nenhuma | decorator + autodiscovery (`pkgutil`) | — | Página Hub só-leitura, marcada `"sem_consumidor_producao"` |
| **MCP** | Nenhuma | 3 URLs hardcoded + regex de prefixo | Não | Não (flag `FEATURE_MCP_PRODUCT` existe mas é inerte) |
| **Canais (WhatsApp)** | `IMessageGateway` existe mas é **código morto** | Instanciação direta em 7+ pontos | Não | Não |
| **Rota→execução (dispatcher)** | — | **5 dicts/frozensets hardcoded** que precisam ficar sincronizados manualmente | Classificação já é data-driven; execução não | `intents_router` cobre só a classificação |
| **HITL** | — | 2 implementações duplicadas para o mesmo funil | Não | Não (TTLs/timeouts são constantes literais) |
| **Gatekeeper** | — | if/regex puro, deliberadamente estável | Não | Não — TD-013 documenta que toda decisão `IGNORE` é reescrita incondicionalmente pra `LLM` |
| **RBAC** | — | dict Python (`domain/permissions.py::_PERMISSOES`) | — | `agentes_catalogo.permissions` existe mas está **sempre vazio** |

**Achado mais importante da v1, ainda válido**: o registro de tools
(`capabilities/registry.py`) já é dinâmico (autodiscovery via decorator) — e
é exatamente o que está morto e quebrado. Isso é evidência direta de que
"tornar dinâmico" não é automaticamente a escolha certa — `bootstrap.py`
preferiu registro **explícito e estático** por só existirem 4 agentes hoje.
Esse princípio continua guiando as recomendações abaixo, inclusive as novas.

---

## B. Problemas de acoplamento (arquivos a editar hoje para adicionar uma capacidade nova)

| Adicionar... | Arquivos a tocar hoje |
|---|---|
| 1 LLM provider novo | `settings.py` + novo adapter + `llm_factory.py` (`_PROVIDERS_VALIDOS` + branch em `_instanciar`) + `pricing.py` |
| 1 STT/TTS provider novo | `settings.py` + novo adapter + `stt_factory.py`/`tts_factory.py` + `audio_service.py` (branch de custo) |
| 1 embedding provider novo | `embeddings.py` (branch inline) |
| 1 parser novo | 1 arquivo + 1 linha em `_REGISTRY` — já é o caminho mais barato hoje |
| 1 tool nova | 1 arquivo `@tool(...)` + branch hardcoded no `service.py` do agente |
| 1 conexão MCP nova | 4 arquivos hardcoded, nenhuma tabela |
| 1 canal novo (Telegram etc.) | Reescrita real: 7+ instanciações diretas de `EvolutionAdapter()`, `IncomingMessage` JID-shaped, `gatekeeper.py` acoplado a JID/`@oraculo` |
| 1 rota/Graph novo | 5-7 arquivos: `contracts.py`, `llm_fallback.py`, `supervisor.py::_HINTS`, `dispatcher_langgraph.py` (2 dicts), `graph.py`, `nodes.py` |

---

## C. Arquitetura alvo

**Não** um `ConfigService` singleton nem um plugin system genérico. Três
camadas complementares (a v1 tinha duas; esta revisão separa explicitamente
a terceira, que a v1 misturava dentro da Configuration Layer sem nomear):

```
REGISTRY LAYER (código, muda em deploy)     CONFIGURATION LAYER (dados, muda em runtime)      TENANCY LAYER (§M, novo nesta revisão)
────────────────────────────────────        ──────────────────────────────────────────       ──────────────────────────────────────
"quais implementações EXISTEM"               "qual está ATIVA, com qual config,               "de QUEM é esta linha de config"
                                               e quem mudou o quê e quando (§N)"

parser_factory.py JÁ é o modelo:             config_dinamica (Fase 1) É o modelo, com          tenant_id opcional em toda tabela nova
  dict de builders lazy-import                 hardening novo desta revisão:                    (nullable hoje = "global/UEMA"),
  + fallback em cadeia                          + version column (optimistic lock, §N)          nunca usado pra isolar UEMA de si
  + probe de disponibilidade                    + read-repair no miss/drift Redis↔PG (§N)        mesma — só existe pra não custar
  registro EXPLÍCITO                            + histórico old_value/new_value p/ rollback      uma reescrita se/quando houver
                                                 + get_bool/get_int/get_str genéricos              um segundo cliente
                                                 + endpoint admin + página Hub
```

O runtime de cada fábrica (`llm_factory`, `stt_factory`, `tts_factory`,
`parser_factory`, `embeddings.py`) continua perguntando à Configuration
Layer **qual** registro usar, e ao Registry Layer **como** instanciá-lo. A
Tenancy Layer não é uma camada de execução nova — é uma **coluna e uma
convenção de query** que evita que "adicionar um segundo cliente" vire
"reescrever todas as tabelas de config" (ver §M para o argumento completo de
por que isso vale a pena decidir agora e custa quase nada).

**Onde termina configuração e começa código**, sem mudança da v1:

- **Configuração**: provider/modelo ativo; parser(s) habilitado(s) e
  prioridade; TTLs/timeouts; associação rota→(graph, agente, cacheable,
  permite-detour); toggle beta-1-grupo do Gatekeeper.
- **Código**: adapters, validadores de node, lógica do Gatekeeper,
  `_PERMISSOES` de RBAC, `build_graph()`.
- **Zona cinzenta**: os 5 dicts de rota→execução são *dados* — viram tabela.
  Validadores de negócio dentro dos nodes ficam em código.

---

## D. Componentes

| Componente | O que é | Estado |
|---|---|---|
| **Dynamic Configuration** | `config_dinamica` + `dynamic_config.py` + admin API + Hub, agora com version column e histórico (§N) | Especificado — Anexo I, endurecido nesta revisão |
| **Route/Workflow Registry** | 1 tabela colapsando os 5 dicts de rota→execução | Maior valor concreto identificado |
| **Provider Registry (LLM)** | Dict de builders no molde de `parser_factory.py`, com **health-check + circuit breaker** (§O, novo) | Plumbing de override já existe |
| **Parser Registry** | Já existe — só falta mover prioridade/enable pra config | Menor esforço |
| **Tool Registry** | Revivido na Fase 5, com **manifesto de capability** (§S, novo) em vez de dict cru | Decidido revivir (§L) |
| **Channel Registry** | Não existe embrião | Adiado (§L) |
| **MCP Connection Manager** | Não existe embrião | Adiado — maior risco SSRF (§G) |
| **Policy Registry (HITL/Gatekeeper)** | Extrair só números mágicos; sem motor genérico | Confirmado nesta revisão (ver §Q sobre OPA) |
| **Secret Management** | Fora de escopo *imediato*, mas com roadmap explícito agora (§P) | Mudou de "fora de escopo" pra "fase futura condicionada" |
| **Admin Configuration API** | Extensão de `admin_api.py` | Segue padrão já estabelecido |
| **Admin Hub** | Extensão de páginas Jinja2 | Sem SPA, sem tabs |
| **Tenancy metadata** (novo) | `tenant_id` nullable em tabelas de config novas | Não é feature visível — é seguro de arquitetura (§M) |
| **Config Audit Trail** (elevado) | `RedisAuditLog` + Postgres, agora com registro append-only e diff old/new (§R) | Generaliza o que já existe em `agentes_catalogo`/`llm_pricing` |

---

## E. Limites — o que fica onde

| Fica em... | Exemplos concretos do Oráculo |
|---|---|
| **Código** | Adapters, validadores de node, `build_graph()`, lógica do Gatekeeper, `_PERMISSOES` |
| **Configuração operacional** (Postgres+Redis, admin-editável) | Provider/modelo ativo, parser habilitado+prioridade, feature flags, TTLs, rota→execução, toggle beta-1-grupo |
| **Metadata** (identidade, campos que o sistema calcula) | Nome/descrição/versão de um registro; `status`/`last_verified` — só o sistema escreve |
| **Policy** (regra configurável, não motor genérico) | Whitelist de detour do HITL, thresholds de confiança do Supervisor |
| **Tenancy** (novo — de quem é a linha) | `tenant_id` nullable; hoje sempre "UEMA", sem custo de produto extra |
| **Segredo** (`.env` hoje; roadmap em §P) | `DATABASE_URL`, `REDIS_URL`, `ADMIN_JWT_SECRET`, API keys |

---

## F. Fluxos (visão conceitual, sem código)

1. **Adicionar provider LLM**: dev cria adapter, registra no dict de
   builders com um manifesto mínimo (§S) → aparece no Hub como "disponível,
   não ativo, saúde desconhecida" → admin ativa → `_instanciar` resolve pelo
   registro, com circuit breaker por trás (§O).
2. **Adicionar parser**: já quase assim hoje — só falta habilitar/priorizar
   via Hub.
3. **Adicionar tool**: dev registra, admin associa a um agente via tabela de
   junção dedicada (não a coluna `permissions`, ver decisão §L.2).
4. **Conectar MCP**: fora de escopo até necessidade concreta — validação
   SSRF obrigatória desde o primeiro commit se/quando acontecer.
5. **Adicionar canal**: fora de escopo até necessidade concreta.
6. **Registrar Graph**: 1 linha em `route_registry` em vez de 5-7 arquivos.
7. **Configurar HITL**: TTLs editáveis via Hub; lógica de entrada continua
   em código.
8. **Configurar Gatekeeper**: só o toggle beta-1-grupo vira config.
9. **Alterar configuração em runtime**: Postgres commit (com checagem de
   versão, §N) → espelho Redis → próxima leitura reflete, sem restart. Se o
   espelho falhar, a próxima leitura detecta a divergência e faz
   read-repair (§N) — não fica silenciosamente desatualizado.
10. **Reverter uma configuração ruim** (novo, §N): admin vê histórico de
    valores no Hub, escolhe uma versão anterior, confirma — sem precisar
    saber qual era o valor antigo de cabeça nem editar `.env` e reiniciar.

---

## G. Segurança

- **Todo novo endpoint admin** usa `require_admin_jwt` + `RedisAuditLog` —
  nenhum mecanismo de auth novo.
- **SSRF continua o risco concreto mais alto do roadmap**, especificamente
  para um futuro MCP Connection Manager: CVE real do `stacklok/toolhive`
  (SSRF em descoberta de auth de servidor MCP remoto, corrigida na v0.31.0).
  Validação contra RFC1918/loopback/link-local e recusa de redirects para
  espaço privado é obrigatória desde o primeiro commit, não depois.
- **Segredos**: `/hub/config` hoje permite reescrever `GEMINI_API_KEY`/
  `ADMIN_JWT_SECRET` em texto puro via browser. A v1 registrava isso como
  dívida sem resolver; esta revisão adiciona um caminho concreto (§P) —
  ainda não implementado, mas não é mais "aceito indefinidamente" se o
  produto for vendido a terceiros.
- **Validação por allowlist + tipo**, nunca chave arbitrária —
  `ALLOWED_DYNAMIC_KEYS`.
- **Degradação sem exceção**: toda config nova cai pro default hardcoded em
  qualquer falha de leitura. Mesma filosofia de `pricing.py`, agora
  explicitamente testada por um caso de teste obrigatório por config nova
  (ver §T, critério de "definition of done").
- **RBAC em código é escolha de segurança, não só simplicidade** —
  `_PERMISSOES` como dict Python não pode ser adulterado por um painel
  admin comprometido. Não mover pra banco sem razão concreta — e se um dia
  houver (múltiplos clientes com papéis diferentes), o modelo certo é
  **camada aditiva por tenant sobre a base em código** (§M), nunca
  substituição da base.
- **Concorrência como superfície de segurança** (novo, §N): duas escritas
  simultâneas na mesma chave sem controle de versão podem silenciosamente
  desfazer uma mudança de segurança (ex.: admin A desliga um provider
  comprometido, admin B salva uma edição não relacionada e sem querer
  reativa o provider por causa de last-write-wins). Isso não é só bug de
  UX, é uma janela de segurança.

---

## H. Persistência

| Camada | O que guarda | Por quê |
|---|---|---|
| `.env` | Segredos + `DATABASE_URL`/`REDIS_URL`/`ADMIN_JWT_SECRET` | Bootstrap |
| Postgres | Fonte de verdade de todo registro/config admin-editável (com version column, §N) | Durável, auditável |
| Redis | Espelho de leitura rápida, write-through, sem TTL, com read-repair (§N) | "Sem restart" comprovado |
| Secret Manager | Não implementado ainda — roadmap em §P | Necessário só se/quando houver 2º cliente ou exigência de compliance |

---

## I. Padrões de Engenharia

**Já em uso, generalizar**: Registry, Factory, Ports & Adapters, Strategy.

**Introduzir com moderação**: Configuration-driven architecture, Policy
Pattern só pra rota→execução, **Circuit Breaker** por provider (§O, novo —
validado como padrão maduro e de baixo custo de implementação, não é
especulativo), **Optimistic Concurrency Control** via version column (§N,
novo — padrão de banco de dados, não infraestrutura nova).

**Deliberadamente NÃO introduzir**: Event-driven/pub-sub, Plugin
architecture com carregamento dinâmico genérico, Dependency Injection
generalizada, microserviços, **Open Policy Agent / policy-as-code
genérico** (avaliado nesta revisão, §Q — decisão de não adotar, com
critério explícito de quando reavaliar), **feature-flag SaaS de terceiros**
(LaunchDarkly/Unleash — o padrão conceitual deles informa o design de
`config_dinamica`, mas rodar a infraestrutura deles seria trocar uma
dependência de baixo custo operacional por uma de alto custo, sem necessidade
de propagação sub-milissegundo em milhares de instâncias que o Oráculo não
tem).

---

## J. Migração — sequência recomendada

> **Estado (2026-08-28): Fases 1–5 concluídas, testadas e rodando.** Fases 6–8
> deliberadamente não iniciadas (sem demanda); 9–11 condicionais a evento de
> negócio. A metade "Workflow" da Fase 2 (topologia do grafo como dado) virou
> um adendo à parte: `arquitetura_nos_declarativa.md`. Status consolidado em
> `estado_e_roteiro_planos.md`.

Ordem por risco crescente e reaproveitamento decrescente do que já existe.
As Fases 1-8 são as mesmas da v1 (não regridem); esta revisão adiciona
Fases 9-11 como **capacidades que só fazem sentido se/quando o Oráculo virar
produto multi-cliente** — deliberadamente depois das Fases 1-8, e
deliberadamente não implementadas agora.

| Fase | O quê | Estado | Por quê nessa posição |
|---|---|---|---|
| **1** | Dynamic Configuration, endurecida com version column + read-repair (§N) | ✅ concluída | Já pronta, generaliza padrão comprovado, mais robusta que a v1 |
| **2** | Route/Workflow Registry | ✅ metade "Route" concluída; "Workflow" → adendo | Maior valor concreto identificado |
| **3** | Provider Registry (LLM) + health-check/circuit breaker (§O) | ✅ concluída | Plumbing de override já existe; resiliência é o item novo |
| **4** | Parser: mover prioridade/enable pra config | ✅ concluída | Reaproveita quase tudo |
| **5** | Tool Registry — revivido + manifesto de capability (§S) | ✅ concluída | Decidido revivir (§L); também TD-003 |
| **6** | STT/TTS/Embeddings — mesmo tratamento do LLM | ⏸ não iniciada | Sem demanda concreta ainda |
| **7** | Channel abstraction | Alto esforço | Adiado |
| **8** | MCP Connection Manager | Alto risco (SSRF) | Adiado |
| **9** *(novo)* | Tenancy real — `tenant_id` deixa de ser sempre nulo, isolamento testado (§M) | Alto (dados) | **Só se/quando houver um 2º cliente real** — condicional, não agendado |
| **10** *(novo)* | Secrets Manager / BYOK (§P) | Médio (infra) | **Só se/quando** exigência de compliance concreta ou 1º cliente enterprise aparecer |
| **11** *(novo)* | Config-as-Code / export-import para GitOps (§Q-bis) | Baixo-médio | **Só se/quando** um cliente pedir change management via PR em vez de UI |

HITL/Gatekeeper/RBAC continuam sem fase própria — mesma decisão da v1.

---

## K. Impacto no código atual (por fase)

- **Fase 1**: `migrations/versions/009_config_dinamica.py` (novo, agora com
  coluna `versao` e tabela `config_dinamica_historico`), `dynamic_config.py`,
  `dynamic_config_repository.py`, `models.py`, `admin_api.py`,
  `llm_factory.py`, `semantic_cache.py`, `capabilities/rag/reranker.py`,
  `templates/hub/config.html`.
- **Fase 2**: `router/contracts.py`, `router/supervisor.py`,
  `dispatcher_langgraph.py`, `dispatcher.py`, nova tabela, novo endpoint
  admin, nova seção Hub.
- **Fase 3**: `llm_factory.py` (reestruturação de `_instanciar` + wrapper de
  circuit breaker), `settings.py`, `pricing.py`.
- **Fase 4**: `parser_factory.py`, novas linhas em `config_dinamica`.
- **Fase 5**: `capabilities/registry.py` (corrigir bug + manifesto),
  `capabilities/tools/*.py`, nova tabela de junção agente↔tool (não a
  coluna `permissions`, ver §L.2), `service.py` de cada agente.
- **Fases 6-8**: não detalhadas — adiadas indefinidamente (§L).
- **Fases 9-11 (novas)**: não detalhadas em nível de arquivo — são
  condicionais a um evento de negócio que ainda não aconteceu; detalhar
  agora seria especular sobre requisitos de um cliente que não existe.

---

## L. Decisões

Já decididas (herdadas da v1, confirmadas nesta revisão):

- **Tools registry → revivido na Fase 5**, com manifesto de capability
  (§S) em vez de dict cru — mudança de forma, não de decisão.
- **Channel abstraction (Fase 7) → adiada indefinidamente.**
- **MCP Connection Manager (Fase 8) → adiado indefinidamente.**
- **`dispatcher.py` legado → avaliar aposentadoria dentro da Fase 2**
  (TD-001).

Novas decisões desta revisão:

- **Multi-tenancy → preparar o schema agora (coluna nullable), não
  implementar isolamento agora.** Custo de adicionar `tenant_id NULL
  DEFAULT NULL` numa tabela nova é ~zero; custo de adicionar depois que já
  existem linhas e código consultando sem esse filtro é uma migração de
  dados arriscada. Decisão: toda tabela nova das Fases 1-5 nasce com a
  coluna; nenhuma linha de código de isolamento é escrita ainda (§M).
- **Segredos → sair de "fora de escopo permanente" para "fase 10
  condicional".** A v1 tratava isso como decisão fechada. Esta revisão
  reabre porque um `.env` editável via browser em texto puro é, na prática,
  um bloqueador de venda para qualquer cliente com requisito de segurança
  formal — mas não vale implementar Vault/KMS sem um cliente real que
  precise disso (§P).
- **OPA / policy-as-code genérico → decisão de NÃO adotar, com critério
  explícito de reavaliação.** Pesquisa externa confirma que motores de
  política valem a pena quando há múltiplos runtimes exigindo política
  consistente ou requisito de auditoria de decisão formal — nenhum dos dois
  é o caso do Oráculo hoje (§Q).
- **Circuit breaker para Provider Registry → adotar na Fase 3.** Baixo
  custo de implementação (wrapper sobre chamada existente), resolve um
  risco real e específico (provider externo degradando sem ninguém notar
  às 3h da manhã) que a v1 não endereçava (§O).
- **Version column + read-repair no `config_dinamica` → adotar na Fase 1.**
  A v1 especificava upsert sem controle de concorrência nem reconciliação
  de drift Redis↔Postgres — gap de correção real, não estético (§N).

Ainda em aberto — decidir antes de implementar a fase correspondente:

1. **Nomenclatura das chaves de config dinâmica**: `GEMINI_MODEL` (flat,
   recomendado) vs. `rag.cache_ttl_seconds` (dotted).
2. **`agentes_catalogo.permissions`**: nesta revisão, recomendação mais
   forte que a v1 — **usar uma tabela de junção nova** (`agente_tools`),
   não a coluna `permissions`, e remover a coluna como vestígio do RBAC
   não-conectado. Motivo: reaproveitar uma coluna JSON solta pra dois
   propósitos diferentes (permissão vs. binding de tool) é exatamente o
   tipo de acoplamento acidental que este documento inteiro está tentando
   eliminar em outras áreas.
3. **Skill de projeto**: vale criar `oraculo-extensible-architecture`
   documentando os padrões deste documento?
4. **Quando "virar produto" deixa de ser hipotético**: não é decisão de
   arquitetura, é decisão de negócio — mas a arquitetura das Fases 1-5 já
   assume que a resposta pode ser "sim" um dia (§M). Vale o time de negócio
   confirmar se essa é de fato a direção antes de gastar esforço nas Fases
   9-11 (que continuam condicionais, não agendadas).

---

## M. Do projeto ao produto — o que multi-tenancy realmente exige

Este é o núcleo da diferença desta revisão em relação à v1. A pergunta não é
"como fazemos o Oráculo multi-tenant" (não é hoje, pode nunca ser) — é
**"o que fazemos hoje, nas Fases 1-5, que barateia essa opção se ela virar
real, sem gastar esforço além disso"**.

**Os 4 modelos de isolamento e por que nenhum se aplica ainda:**

1. *Shared schema + `tenant_id`* — 1 banco, todas as linhas marcadas por
   tenant, isolamento garantido por `WHERE tenant_id = :t` em toda query
   (idealmente reforçado por Row-Level Security do Postgres, não só
   disciplina de código). É o padrão recomendado pra produtos B2B iniciais
   (até ~10 mil tenants, sem exigência de compliance que force isolamento
   físico).
2. *Schema-per-tenant* — raramente compensa o custo operacional.
3. *Database-per-tenant* — reservado pra clientes regulados/white-label que
   exigem isolamento físico contratual.
4. *Híbrido "pool compute, silo data"* — compute compartilhado, banco
   dedicado por tenant grande — o formato de maturidade típico quando um
   produto cresce além do estágio inicial.

**Decisão desta revisão**: nenhum dos 4 é implementado agora. O que é
implementado é a precondição pro modelo 1 (o mais barato e o que qualquer
um dos outros 3 evolui a partir dele): toda tabela nova de config/registro
das Fases 1-5 (`config_dinamica`, `route_registry`, tabela de junção
`agente_tools`) nasce com uma coluna `tenant_id UUID NULL` — nula hoje
significa "config global da UEMA", nunca lida com filtro nenhum no código
das Fases 1-5. Índice único passa a ser `(tenant_id, chave)` em vez de só
`(chave)` — mudança que não afeta nenhuma leitura/escrita atual (UEMA
sempre com `tenant_id IS NULL`), mas evita que a Fase 9 precise recriar a
tabela inteira.

**O que isso NÃO inclui agora (deliberadamente)**: Row-Level Security
ativada, isolamento de Redis por prefixo de tenant, cota/rate-limit por
tenant, cobrança/billing, onboarding self-service. Tudo isso é Fase 9,
condicional a um evento de negócio real (§J).

**Fonte**: [ClickHouse — How to architect multi-tenant SaaS on Postgres](https://clickhouse.com/resources/engineering/multi-tenant-saas-postgres-architecture),
[Alok — Designing Multi-Tenant SaaS Systems: Isolation Models, Data Strategies, and Failure Domains](https://aloknecessary.github.io/blogs/designing-multi-tenant-saas-systems/).

---

## N. Correção e concorrência — o que "não bugue" exige de verdade

A v1 especificava `config_dinamica` com upsert simples (`on_conflict_do_update`)
e mirror write-through no Redis, sem tratar dois problemas reais de
corretude que qualquer sistema com múltiplos admins editando o mesmo estado
compartilhado precisa resolver:

**1. Lost update entre dois admins editando a mesma chave.** Sem controle
de versão, admin A lê `RAG_CACHE_TTL_SECONDS=3600`, admin B lê o mesmo
valor, A salva `1800`, B — sem saber da mudança de A — salva `7200`
baseado no valor que **B** tinha lido, apagando silenciosamente a mudança de
A. Padrão de correção: **optimistic concurrency control** — cada linha
ganha uma coluna `versao` (inteiro, incrementada a cada escrita); o UPDATE
inclui `WHERE versao = :versao_lida`; se afetar 0 linhas, outra escrita
aconteceu no meio — a API retorna conflito (HTTP 409) em vez de aplicar a
escrita cegamente, e o Hub mostra "este valor mudou desde que você abriu a
tela, recarregue antes de salvar". Custo de implementação: 1 coluna + 1
cláusula WHERE — não é infraestrutura nova.

**2. Drift silencioso entre Postgres e o espelho Redis.** O desenho
"write-through, sem TTL" da v1 assume implicitamente que a escrita no Redis
nunca falha depois que a escrita no Postgres já teve sucesso — mas rede
falha, Redis fica indisponível por segundos, processos são mortos no meio.
Se isso acontecer, a v1 deixava Postgres e Redis divergentes **para
sempre**, sem nenhum mecanismo de detectar ou corrigir isso — o oposto de
"não bugue". Correção: toda leitura que encontra o valor no Redis
continua rápida (~1ms, sem mudança); toda leitura que dá **miss** no Redis
lê o Postgres e **reescreve o Redis antes de retornar** (read-repair) — o
próximo tráfego já sana a divergência sem intervenção humana, sem job de
reconciliação separado pra manter.

**3. Rollback de uma mudança ruim.** A v1 tinha auditoria de "quem mudou o
quê, quando" mas não guardava o **valor anterior** de forma consultável —
reverter exigia lembrar de cabeça o valor antigo e editar de novo. Correção:
tabela `config_dinamica_historico` (append-only, nunca `UPDATE`/`DELETE`)
com `chave, valor_antigo, valor_novo, versao, atualizado_por, atualizado_em`
— o Hub ganha um botão "reverter pra esta versão" que é, tecnicamente, só
mais uma escrita normal (respeitando o controle de versão do item 1).

**Fontes**: [Optimistic Concurrency with SQL Version Columns](https://alexanderobregon.substack.com/p/optimistic-concurrency-with-sql-version),
[Databricks — Concurrency Control in DBMS](https://www.databricks.com/blog/concurrency-control).

---

## O. Resiliência de provider — circuit breaker, não só failover manual

Hoje, se o Gemini degradar (latência alta, taxa de erro subindo, rate
limit), a única resposta do sistema é o admin notar via `/hub/llm-custo` e
trocar manualmente o provider ativo no Redis. Para um produto vendido a
terceiros, "alguém precisa estar olhando o dashboard às 3h da manhã" não é
uma resposta aceitável.

**Padrão recomendado, adotado na Fase 3**: circuit breaker por provider,
como wrapper fino sobre a chamada já existente em `_instanciar` — não é uma
reescrita do `llm_factory`, é uma camada de observação em cima dele.
Parâmetros de partida, alinhados ao que a prática de produção já convergiu:
abrir o circuito depois de ~5 falhas consecutivas ou taxa de erro >5% numa
janela curta; período de resfriamento de ~60s antes de tentar de novo
(half-open); alerta (não decisão automática de troca de provider — isso
continua sendo escolha do admin) quando o circuito abre. Diferença chave
em relação a circuit breakers de microserviço clássicos: como a resposta de
um LLM não é binária sucesso/falha, o circuito também deve considerar
degradação de qualidade (ex.: taxa de respostas vazias/truncadas), não só
exceção de rede — mas isso é refinamento de Fase 6, não bloqueador da Fase 3.

**O que isso NÃO é**: troca automática de provider ativo sem aprovação
humana. Trocar o provider que atende produção é uma decisão com implicação
de custo e qualidade — o circuito abre e alerta; quem decide trocar
continua sendo o admin via Hub, agora com um sinal automático em vez de
descoberta manual.

**Fontes**: [Portkey — Retries, fallbacks, and circuit breakers in LLM apps](https://portkey.ai/blog/retries-fallbacks-and-circuit-breakers-in-llm-apps/),
[Circuit Breaker Patterns for AI Agent Reliability](https://brandonlincolnhendricks.com/research/circuit-breaker-patterns-ai-agent-reliability).

---

## P. Segredos — o caminho para "enterprise-ready", não implementado ainda

A v1 tratava segredos como decisão fechada e fora de escopo. Esta revisão
não implementa nada aqui — mas registra por que isso deixa de poder ser
"fora de escopo para sempre" no momento em que o Oráculo tiver um segundo
cliente pagante, e qual é o caminho quando isso acontecer.

**Por que o `.env` editável via browser é um bloqueador real, não só uma
dívida cosmética**: qualquer cliente enterprise com requisito de segurança
formal (SOC 2, ISO 27001, ou simplesmente um time de segurança que revisa
fornecedores) vai perguntar "onde ficam minhas credenciais e quem tem
acesso a elas" — e "num arquivo de texto editável por qualquer admin do
painel web" reprova essa conversa antes de começar.

**Caminho recomendado quando (se) isso for necessário** (Fase 10,
condicional): um secrets manager dedicado (HashiCorp Vault, AWS Secrets
Manager ou equivalente gerenciado) por trás de uma interface mínima —
`get_secret(nome) -> str`, que hoje seria implementada lendo `.env` e no
futuro leria do vault, sem o código consumidor saber a diferença (mesmo
princípio de Ports & Adapters já usado no resto do sistema). Para um
cenário multi-tenant real, o requisito sobe: **cada tenant precisa de
credenciais de LLM/WhatsApp isoladas** (BYOK — cliente traz a própria API
key), criptografadas com uma chave de envelope por tenant, não uma chave
mestra compartilhada — assim o vazamento de uma credencial nunca expõe a de
outro cliente.

**Decisão explícita**: não implementar nada disso agora. Não há hoje um
segundo cliente, e a `.env` continua adequada para 1 instituição só. O que
muda em relação à v1 é o texto da decisão — de "resolvido, fora de escopo
permanente" para "dívida reconhecida com caminho conhecido, revisitar no
primeiro sinal real de venda a terceiro".

**Fontes**: [OWASP — Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html),
[WorkOS Vault — envelope encryption por contexto](https://workos.com/docs/vault),
[Microsoft Learn — Key Vault por tenant em SaaS multi-tenant](https://learn.microsoft.com/en-us/azure/key-vault/general/secure-key-vault).

---

## Q. Por que não adotar um motor de política genérico (OPA) nem feature-flag SaaS de terceiros

Duas tentações de "arquitetura de plataforma madura" que a pesquisa externa
desta revisão avalia e **rejeita explicitamente**, com critério, não por
reflexo:

**Open Policy Agent / policy-as-code**: motores de política valem a pena
quando há múltiplos runtimes/serviços exigindo decisão de política
consistente entre eles, ou quando existe requisito formal de decisão
auditável e legível por humano (ex.: regulador exige ver a regra escrita
separada do código). Nenhum dos dois é o caso do Oráculo hoje — RBAC/HITL/
Gatekeeper vivem dentro de um único processo Python, e a auditoria já é
resolvida por log estruturado (§R), não por precisar de uma regra em Rego.
Adotar OPA agora seria trocar `if`/dict Python — que qualquer pessoa do
time já lê — por uma linguagem de política nova, uma peça de
infraestrutura nova, pra resolver um problema (política inconsistente entre
serviços) que não existe com 1 processo só. **Critério de reavaliação**:
se/quando existirem múltiplos serviços/produtos distintos precisando da
mesma política de RBAC, ou um cliente exigir contratualmente política
auditável fora do código-fonte.

**Feature-flag SaaS de terceiros (LaunchDarkly/Unleash)**: o valor central
dessas plataformas — propagação de mudança em milissegundos pra milhares de
instâncias distribuídas, segmentação de usuário fina, experimentação A/B —
não corresponde a nenhuma dor real do Oráculo hoje (poucas instâncias, sem
necessidade de propagação sub-segundo, sem programa de experimentação). O
padrão conceitual deles (kill switch, rollout percentual, auditoria) **já
informa o design de `config_dinamica`** — é exatamente por isso que o Hub
ganha histórico e não só "ligado/desligado" — mas rodar a infraestrutura
deles seria pagar o custo operacional e financeiro de uma ferramenta feita
pra um problema de escala que o Oráculo não tem.

**Fontes**: [Wiz — What is Open Policy Agent? Best Practices + Use](https://www.wiz.io/academy/application-security/open-policy-agent-opa),
[LaunchDarkly — Feature Flags 101](https://launchdarkly.com/blog/what-are-feature-flags/).

---

## Q-bis. Config-as-Code — só se um cliente pedir, mas o desenho já não impede

Empresas grandes frequentemente preferem revisar mudança de configuração
via pull request (com aprovador, diff, histórico no Git) em vez de um botão
num painel web — é o mesmo raciocínio de change management que já se aplica
a infraestrutura. Isso **não** é implementado agora (Fase 11, condicional),
mas vale registrar que o desenho de `config_dinamica` com histórico
versionado (§N) já é compatível com um export/import futuro (dump de todas
as chaves ativas pra um YAML, revisável em PR, aplicado via endpoint
idempotente) sem precisar de redesenho — a diferença entre "só Hub" e
"Hub + GitOps opcional" é um endpoint de export/import a mais, não uma
segunda fonte de verdade.

**Fonte**: [dev.to — Configuration as Code: The Missing GitOps Layer in Multi-Tenant SaaS](https://dev.to/sbimochan/configuration-as-code-the-missing-gitops-layer-in-multi-tenant-saas-1kph).

---

## R. Auditoria à prova de compliance

O que já existe (`atualizado_por`/`atualizado_em` em `agentes_catalogo`/
`llm_pricing`, `RedisAuditLog` nos endpoints admin) cobre "quem mudou o quê,
quando" — suficiente pra operação interna hoje. O que um cliente com
requisito de compliance formal (SOC 2 Tipo II, por exemplo) vai perguntar
é: **"esse log pode ser apagado ou alterado por um admin comprometido?"**.
Hoje a resposta honesta é "sim, tecnicamente pode" — não é um ataque
provável, mas é uma lacuna real na história de confiança do produto.

**Caminho recomendado, não implementado agora**: log de auditoria
append-only por desenho (sem `UPDATE`/`DELETE` nunca emitido contra a
tabela, reforçado por permissão de banco, não só disciplina de aplicação);
opcionalmente, cadeia de hash entre entradas consecutivas (cada linha
inclui o hash da anterior) pra que qualquer adulteração retroativa seja
matematicamente detectável, sem precisar de infraestrutura de blockchain —
é o mesmo princípio de um Merkle-ish log, aplicável a uma tabela Postgres
comum. Isso generaliza — não substitui — o padrão de auditoria que
`config_dinamica_historico` (§N) já propõe.

**Decisão desta revisão**: registrar o gap, não fechar a lacuna agora. O
esforço de tornar o log criptograficamente à prova de adulteração só se
justifica quando houver, de fato, um processo de auditoria externo
cobrando essa garantia.

**Fontes**: [Bytebase — SOC 2 Audit Log Requirements](https://www.bytebase.com/blog/soc2-audit-logging/),
[Hoop.dev — Immutability for SOC 2](https://hoop.dev/blog/immutability-for-soc-2-how-to-protect-evidence-logs-and-records-permanently/).

---

## S. Capability Manifest — o contrato do Registry Layer

A v1 descrevia o Registry Layer como "dict de builders", no molde do
`parser_factory.py` — correto como mecanismo, mas subespecificado como
contrato: um dict `{"nome": builder_fn}` não diz nada sobre versão de
interface esperada, se o provider está de fato saudável, ou que permissões
ele precisa. Isso é seguro **hoje** porque só o próprio time escreve
adapters — mas é exatamente o tipo de lacuna que se paga caro se o
Registry Layer crescer (Fase 5-6) sem um contrato mínimo.

**Recomendação, adotada a partir da Fase 3 (Provider Registry)**: cada
entrada do registro carrega um manifesto pequeno e estático — nome, versão
de interface implementada (`ILLMProvider` v1, por exemplo), e um
`health_check()` opcional que o circuit breaker (§O) usa. Isso **não** é
plugin architecture com carregamento dinâmico de código externo — continua
sendo registro explícito, no mesmo arquivo, pelo mesmo time, só que
autodescritivo o suficiente para o Hub mostrar "disponível, versão X, saúde
Y" em vez de só o nome cru.

---

## T. Verificação e "definition of done" (endurecido nesta revisão)

Todo item novo de config precisa, além dos testes unitários da fábrica/
registro tocado:

- **Teste de degradação**: simular Postgres fora do ar e Redis fora do ar
  separadamente — confirmar que o sistema cai pro default hardcoded sem
  exceção não tratada, em ambos os casos.
- **Teste de concorrência** (novo, §N): duas escritas simultâneas na mesma
  chave — confirmar que a segunda recebe conflito de versão, nunca que a
  primeira é silenciosamente perdida.
- **Teste de drift** (novo, §N): forçar um valor diferente entre Postgres e
  Redis manualmente, confirmar que a próxima leitura corrige o Redis.
- **1 teste manual de ponta a ponta** de pelo menos 1 fluxo real antes de
  considerar a fase concluída — mesma disciplina da v1.

Verificação por fase (herdada da v1, sem mudança):

- Fase 1: `pytest tests/unit/infrastructure/test_dynamic_config*.py` +
  toggle via `/hub/config` local + confirmar leitura sem restart no worker
  Celery.
- Fase 2: nova rota chega ao Graph certo sem editar `dispatcher_langgraph.py`;
  zero regressão nas 11 rotas atuais.
- Fase 3: circuit breaker abre de fato sob falha simulada do provider ativo
  e fecha depois do período de resfriamento.

---

## Anexo I — Fase 1 (Dynamic Configuration), plano detalhado, endurecido nesta revisão

Tabela `config_dinamica` (migration 009) com 7 chaves iniciais seedadas
(`DEV_TEST_NO_DB_WRITE`, `DEV_TEST_SKIP_REGISTRATION`,
`FEATURE_LANGGRAPH_NATIVE_ROUTES`, `FEATURE_LANGGRAPH_CELERY_DISPATCH`,
`GEMINI_MODEL`, `RAG_CACHE_TTL_SECONDS`, `RAG_RERANKER_ENABLED`) —
**mais, nesta revisão**: coluna `versao` (inteiro, default 1, incrementada
a cada escrita) e coluna `tenant_id UUID NULL` (§M); tabela irmã
`config_dinamica_historico` (append-only, §N) alimentada pelo mesmo upsert.
Repositório com upsert condicionado à versão lida (`WHERE versao = :v`,
retorna conflito se 0 linhas afetadas); `dynamic_config.py` com
`get_bool/get_int/get_str` (Redis→Postgres→default, com read-repair no
miss, §N); endpoints `GET/POST /api/admin/config` (POST agora exige e
retorna a versão); extensão de `/hub/config` com histórico e botão
"reverter". Detalhamento completo (assinaturas de função, SQL de seed,
testes) disponível — a implementar como Fase 1 assim que este documento for
aprovado.

---

## Fontes desta revisão (v2)

- [ClickHouse — How to architect multi-tenant SaaS on Postgres](https://clickhouse.com/resources/engineering/multi-tenant-saas-postgres-architecture)
- [Alok — Designing Multi-Tenant SaaS Systems: Isolation Models, Data Strategies, and Failure Domains](https://aloknecessary.github.io/blogs/designing-multi-tenant-saas-systems/)
- [Optimistic Concurrency with SQL Version Columns](https://alexanderobregon.substack.com/p/optimistic-concurrency-with-sql-version)
- [Databricks — Concurrency Control in DBMS: How Locking, MVCC and Optimistic Strategies Keep Data Consistent](https://www.databricks.com/blog/concurrency-control)
- [Portkey — Retries, fallbacks, and circuit breakers in LLM apps: what to use when](https://portkey.ai/blog/retries-fallbacks-and-circuit-breakers-in-llm-apps/)
- [Brandon Lincoln Hendricks — Circuit Breaker Patterns for AI Agent Reliability](https://brandonlincolnhendricks.com/research/circuit-breaker-patterns-ai-agent-reliability)
- [OWASP — Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [WorkOS Vault docs — envelope encryption por contexto/tenant](https://workos.com/docs/vault)
- [Microsoft Learn — Secure your Azure Key Vault (isolamento por tenant em SaaS)](https://learn.microsoft.com/en-us/azure/key-vault/general/secure-key-vault)
- [Wiz — What is Open Policy Agent (OPA)? Best Practices + Use](https://www.wiz.io/academy/application-security/open-policy-agent-opa)
- [LaunchDarkly — Feature Flags 101: Use Cases, Benefits, and Best Practices](https://launchdarkly.com/blog/what-are-feature-flags/)
- [dev.to — Configuration as Code: The Missing GitOps Layer in Multi-Tenant SaaS](https://dev.to/sbimochan/configuration-as-code-the-missing-gitops-layer-in-multi-tenant-saas-1kph)
- [Bytebase — SOC 2 Audit Log Requirements: Lessons From Our Own Audit](https://www.bytebase.com/blog/soc2-audit-logging/)
- [Hoop.dev — Immutability for SOC 2: How to Protect Evidence, Logs, and Records Permanently](https://hoop.dev/blog/immutability-for-soc-2-how-to-protect-evidence-logs-and-records-permanently/)

(Fontes da v1 — CVE stacklok/toolhive, MCP Registry, Pinecone/AWS RAG access
control — continuam válidas e citadas nas seções que herdam raciocínio da
v1: §G, §M.)
