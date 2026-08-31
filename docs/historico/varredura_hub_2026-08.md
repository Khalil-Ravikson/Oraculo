# Varredura do Hub — Sprint 1 (Hub v2)

> Levantamento página a página do Hub antes do redesign (Sprints 2–4).
> Produzido em 2026-08-31 na branch `hub/redesign-htmx-infra`.
>
> **Método:** leitura de `templates/hub/*.html` + `static/js/pages/*.js` +
> `src/api/routers/web/hub.py` + `src/api/routers/admin/admin_api.py`, cruzada
> com `estado_e_roteiro_planos.md` e `plataforma_orientada_a_configuracao.md`.
> As colunas "carrega" e "efeito real" são **derivadas do código** — a
> verificação com o app rodando no browser fica pendente (ver §D).

---

## A. Resumo por página

Estado real: **✅ funciona** (ação tem efeito no runtime) · **🟡 parcial**
(grava dado, mas nenhum consumidor de produção lê) · **📊 leitura** (só
mostra dados, sem ação) · **❓ a confirmar no browser**.

| Rota | Propósito (linguagem de operador) | Estado real | Jargão exposto |
|---|---|---|---|
| `/hub/` (Painel) | Números do dia + atalhos | 📊 leitura (`home.js` busca stats reais) | baixo — "chunks indexados", links Grafana/Prometheus/Jaeger |
| `/hub/routes` | Mapa rota→execução, editável sem restart | ✅ funciona (Fase 2, `route_registry`, versão otimista, histórico/reverter) | **alto** — `route_registry (migration 010)`, `owner`, `langgraph/langgraph_conditional/legacy`, `FEATURE_LANGGRAPH_NATIVE_ROUTES`, `dispatcher.py`, `entrypoint_node`, `planner_steps` |
| `/hub/config` | Chaves dinâmicas, prompt global, manutenção, cache, credenciais | 🟡 parcial — 5 chaves reconectadas têm efeito (`GEMINI_MODEL`, `RAG_CACHE_TTL_SECONDS`, `RAG_RERANKER_ENABLED`, `PARSER_PDF_PRIORIDADE`, `PARSER_DESABILITADOS`); as demais (`DEV_TEST_*`, `FEATURE_LANGGRAPH_*`) ficam na allowlist mas o consumidor ainda lê de `settings`. Credenciais `.env` exigem restart. | **médio** — nomes de chave crus, "credenciais (.env — exige restart)", "workers celery" |
| `/hub/agents` | Liga/desliga agente, escolhe provedor/modelo | ✅ funciona (toggle no Redis; override LLM com write-through Postgres+Redis) | médio — `agents/data` devolve `llm_provider`, `atualizado_por`; provedores fixos `("gemini","deepseek","groq")` |
| `/hub/capabilities` | Catálogo de ferramentas + vínculo agente↔ferramenta | 🟡 parcial — toggle grava `agente_tools`, mas "não há consumidor de produção que dispare as capabilities" | **alto** — `capabilities/registry.py`, `agente_tools`, "manifesto (§S)", `interface` cru (`ICapability/1`) |
| `/hub/graph-nodes` | Catálogo de componentes (LLM/STT/TTS/Parser/Tool/Channel/MCP/REST) | 🟡 parcial — `list_nodes()` real; toggle grava `graph_node_config` sem efeito em runtime; `health` **sempre `null`** (nenhum `health_check` implementado nos wrappers) | **alto** — "Configuration Layer (tabela `graph_node_config`)", "Camada 1 (BaseNode)", "grafo declarativo (adendo de nós)", tipos de porta crus (`text`/`llm_response`/...) |
| `/hub/mcp-servers` | Cadastro de servidores MCP | 🟡 parcial — cadastro grava (`mcp_servers`, SSRF validado); **sem conexão real** (`mcp_lab/` segue com 3 URLs hardcoded) | **alto** — `Fase 8 "MCP Connection Manager"`, `mcp_lab/`, "sem consumidor de produção" |
| `/hub/graph-studio` | Composição visual de grafo (canvas Konva) | 🟡 parcial — salva topologia (`graph_topology`, valida DAG + tipos de porta no servidor); **sem execução** | **alto** — "Camada 3, adendo de nós declarativos", "nenhum fluxo de produção lê isso" |
| `/hub/llm-custo` | Custo/tokens/latência real + circuit breaker + câmbio | ✅ funciona (telemetria real `metricas_llm`; troca de câmbio; status de circuito real) | médio — "registry de providers + circuit breaker", "CIRCUITO (fechado)" |
| `/hub/eval` | Avaliação RAG (hit rate, MRR, faithfulness) | ❓ a confirmar | a confirmar |
| `/hub/chunkviz` | Upload + visualização de chunks; ingestão ao Redis | ✅ funciona (chunker real, `simulate_chunks_logic`; upload real) | baixo |
| `/hub/audit` | Log de ações admin | 📊 leitura | baixo |
| `/hub/users` | Cadastro/papéis/ativo de pessoas | ✅ funciona (CRUD real) | baixo |
| `/hub/chat` | Simular conversa + ver decisão de rota (SSE) | ✅ funciona (`dispatcher_langgraph.processar`) | médio — expõe `rota=...` cru nos passos SSE |
| `/hub/agents/{n}/prompt` | Editar prompt do agente + histórico | ✅ funciona (Postgres, versões, reset) | baixo |
| `/hub/_styleguide` | Referência de componentes | 📊 (não no menu) | n/a |
| `/hub/_shell` | Casca (não é página) | n/a | resolvido no Sprint 0 |

