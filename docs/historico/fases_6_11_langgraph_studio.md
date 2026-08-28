# Oráculo — Fases 6–11: Da Plataforma Orientada a Config à Studio Visual (LangGraph Studio Inspired)

> **Status: Proposta de Roadmap — 2026-08-28**  
> Consolidação das Fases 6–11 do plano de plataforma orientada a configuração,
> com redesign da Fase 2 (adendo de nós declarativos) e inspiração visual/
> arquitetural em LangGraph Studio. As Fases 1–5 estão concluídas e em produção;
> estas 6–11 descrevem a evolução natural, com dependências claras e gatilhos
> de decisão (demanda vs. evento de negócio).

---

## A. Visão — Do "Config Manager" ao "Agent Graph Studio"

**Hoje** (Fases 1–5):
- Hub Admin permite ligar/desligar providers, parsers, tools — UI de tabelas/toggles.
- Rotas e workflows codificados ou dados estáticos em Postgres.
- Nós do LangGraph (Supervisor, Planner, agentes) vivem em `src/graph/`.

**Amanhã** (Fases 6–11):
- Hub vira um **graph studio** visual, tipo LangGraph Studio:
  - Arrastar/soltar nós (STT, TTS, LLM providers, parsers, tools, agentes).
  - Conectar dependências (output de um nó → input de outro).
  - Visualizar fluxo em tempo real: dados entrando, sendo processados, saindo.
  - Versionamento + rollback de grafos (igual a configs dinâmicas).
  - Multi-tenant ready: cada cliente edita seu grafo sem afetar outros.

**Diferença com LangGraph Studio**:
- Studio é *editor* (compile/save/test localmente).
- Oráculo Hub é *runtime admin* (vive em produção, tudo ao vivo).

---

## B. Arquitetura das Fases 6–11 — Três pilares novos

### 1. **Camada de Nós Declarativa** (adendo integrado)
```
REGISTRY LAYER (código, muda em deploy)
────────────────────────────────────────
BaseNode (abc) + NodeRegistry (autodiscovery)
  ├─ InputPort / OutputPort (tipos declarados)
  ├─ Metadata (name, version, schema)
  └─ Health check opcionalmente

Todos os providers + agentes herdam de BaseNode.
```

### 2. **Camada de Grafo Dinâmico** (Fase 6–8 consolidadas)
```
CONFIGURATION LAYER (dados, muda em runtime)
──────────────────────────────────────────────
graph_topology (tabela nova):
  id, tenant_id, name, description, version
  topology_json (serializado: {"nodes": [...], "edges": [...]})
  status (draft/active/deprecated)
  atualizado_por, atualizado_em, tenant_id

graph_node_bindings (junção nó↔provider):
  graph_id, node_id (string, ex.: "rag_search", "llm_primary")
  provider_registry_id, config_overrides (JSON)
  fallback_provider_id (opcional, pra circuit breaker)

Validação: topologia é DAG (acíclica), tipos de porta casam.
```

### 3. **Camada de Runtime** (execução do grafo)
```
GraphExecutor:
  ├─ parse(topology_json) → DAG validado
  ├─ bind_providers(provider_registry) → nós resolvidos com instâncias
  ├─ execute(input_data, tracer) → streaming de eventos por aresta
  └─ fallback_chain (circuit breaker em cada nó se configurado)

Cada aresta é um span do OpenTelemetry com `gen_ai.*` semântica.
```

---

## C. Roadmap das Fases — Ordem, Dependências, Gatilhos

### **Fase 6: STT/TTS/Embeddings — Node-ificação**

**O quê**: Estender `BaseNode` aos providers de áudio e embeddings. Não é "suporte novo", é **uniformizar o que já existe** sob a nova abstração de nós.

| Aspecto | Hoje | Amanhã |
|---|---|---|
| **Interface** | `if/elif` esparso em 3 arquivos | `BaseNode` + `NodeRegistry` |
| **Binding** | Hardcoded em `settings` | `graph_node_bindings` (multiplos por grafo) |
| **Config** | Sem dinamismo | Config dinâmica (taxa de amostra, idioma de STT, dim de embedding) |
| **Health** | Nenhum | Circuit breaker por provider (herança de Fase 3) |

