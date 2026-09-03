# ADR 0008 — Orquestrador único de mensagem sobre o StateGraph do LangGraph

- **Status:** ativo — Fases 0-3 concluídas; 4-5 em andamento
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
   código (subgrafos); só o fan-out simples vira spec.
7. **Hub realinhado** para mostrar um só grafo — o de produção real, gerado
   de `orchestration.builder.describe()`.

## Faseamento

| Fase | O que | Estado |
|---|---|---|
| 0 | Pacote `orchestration/` + `src/graph_studio/` + limpeza | ✅ `33dbf25` |
| 1 | Entrypoint único + circuit-breaker global + TD-013 | ✅ `a7f3d31` |
| 2 | Rota/nó `human_handoff` (ESCALAR_HUMANO) + migration 022 | ✅ `b988ab2` |
| 3 | Aposentar `dispatcher.py` + Planner + migration 023 | ✅ |
| 4 | Realinhar o Hub | parcial (`c67a571`/`be77173`/`fdadc99`) |
| 5 | `GraphSpec` declarativa | pendente |

## Consequências

- Toda mudança de roteamento passa a ter **um lugar** (`orchestration/`) —
  fim de "verifique os dois dispatchers".
- O kill-switch de `/hub/agents` passa a valer para SIGAA, RAG e todas as
  rotas — antes só para as delegadas.
- `FEATURE_LANGGRAPH_NATIVE_ROUTES` não existe mais (nem em `settings.py`, nem
  em `dynamic_config`, nem no seed de `config_dinamica`).

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
