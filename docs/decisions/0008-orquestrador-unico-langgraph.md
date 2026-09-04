# ADR 0008 — Orquestrador único de mensagem sobre o StateGraph do LangGraph

- **Status:** ativo — Fases 0-5 e B concluídas
- **Data:** 2026-09-02
- **Supera:** ADR 0001 (LangGraph aprovado como dispatcher definitivo — este
  ADR fecha a migração que o 0001 abriu). Complementa ADR 0007 (Hub v2).
- **Fonte:** plano `~/.claude/plans/voce-consegue-fazer-um-polished-salamander.md`,
  construído sobre `docs/historico/aposentadoria_dispatcher_legado.md` e
  `docs/historico/arquitetura_nos_declarativa.md`.

## Contexto

O processamento de mensagem estava espalhado por **dois orquestradores**
(`application/runtime/dispatcher.py` legado + `dispatcher_langgraph.py`) e
**três vocabulários de "nó"** (`route_registry.NODES_ENTRYPOINT`,
`src/graph/` do Hub v2, `reference_flows.py`). Isso é TD-001/TD-002/TD-013 e
causou 2 incidentes reais (nota de voz virando embedding vazio;
reclassificação dupla paga no Gemini Flash). Faltava também um caminho de
escalonamento para atendente humano.

## Decisão

1. **Um entrypoint único** em `src/application/orchestration/entrypoint.py`
   (ex-`dispatcher_langgraph.py`), sobre o `StateGraph` montado por
   `orchestration/builder.py` (ex-`langgraph_experiment/graph.py`).
   `langgraph_experiment/` deixa de existir.
2. **`src/graph/` → `src/graph_studio/`** — é biblioteca de componentes de
   infraestrutura + sandbox do Graph Studio, nunca foi o grafo de produção.
3. **Circuit-breaker por agente no entrypoint**, valendo para todas as rotas
   (antes só `dispatcher.py`, não valia para as rotas nativas do grafo).
4. **Rota `ESCALAR_HUMANO` + nó terminal `human_handoff`** — silencia o bot
   para a sessão (Redis `handoff:session:{id}`, TTL 24h), enfileira em
   `handoff:queue`, avisa `settings.SUPPORT_GROUP_JID`. Sai com `$voltar`.
5. **`dispatcher.py` e o Planner são deletados** — as 4 rotas condicionais
   (GREETING/SIGAA/MEDIA_DOWNLOAD/CHECK_STATUS) passam a `owner="langgraph"`
   (migration 023), a flag `FEATURE_LANGGRAPH_NATIVE_ROUTES` some, e a coluna
   `route_registry.planner_steps` (DAG do Planner) é removida.
6. **Topologia do grafo como dado** (`GraphSpec`, Fase 5) — finaliza a
   metade "Workflow" da Fase 2 do Plano A. Funis de ticket/CRUD ficam em
   código como sequência de nós travados (`locked=True`); o fan-out simples
   (o que `classify` decide) vira spec, editável pelo Graph Studio.
7. **Hub realinhado** — Graph Studio mostra e edita a `GraphSpec` ATIVA
   (não um desenho manual); "criar um fluxo novo" grava rota + nó + arestas
   numa transação só (route_registry + graph_spec).
8. **`classify` vira nó real** (Fase B) — antes o `entrypoint.py` chamava
   `rotear()`/circuit-breaker como Python puro ANTES do grafo, e o
   `classify_node` era passthrough. Agora a classificação e o
   circuit-breaker rodam DENTRO do grafo, no próprio `classify_node` — o
   diagrama do Hub mostra o pipeline real, não uma aproximação.

## Faseamento

| Fase | O que | Estado |
|---|---|---|
| 0 | Pacote `orchestration/` + `src/graph_studio/` + limpeza | ✅ `33dbf25` |
| 1 | Entrypoint único + circuit-breaker global + TD-013 | ✅ `a7f3d31` |
| 2 | Rota/nó `human_handoff` (ESCALAR_HUMANO) + migration 022 | ✅ `b988ab2` |
| 3 | Aposentar `dispatcher.py` + Planner + migration 023 | ✅ `c045299` |
| 5 | `GraphSpec` declarativa + migration 024 | ✅ `d4ce577` |
| 4 | Graph Studio edita a `GraphSpec` (criar fluxo novo) | ✅ `af20019` |
| B | `classify_node` real (Supervisor + circuit-breaker dentro do grafo) | ✅ |