**Implementação** (1–2 sprints):
1. `src/graph/nodes/stt_node.py`, `tts_node.py`, `embeddings_node.py` — herdam `BaseNode`.
2. Manifestos de capability (input/output types, config schema).
3. Seed em `graph_node_bindings` para grafo "default" (o atual).
4. Testes: topologia válida + execute com falha de provider → fallback.

**Hub**: página `/hub/graph-nodes` lista todos os nós registrados (LLM, STT, TTS, Embeddings, Parsers, Tools, agentes). Mostra saúde de cada um.

**Gatilho de inicio**: Demanda por multi-provider de áudio (ex.: Gemini STT + ElevenLabs TTS na mesma conversa) OU preparar o terreno pra Fase 7 (channels).

**Status proposto**: 🟢 Iniciar junto com Fase 1 de grafo, ou 🟡 Adiado até demanda.

---

### **Fase 7: Channel Abstraction (Telegram, Slack, etc.) — Inbound/Outbound Ports**

> **Atualização 2026-08-28**: metade "saída" já implementada —
> `ChannelNode` (`src/graph/nodes/channel_node.py`) envolve
> `EvolutionAdapter` (WhatsApp) com ações `text`/`typing`/`media_url`,
> registrado no NodeRegistry, visível em `/hub/graph-nodes`. **Inbound
> (webhook) continua fora do modelo de nó** — decisão explícita: um
> webhook HTTP é um *trigger* (evento externo que inicia um fluxo), não um
> nó que se chama com `inputs` e devolve `outputs` no mesmo request, que é
> o contrato de `BaseNode.execute()`. Hoje inbound continua em
> `src/application/webhook/webhook_controller.py` (rota `POST
> /webhook/evolution`) + task Celery, sem mudança. Modelar "trigger" como
> conceito de grafo (distinto de "nó de execução") é decisão de design
> ainda em aberto — não é só "criar mais um Node", é decidir se o
> `BaseNode`/`NodeRegistry` atual sequer é o lugar certo pra isso, ou se
> merece uma abstração irmã (`TriggerNode`/`EventSource`). Fica registrado
> aqui pra não se perder, não decidido ainda.

**O quê**: Generalizar EvolutionAdapter (WhatsApp) pra um pattern `ChannelNode` com porta de entrada (webhook de mensagem) e saída (send message).

| Aspecto | Hoje | Amanhã |
|---|---|---|
| **Múltiplos canais** | 1 só: WhatsApp (EvolutionAdapter hardcoded em 7+ pontos) | `ChannelNode` para Telegram, Slack, Discord, etc. |
| **Orchestração** | Sem coordenação entre canais | Um grafo por canal (ou compartilhado, configurável) |
| **Rate limit / quotas** | Por provider, não por canal | Por canal + por tenant (Fase 9) |

**Implementação** (2–3 sprints, maior esforço):
1. `ChannelNode` (abstrato) + adapters concretos (`TelegramChannelNode`, `SlackChannelNode`).
2. Tabela `channel_configs` (similiar a `graph_node_bindings`, mas pra canais).
3. Webhook router novo: recebe evento de **qualquer** canal, associa ao grafo certo, executa.
4. SSRF validation obrigatória em todo novo código (requisito firme).
5. Testes: webhook simulado, múltiplos canais em paralelo, isolamento de tenant (Fase 9).

**Hub**: `/hub/channels` — painel pra ativar/desativar canais, status de conectividade, logs por canal.

**Gatilho**: Demanda por suporte a Telegram/Slack OU preparar multi-channel como feature de produto.

**Status proposto**: 🟡 Adiado até demanda concreta.

**Risco**: Mais alto que Fase 6 — SSRF, auth de terceiros, webhook delivery — justifica esperar demanda real.

---

### **Fase 8: MCP Connection Manager — Integração com LLM Provider**

**O quê**: Sacar MCP do `mcp_lab/` (laboratório) pra Fase 5 produção, como um `ToolProviderNode` especial. Conexão gerenciada, validação SSRF rigorosa.

