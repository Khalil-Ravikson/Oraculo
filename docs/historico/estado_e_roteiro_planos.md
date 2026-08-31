# Oráculo — Estado dos planos e roteiro do que falta

> Consolidação do que já foi implementado e do que resta, nos dois planos em
> curso + o adendo de arquitetura de nós. Ponto único de verdade para retomar
> o trabalho. Atualizado em 2026-08-28.
>
> **Superado em parte (2026-08-31):** o redesign do Hub (Hub v2) retomou e
> avançou muito além do que este doc descreve — HTMX+Alpine, camada de
> tradução, registries dinâmicos (tools/provedores/canais), painéis de infra,
> Camada 1 de nós virou consumidor real, GraphExecutor MVP. Plano vivo:
> `C:\Users\User\.claude\plans\silly-percolating-ritchie.md`; arquitetura em
> `docs/architecture/arquitetura_oraculo.md` §12; decisão em
> `docs/decisions/0007-hub-v2-htmx-alpine-e-registries-dinamicos.md`.
> Migrations 016–020. Este doc abaixo reflete o estado de 2026-08-28.

---

## Resumo em uma linha

**Plano B (frontend) — concluído** (e depois retomado como Hub v2, ver nota
acima). **Plano A (plataforma orientada a config) — Fases 1–5 concluídas;
6–8 retomadas parcialmente no Hub v2 (nós, MCP, canais); 9–11 condicionais.**

---

## Plano A — `plataforma_orientada_a_configuracao.md`

| Fase | O quê | Estado | Onde ficou o código |
|---|---|---|---|
| **1** | Config dinâmica (version column + read-repair Redis↔Postgres) | ✅ concluída, testada, rodando | `src/infrastructure/dynamic_config.py`, `dynamic_config_repository.py`, migrations 009 + 011 |
| **2** | Route Registry (execução por rota no chokepoint) | ✅ concluída, testada, rodando | `src/infrastructure/route_registry.py`, `route_registry_repository.py`, migration 010, `/hub/routes` |
| **2 — avaliação** | Aposentadoria do `dispatcher.py` legado | ✅ avaliada — **bloqueada** por pré-requisito de produção | `docs/historico/aposentadoria_dispatcher_legado.md` |
| **3** | Provider Registry LLM + circuit breaker | ✅ concluída, testada | `src/infrastructure/adapters/llm_provider_registry.py`, `llm_circuit_breaker.py` |
| **4** | Parser por config (prioridade/enable sem restart) | ✅ concluída, testada | `parser_factory.py::auto()`, migration 011 |
| **5** | Tool Registry + manifesto de capability + junction `agente_tools` | ✅ concluída, testada | `src/capabilities/registry.py`, `agent_tools.py`, migration 012 |
| **6** | STT/TTS/Embeddings — mesmo tratamento do LLM | ⏸ **não iniciada** — "sem demanda concreta" (§J do plano). Só sob pedido explícito. | — |
| **7** | Channel abstraction | ⏸ **não iniciada** — "alto esforço, adiado" | — |
| **8** | MCP Connection Manager | ⏸ **não iniciada** — "alto risco SSRF, adiado" | — |
| **9** | Tenancy real (`tenant_id` deixa de ser sempre nulo) | ⏸ **condicional** a um 2º cliente real. Não agendada. | colunas `tenant_id` já existem, nuláveis |
| **10** | Secrets Manager / BYOK | ⏸ **condicional** a exigência de compliance ou 1º cliente enterprise | — |
| **11** | Config-as-Code / export-import GitOps | ⏸ **condicional** a um cliente pedir change-management via PR | — |

### Pré-requisito que destrava a Fase 2 (aposentadoria do dispatcher legado)

Validar `FEATURE_LANGGRAPH_NATIVE_ROUTES=true` em produção e replicar
circuit-breaker/cache nos nodes nativos. Enquanto não feito, `dispatcher.py`
continua vivo para as rotas com `owner=legacy`/`langgraph_conditional`.
Detalhe em `aposentadoria_dispatcher_legado.md`.

---

## Plano B — `plano_frontend_ui_ux.md`