## Consequências

- Toda mudança de roteamento passa a ter **um lugar** (`orchestration/`) —
  fim de "verifique os dois dispatchers".
- O kill-switch de `/hub/agents` passa a valer para SIGAA, RAG e todas as
  rotas — antes só para as delegadas.
- `FEATURE_LANGGRAPH_NATIVE_ROUTES` não existe mais (nem em `settings.py`, nem
  em `dynamic_config`, nem no seed de `config_dinamica`).
- O diagrama do Hub (`/hub/graph-studio`, aba "Grafo de produção") é a
  `GraphSpec` de verdade — nós/arestas que a GUI mostra são os mesmos que o
  `builder.build_graph()` compila em produção, sem tradução.

### Dois bugs achados escrevendo os testes de regressão da Fase B

A migração do circuit-breaker/classificação pra dentro de `classify_node`
expôs dois bugs que ficavam mascarados pela forma como o código antigo
mexia no payload — nenhum dos dois era visível olhando só o "novo" código
isolado, só apareceu com testes de regressão fim-a-fim:

1. **Namespace de `route_value` vs. id de nó do grafo.** A rede de
   segurança ("`entrypoint_node` inválido cai em `rag`") comparava
   `rr.entrypoint_node` (ex.: `"ticket"`) contra os IDS DE NÓ do grafo
   compilado (ex.: `"ticket_ask_tipo"`) — namespaces diferentes, então
   "ticket"/"crud" nunca validavam. Existia desde a Fase 5 mas nenhum teste
   populava a checagem pra uma rota de funil (os testes de ticket/HITL
   setam `dlg._graph` direto, contornando o único ponto — `_get_graph()` —
   que calculava o conjunto antigo). Corrigido: `builder.route_values_ativos()`
   deriva o conjunto certo dos `route_value` da spec, não dos ids de nó.
2. **`route` stale sobrevivendo no checkpoint.** `_payload_mensagem_nova`
   não resetava `route` (a decisão "fica pro `classify_node`"), mas como o
   `thread_id` é fixo por sessão, o `route` do ÚLTIMO funil concluído
   continuava no checkpoint — e o atalho de REPL de `classify_node`
   (`if state.route: return {}`) tomava esse valor stale como "já
   classificado", pulando a classificação de verdade numa mensagem nova (2º
   funil na mesma sessão herdava as perguntas do 1º). Corrigido:
   `_payload_mensagem_nova` volta a resetar `route=""` explicitamente.

Achado por `test_dispatcher_nao_vaza_estado_entre_crud_e_ticket`
(`tests/unit/application/test_langgraph_crud_hitl.py`) — o mesmo tipo de
teste que já tinha pego o bug original do `cancelado` vazando (ver ATENÇÃO 2
em `entrypoint.py`).

## Rollout

O plano original previa uma **janela de validação em WhatsApp real** com a
flag ligada antes de deletar o legado. O dono optou por concluir todas as
fases sem essa janela — a validação em produção fica como checklist pós-merge
(`aposentadoria_dispatcher_legado.md`), não como bloqueio de código.

## Dívidas fechadas

- **TD-001 / TD-002** (Fase 3) — um só orquestrador, um só vocabulário de nó.
- **TD-013** (Fase 1) — override `IGNORE→LLM` do gatekeeper deixa de ser
  incondicional.
- **TD-017** (Fase 3) — teste de auth SIGAA reescrito para o `auth_token`.
- **TD-018** (Fase 3) — testes de cadastro forçam `DEV_TEST_NO_DB_WRITE=False`.
- **TD-012 / TD-014** (Fase 3) — 5 arquivos de teste órfãos deletados.