| Aspecto | Hoje | Amanhã |
|---|---|---|
| **Status** | Laboratório prova-de-conceito | Feature de produto, versionada |
| **Integração** | 3 URLs hardcoded, regex de prefixo | MCP registry (tabela), autodiscovery com health check |
| **Segurança** | Nenhuma validação SSRF | RFC1918/loopback/link-local bloqueados, sem redirect pra espaço privado |
| **Binding a LLM** | Manual (não existe) | `function_calling_provider` em `graph_node_bindings` |

**Implementação** (2 sprints, alto risco de segurança):
1. `MCPToolProviderNode` herda `BaseNode`.
2. Tabela `mcp_servers` (name, url, capabilities, health_status, last_checked).
3. Validador de URL (RFC 1918, loopback, link-local, redirect detection).
4. Circuit breaker: MCP server cai → fallback pra sem function-calling, não erro.
5. Integração com LLM provider: `bind_tools()` respeitando versão de interface.
6. Testes: SSRF bloqueado, tool schema compatível com Gemini 2.5.

**Hub**: `/hub/mcp-servers` — registrar, testar, health check, bind a um LLM.

**Gatilho**: Apresentação na CETIC/UEMA que inclua MCP OU demanda de "integração com ferramentas externas" de um cliente.

**Pré-requisito**: Tool calling nativo (`google.genai` com `bind_tools()`, não LangChain). Deve estar vivo em main, não em branch isolada.

**Status proposto**: 🟡 Adiado até pré-requisito + gatilho.

---

### **Fase 2–Adendo: Nós Declarativos — Integração com Grafo**

**O quê**: A proposta que já existe em `arquitetura_nos_declarativa.md`, agora **integrada com as Fases 6–8**.

**Camadas** (recomendação da proposta original, reafirmada):

| Camada | Escopo | Vale? | Quando |
|---|---|---|---|
| **1** | `BaseNode` + `NodeRegistry` (autodiscovery `pkgutil`) | ✅ Sim — base pra tudo | Junto com Fase 6 |
| **2** | Spec declarativa pra fan-out simples (rota → {RAG, Greeting, SIGAA}) | 🟡 Condicional | Depende de Camada 1 provar valor; recomendação: não iniciar agora |
| **3** | Subgrafos (funis ticket/CRUD em código como nó composto) | ✅ Sim | Depois de Camada 1; recomendação: não forçar tudo pra declarativo |

**Implementação de Camada 1** (parte de Fase 6):
1. `src/graph/base_node.py`: classe abstrata com `InputPort`, `OutputPort`, `execute()`.
2. `src/graph/node_registry.py`: autodiscovery via decorator (tipo `capabilities/registry.py`).
3. Todos os providers herdam de `BaseNode`.
4. Hub mostra lista de nós registrados com schema.
5. Teste: grafo mínimo (2 nós, 1 aresta), valida tipos de porta, executa.

**Por que não Camadas 2–3 ainda?**
- Camada 2 (fan-out declarativo) é nice-to-have, não essencial. Hoje o classificador LLM já **é** a fan-out.
- Camada 3 (subgrafos em código) precisa de Camada 1 madura + experiência de usar grafos em produção.

**Status proposto**: 🟢 Camada 1 junto com Fase 6. Camadas 2–3 condicionais.

---

### **Fase 9: Multi-Tenancy Real — Isolamento na Execução**

**O quê**: As colunas `tenant_id` que já existem (mas sempre nulas) passam a ser usadas. Um cliente = um grafo isolado.

**Pré-requisitos** (Fase 9 **não pode** iniciar sem estes):
- ✅ `graph_topology` com `tenant_id` (Fase 6–8)
- ✅ `graph_node_bindings` com `tenant_id` (Fase 6–8)
- ⏸ Um **segundo cliente real** assinando (evento de negócio)
- ⏸ Separação de secrets por tenant (Fase 10 ou workaround)

**Implementação** (2–3 sprints):
1. GraphExecutor recebe `tenant_id` como contexto obrigatório.
2. Toda query de grafo/provider filtra por `WHERE tenant_id = :t`.
3. Redis: namespace por tenant (`tenant:{tenant_id}:config:{key}`).
4. Rate-limiting por tenant (quotas de token, chamadas).
5. Row-Level Security do Postgres ativada (opcional, mas recomendado).
6. Testes: dois tenants em paralelo, nenhum vazamento de dados.