---

## B. Inventário de jargão (→ Sprint 2–4 com o glossário)

Já mapeado em `templates/hub/_glossario.html` / `static/js/core/glossario.js`.
Ocorrências a substituir:

| Arquivo | Linha(s) | Termo cru | Vira |
|---|---|---|---|
| `routes.html` | 11 | `route_registry (migration 010)` | "Mapa de rotas — vale na próxima mensagem, sem reiniciar" |
| `routes.html` | 18–22 | `owner` + `langgraph`/`langgraph_conditional`/`legacy` + `FEATURE_LANGGRAPH_NATIVE_ROUTES` + `dispatcher.py` | `InfoBanner` retrátil + `chip('owner:*')` |
| `routes.js` | 11–15, 52 | colunas `entrypoint_node`, `owner`, `planner_steps` | "Ponto de entrada", "Motor", "Passos do planejador" |
| `config.html` | 52 | "credenciais (.env — exige restart)" | "Credenciais — mudança exige reiniciar o serviço" |
| `config.html` | 55 | "GEMINI_MODEL agora é chave dinâmica" | remover (a aba Avançado explica) |
| `capabilities.html` | 8 | `capabilities/registry.py`, `agente_tools`, "§S" | `InfoBanner`: "Ferramentas que os agentes podem usar. A ligação ainda não dispara em produção." |
| `capabilities.js` | 19 | `c.interface` (`ICapability/1`) | ocultar (é versão de contrato interno) |
| `graph-nodes.html` | 11–12, 21, 29 | `graph_node_config`, "Configuration Layer", "Camada 1 (BaseNode)", "grafo declarativo (adendo de nós)" | tooltip `(?)` + `InfoBanner` |
| `graph-nodes.js` | 19, 41, 50 | "saúde não monitorada", "habilitado/desabilitado", "Desabilitar" | `StatusPill` + `Switch` no cabeçalho |
| `mcp-servers.html` | 11–17 | `Fase 8 "MCP Connection Manager"`, `mcp_lab/`, "sem consumidor de produção" | `InfoBanner` curto + `EmptyState` |
| `graph-studio.html` | 11–17 | "Camada 3, adendo de nós declarativos", "nenhum fluxo de produção lê isso" | `InfoBanner`: "Rascunho visual do fluxo. Salvar só guarda o desenho." |
| `llm_custo.html` | 27 | "registry de providers + circuit breaker" | "Provedores e disjuntor de falhas" |
| `index.html` | 33 | "circuit breaker" na descrição | "disjuntor de falhas" |

---

## C. Lacunas de backend que a UI vai precisar (Sprints 2–4)

1. **`health_check` real** nos wrappers `src/graph/nodes/*.py` — hoje todos
   retornam `None` → `graph-nodes/data` sempre manda `health: null`. (Sprint 4)
2. **Endpoint de "Testar Conexão"** para provedor LLM — não existe. (Sprint 3)
3. **Latência/last_checked de servidor MCP** — `mcp_servers` não guarda. (Sprint 4)
4. **Provedores dinâmicos** — `_PROVIDERS_VALIDOS = ("gemini","deepseek","groq")`
   hardcoded em `hub.py` (linha ~581) e `llm_provider_registry._REGISTRY`. (Sprint 3)
5. **Canais** — instância Evolution vem só de `settings.EVOLUTION_*`; sem tabela. (Sprint 3)
6. **Tools dinâmicas** — `capabilities/registry.py` só descobre `tool_*.py` por
   `pkgutil`; não há caminho de dado. (Sprint 2)
7. **Chaves de config sem consumidor** — `DEV_TEST_NO_DB_WRITE`,
   `DEV_TEST_SKIP_REGISTRATION`, `FEATURE_LANGGRAPH_NATIVE_ROUTES`,
   `FEATURE_LANGGRAPH_CELERY_DISPATCH`: decidir reconectar vs. aposentar. (Sprint 3)

---

## D. Pendências de verificação no browser (Docker)

Rodar `docker compose up -d` (perfil completo) e conferir em `localhost:9000/hub/`:

- [ ] `/hub/_styleguide` — componentes do Sprint 0 renderizam; sem erro no console; HTMX/Alpine carregam.
- [ ] Navegação nova aparece em todas as páginas; links `#provedores`/`#canais` não quebram (hoje caem em `/hub/config`).
- [ ] `/hub/eval` — carrega? Qual o estado real?
- [ ] `/hub/` (Painel) — os 4 stat cards preenchem com número real.
- [ ] `/hub/graph-nodes` — confirma que `health` vem `null` para todos.
- [ ] `/hub/agents` — toggle e troca de provedor refletem no chat logo depois.
- [ ] `/hub/chunkviz`, `/hub/chat` — fluxo completo sem erro.