| Fase | O quê | Estado |
|---|---|---|
| **0** | tokens/base/layout/components + core JS, isolados em `/hub/_styleguide` | ✅ |
| **1** | `_shell.html` (sidebar+topbar) + `index`/`login` migradas | ✅ |
| **2** | `config`/`agents`/`users` migradas | ✅ |
| **3** | `audit`/`llm_custo`/`chat`/`chunkviz`/`eval`/`capabilities`/`agent_prompt`/`routes` migradas | ✅ |
| **4** | remoção do legado: `_base.html`, `hub-bridge.css`, `hub.css`, `hub_index.css`, templates órfãos (`admin/*`, `monitor/*`, `hub/dashboard.html`), `src/api/monitor.py`, JS órfãos | ✅ |
| **5** | auditoria final: zero `<style>`/`<script>` inline, zero `on*=`, zero emoji, foco de teclado (`form.css`), CSS escopado por página, tokens-only | ✅ |

**As 14 rotas HTML do Hub estendem `_shell.html` diretamente.** Sistema de
design único: `tokens.css` + `base.css` + `layout.css` + `components/*` +
`pages/<nome>.css` (escopado). Chart.js vendorado em `static/js/vendor/`.

### Pendência não-bloqueante

- **Sign-off visual no browser** — abrir `localhost:9000/hub/` e revisar as 14
  páginas. Especialmente `chunkviz` (maior refatoração de JS) e `chat` (design
  novo das bolhas + painel de instrumentos).
- **Extensão futura §I** (component framework leve / React no fim) — fora deste
  plano, condicionada.

---

## Adendo — `arquitetura_nos_declarativa.md`

Grafo LangGraph montado a partir de dados (JSON/YAML), nós descobertos
automaticamente com metadados — estilo n8n/Langflow/LangGraph Studio.
Enquadrado como **a metade "Workflow" da Fase 2** (que só teve a metade
"Route" feita).

**Estado: proposta escrita, nada codificado.** Recomendação de adoção
escopada em 3 camadas:

| Camada | O quê | Vale? |
|---|---|---|
| 1 | `BaseNode` + `NodeRegistry` (autodiscovery `pkgutil`, igual `capabilities/registry.py`) | **Sim — maior valor, menor risco, não toca produção.** É o próximo passo lógico se for adotar. |
| 2 | Spec declarativa só para o fan-out simples (`classify → {rag, greeting, sigaa…}`) | Condicional — depende da Camada 1 provar valor |
| 3 | Funis ticket/CRUD ficam em código (subgrafo registrado como nó composto) | Sim — não declarativizar o que é frágil (`interrupt()`, bug do checkpointer Redis) |

**Decisão pendente do dono:** iniciar a Camada 1 ou não. Sem isso, este
adendo fica só como registro.

---

## O que fazer a seguir (ordem sugerida)

1. **Sign-off visual do Plano B** no browser (bloqueia "dado como pronto").
2. **Decidir sobre Camada 1 (BaseNode + NodeRegistry)** — ver `decision_camada1_nodes.md`
   para contexto. Recomendação: **SIM, iniciar junto com Fase 6**. Desbloqueia
   Fases 6–8 com alicerce sólido.
3. **Roadmap completo das Fases 6–11** — ver `fases_6_11_langgraph_studio.md`
   (novo documento). Detalha implementação, testes, Hub como "Graph Studio"
   visual inspirado em LangGraph Studio.
4. **Destravar a aposentadoria do `dispatcher.py`** quando
   `FEATURE_LANGGRAPH_NATIVE_ROUTES` puder ser validada em produção.

Nada aqui está com trabalho em andamento — todos os pontos acima são
inícios de fase, não continuações.

---

## Documentos relacionados (2026-08-28)

- **`fases_6_11_langgraph_studio.md`** — Roadmap completo das próximas fases (6–11),
  com inspiração visual/arquitetural em LangGraph Studio. Sequência, dependências,
  gatilhos de decisão, impacto de código.
- **`decision_camada1_nodes.md`** — Proposta de decisão: iniciar Camada 1
  (BaseNode + NodeRegistry) agora? Contexto, benefícios, riscos, recomendação.