**Hub**: identidade de tenant em toda operação (header `X-Tenant-ID` ou JWT claim).

**Gatilho**: Primeira venda real a um segundo cliente OU decisão de negócio explícita "Oráculo vira produto multi-tenant".

**Status proposto**: 🟡 Condicional a evento de negócio.

---

### **Fase 10: Secrets Manager / BYOK — Isolamento de Credenciais**

**O quê**: Sair do `.env` editável via browser (segurança nula) pro Vault/KMS (segurança enterprise).

**Pré-requisitos**:
- ✅ Caminho explícito definido (em `plataforma_orientada_a_configuracao.md` §P)
- ⏸ Um cliente enterprise com requisito de segurança formal (SOC 2, ISO 27001)
- ⏸ Ou decisão de negócio "Oráculo é produto vendido a terceiros"

**Implementação** (1–2 sprints, se acontecer):
1. `get_secret(nome) -> str` abstração, hoje lê `.env`, amanhã lê Vault.
2. Secrets manager concreto: HashiCorp Vault (recomendação) ou AWS Secrets Manager.
3. Por tenant (Fase 9 + 10): cada tenant traz sua chave de LLM, WhatsApp, etc. (BYOK).
4. Criptografia de envelope: chave mestre no KMS, chaves de tenant derivadas, isoladas.
5. Rotação de chaves + auditoria (quem acessou qual secret, quando).

**Hub**: Interface de secret, sem exibição (view-only ou totalmente oculto, acesso via API).

**Gatilho**: Primeira conversa com cliente que pede "onde ficam minhas credenciais?".

**Status proposto**: 🟡 Condicional a requisito de compliance.

---

### **Fase 11: Config-as-Code / GitOps — Versionamento de Grafos no Git**

**O quê**: Exportar topologias de grafo + configs pra YAML, versionável em Git, aplicável via PR.

**Pré-requisitos**:
- ✅ `graph_topology` com histórico/versioning (Fases 6–8)
- ⏸ Um cliente pedindo "mudança de config via PR, não via UI"

**Implementação** (1–2 sprints, baixo-médio risco):
1. Endpoint `GET /api/admin/graph/export/{graph_id}` → YAML (nós + bindings + configs).
2. Endpoint `POST /api/admin/graph/import` ← YAML validado (schema checking).
3. Comparação antes/depois (diff).
4. Rollback via histórico de versões (idempotente).
5. CI hook opcional: validar YAML em PR, aplicar em merge.

**Hub**: Botão "export to YAML" + form de import com preview.

**Gatilho**: Cliente grande pedindo "change management via Git".

**Status proposto**: 🟡 Condicional.

---

## D. Hub — Redesign como "Graph Studio"

### Novo layout da seção `/hub/graph-studio/`

```
┌─────────────────────────────────────────────────────┐
│ Oráculo Graph Studio                            [?] │
├─────────────────────────────────────────────────────┤
│ ┌───────────────────────────────────────────────┐   │
│ │ Active Graph: "Production RAG + Backup LLM"   │   │
│ │ Tenant: UEMA | Status: 🟢 Healthy            │   │
│ └───────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────┤
│ [New Graph] [Edit] [Export] [History]               │
├─────────────────────────────────────────────────────┤
│                                                       │
│  ┌─────────────┐     ┌─────────────┐                │
│  │ STT Node    │────▶│ LLM Gemini  │──────┐          │
│  │ (Whisper)   │     │ (Pro v2.0)  │      │          │
│  └─────────────┘     └─────────────┘      │          │
│                                            ▼          │
│  ┌─────────────┐     ┌──────────────┐   ┌──────────┐ │
│  │ RAG Search  │────▶│ Reranker CE  │──▶│ Output   │ │
│  │ (HNSW)      │     │ (Cross-Enc)  │   │ (TTS+WA) │ │
│  └─────────────┘     └──────────────┘   └──────────┘ │
│                                                       │
│  [+ Add Node] [Validate] [Test] [Deploy]             │
│                                                       │
│  Metrics:                                            │
│  ├─ Throughput: 45 msgs/min                          │
│  ├─ Latency: p50=320ms, p99=1200ms                   │
│  ├─ Error rate: 0.2%                                 │
│  └─ Nodes healthy: 7/7 🟢                            │
│                                                       │
└─────────────────────────────────────────────────────┘
```

**Seções novas do Hub** (mapa de navegação):

| Rota | O quê | Fase |
|---|---|---|
| `/hub/graph-studio` | Editor visual de grafo (canvas com nós/arestas) | 6–8 |
| `/hub/graph-nodes` | Registry de nós (STT, TTS, LLM, tools, agentes) | 6 |
| `/hub/channels` | Gerenciar canais (WhatsApp, Telegram, Slack) | 7 |
| `/hub/mcp-servers` | Registrar/testar servidores MCP | 8 |
| `/hub/graph-history` | Versões anteriores, rollback | Junto com Fase 6 |
| `/hub/graph-export` | Export/import YAML, GitOps | 11 |

**Frontend**: Usar Lucide icons (já adotado em Plano B) + Chart.js (vendorado) pra métricas.

---

## E. Validação e Testes — "Definition of Done" por Fase

### Fase 6 (STT/TTS/Embeddings):
- ✅ `test_graph_nodes_stt_tts.py` — topologia válida, types casam.
- ✅ `test_fallback_chain.py` — provider principal cai → fallback funciona.
- ✅ `test_dynamic_config_stt.py` — mudar taxa de amostra sem restart.
- ✅ Manual: `/hub/graph-nodes` lista todos registrados.

### Fase 7 (Channels):
- ✅ `test_webhook_router.py` — webhook de Telegram recebido, roteado certo.
- ✅ `test_channel_isolation.py` — dois canais em paralelo, sem contaminar um ao outro.
- ✅ `test_ssrf_validation.py` — rejeta URL privada / localhost.
- ✅ Manual: `/hub/channels` conecta e recebe mensagens real de teste.

### Fase 8 (MCP):
- ✅ `test_mcp_node_registry.py` — servidor registrado, schema valid.
- ✅ `test_mcp_ssrf.py` — rejeta redirect pra RFC1918.
- ✅ `test_circuit_breaker_mcp.py` — servidor cai → fallback sem erro.
- ✅ Manual: `/hub/mcp-servers` testa conexão, listar tools do servidor.

### Fases 9–11:
- Mesmo template acima, ajustado por fase.

---

## F. Sequência recomendada e dependências

```
Hoje (2026-08-28)
Fases 1–5: ✅ Concluídas, em produção
├─ Plano A: Config dinâmica, Route Registry, Provider Registry, Parser Registry, Tool Registry
└─ Plano B: Frontend unificado, design system novo, zero inline styles/scripts

▼

Camada de Nós Declarativos (Camada 1, adendo)
└─ BaseNode + NodeRegistry
  └─ Prerequisite pra Fases 6–8

▼

Fase 6: STT/TTS/Embeddings Nodes
├─ Generaliza padrão de providers
├─ Traz circuit breaker uniforme
└─ Maior valor: prepare terrain pra Fase 7

▼

Fase 7: Channel Abstraction (⏸ adiado até demanda)
├─ Alto risco (SSRF, webhooks)
└─ Gatilho: suporte a Telegram/Slack

▼

Fase 8: MCP Connection Manager (⏸ adiado até demanda + pré-req)
├─ Alto risco de segurança
├─ Pré-req: tool calling nativo em main
└─ Gatilho: apresentação CETIC ou demanda de cliente

▼

Fase 2–Adendo (Camadas 2–3): Subgrafos em Código
├─ Depende de Camada 1 + experiência de Fases 6–8
└─ Recomendação: não iniciar enquanto Camada 1 não provar valor

▼

Fase 9: Multi-Tenancy Real (⏸ condicional a evento de negócio)
├─ Pré-req: segundo cliente real OU decisão explícita
├─ Usa: graph_topology, graph_node_bindings com tenant_id
└─ Ativa: isolamento de dados, rate-limit por tenant

▼

Fase 10: Secrets Manager (⏸ condicional a compliance)
├─ Gatilho: cliente enterprise ou venda a terceiro
└─ Implementa: BYOK, envelope encryption, auditoria

▼

Fase 11: GitOps (⏸ condicional a pedido de cliente)
└─ Gatilho: cliente pede versionamento de grafo em Git
```

---

## G. Impacto de código — Arquivos a criar/modificar

| Fase | Arquivos novos | Modificações |
|---|---|---|
| **Camada 1** | `src/graph/base_node.py`, `node_registry.py`, `nodes/__init__.py` | Todos os providers herdam `BaseNode` |
| **Fase 6** | `src/graph/nodes/stt_node.py`, `tts_node.py`, `embeddings_node.py` | `src/graph/llm_node.py` já existe? Refatorar pra padrão comum. |
| **Fase 7** | `src/graph/nodes/channel_node.py`, adapters (`telegram`, `slack`) | `src/api/webhook.py` novo, `gatekeeper.py` reescrito |
| **Fase 8** | `src/graph/nodes/mcp_node.py`, `mcp_connection_manager.py` | Sair de `mcp_lab/`, integrar em `src/` |
| **Fases 6–8** | Tabelas: `graph_topology`, `graph_node_bindings`, `mcp_servers`, `channel_configs` | Migrations 013–016 |
| **Hub** | `templates/hub/graph-studio.html`, `static/js/pages/graph-studio.js`, `static/css/pages/graph-studio.css` | Extensão de `admin_api.py` pra endpoints de grafo |

---

## H. Decisões pendentes (pré-implementação)

1. **LangGraph Studio como inspiração visual**: que fidelidade?
   - **Opção A** (leve): Visualização de nós/arestas em SVG estático, arrastar/soltar com Konva.js ou similar.
   - **Opção B** (fidedigna): Espelhar LangGraph Studio o máximo possível, com visual fiel.
   - Recomendação: **Opção A** (menor complexidade, suficiente pro job).

2. **Fase 2–Adendo: iniciar Camadas 2–3 depois de Camada 1?**
   - Recomendação: **Não.** Provar Camada 1 em produção (Fase 6) primeiro. Decisão revisitada depois.

3. **Qual é o evento de negócio "real" que dispara Fases 9–11?**
   - Hoje: não definido.
   - Recomendação: **Conversa com stakeholders** — quando Oráculo vira "produto", não "POC".

---

## I. Fontes e referências

- [LangGraph Studio — Langgraph official editor](https://langchain-ai.github.io/langgraph/concepts/persistence/),
  visual graph editing for agent workflows.
- [n8n Workflow Editor](https://n8n.io/) — inspiração pra UI de nós/arestas (referência de UX).
- [Langflow — Low-code platform for LLMs](https://www.langflow.org/).
- `docs/historico/plataforma_orientada_a_configuracao.md` — arquitetura de config orientada a dados.
- `docs/historico/arquitetura_nos_declarativa.md` — proposta original de nós declarativos.
- `docs/architecture/plano_frontend_ui_ux.md` — design system do Hub (tokens, componentes).

---

## J. Próximos passos imediatos

1. **Decidir**: Iniciar Camada 1 (BaseNode + NodeRegistry) agora ou não?
   - Se **sim**: começa Fase 6.
   - Se **não**: Fases 6–8 ficam bloqueadas.

2. **Validar com stakeholders**: Quando Oráculo deixa de ser "POC UEMA" e vira "produto"?
   - Não há data fixa — mas essa decisão desbloqueia Fases 9–11.

3. **Sign-off visual do Plano B**: Abrir `localhost:9000/hub/` em browser, revisar as 14 páginas, confirmar UX.

4. **Avaliar pré-requisito de Fase 8**: Tool calling nativo (`google.genai` com `bind_tools()`) está vivo em main ou só em branch LangGraph?

---

## Resumo em uma linha

**Fases 6–8 são extensões naturais da Camada 1 (BaseNode); Fases 9–11 são produto-scale, condicionais a evento de negócio. O Hub vira um Graph Studio visual, inspirado em LangGraph Studio, mas rodando em produção.**
