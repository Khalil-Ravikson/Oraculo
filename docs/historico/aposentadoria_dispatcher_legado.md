# Aposentadoria do `dispatcher.py` legado — CONCLUÍDA

> **Status: FEITO (ADR 0008 Fase 3).** `dispatcher.py`, o Planner e os workers
> `worker_greeting`/`worker_action` foram deletados. As 4 rotas condicionais
> passaram a `owner="langgraph"` (migration 023), a flag
> `FEATURE_LANGGRAPH_NATIVE_ROUTES` foi removida e a coluna
> `route_registry.planner_steps` foi dropada. O dono optou por não fazer a
> janela de validação em WhatsApp real antes de deletar — a validação em
> produção (§ "Checklist pós-merge" abaixo) fica como acompanhamento, não
> bloqueio. Este documento vira histórico.

## Contexto

`src/application/runtime/dispatcher.py` (legado, ex-`cognitive_os.py`) e
`src/application/runtime/dispatcher_langgraph.py` (produção, ADR 0001
"substituído" / Decisão 01 do plano de integração) coexistem. TD-001.

`dispatcher_langgraph.py` é o entry point real (`process_message_task.py`
importa `processar` dele incondicionalmente). Ele **delega** para
`dispatcher.py::processar` toda rota cujo `route_registry.owner` seja
`legacy` ou `langgraph_conditional` com `FEATURE_LANGGRAPH_NATIVE_ROUTES`
desligada — hoje: `SIGAA`, `MEDIA_DOWNLOAD`, `GREETING`, `CHECK_STATUS`
(a flag está `false` em produção).

## O que a Fase 2 fez (pré-requisitos — Parte C)

1. **`OSResult` extraído** para `src/application/runtime/contracts.py`.
   `agents/sigaa/auth_flow.py`, `agents/tickets/ticket_flow.py`,
   `agents/tickets/crud_tool.py`, `dispatcher_langgraph.py` deixam de importar
   o contrato do dispatcher legado. `dispatcher.py` re-exporta para
   compatibilidade.
2. **Ingestão de áudio extraída** para `src/application/runtime/audio_intake.py`
   (`_transcrever_audio_recebido`, `_quer_resposta_em_audio`,
   `_remover_pedido_audio` + constantes). `dispatcher_langgraph.py` e
   `process_message_task.py` deixam de importar do dispatcher legado.
3. **Callers de debug migrados** para `dispatcher_langgraph.processar`:
   - `src/api/routers/web/hub.py` (`POST /eval/query`) — lê só `.answer`/`.rota`, drop-in.
   - `src/api/routers/admin/eval_api.py` (`_evaluate_single`) — mantém o
     fallback `_aguardar_resposta_final` (que continua em `dispatcher.py`,
     ainda importável); a eval passa a rodar pelo caminho de produção.
   - `src/api/chain_sse.py` — import trocado por consistência, **mas o
     `sse_router` não está registrado em `main.py`** (código órfão — ver
     recomendação abaixo).

Depois disso, os únicos consumidores de `dispatcher.py` são:
`dispatcher_langgraph.py` (delegação intencional) e `process_message_task.py`
(nada — só via `dispatcher_langgraph`).

## Bloqueios — todos fechados

| # | Bloqueio | Estado |
|---|---|---|
| 1 | `FEATURE_LANGGRAPH_NATIVE_ROUTES=true` nunca validado via WhatsApp real. | Fechado por decisão do dono — validação vira checklist pós-merge, não bloqueio. |
| 2 | Nodes nativos não rodavam circuit-breaker por agente nem semantic cache. | ✅ Fase 1 — circuit-breaker no `entrypoint.py`, pra todas as rotas. As 4 rotas são `cacheavel=False`, não havia cache pra replicar. |
| 3 | Fluxo do Planner só existia em `dispatcher.py`. | ✅ Fase 3 — cenário A replicado por `_responder_rag_via_celery`; cenário B coberto por `sigaa_node`/`media_download_node`. Planner deletado. |
| 4 | `chain_sse.py` órfão. | ✅ Fase 0 — deletado. |
| 5 | `_aguardar_resposta_final` (usado por `eval_api.py`). | ✅ Fase 3 — `eval_api.py` lê `.answer` direto. |

## O que a Fase 3 fez

- Deletou `src/application/runtime/dispatcher.py`, `application/chain/planner.py`,
  `agents/academic_knowledge/planning.py` (`criar_plano`/`_planejar_com_pro`/
  `_plano_*`/`_despachar_workers`/`_aguardar_resposta_final`),
  `worker_greeting`, `worker_action` e `tests/unit/application/test_dispatcher*.py`.
- `route_registry`: as 4 rotas → `owner="langgraph"`, `OWNERS_VALIDOS = {"langgraph"}`,
  `RouteConfig.delega_para_legado()` removido, coluna `planner_steps` dropada
  (migration 023).
- `FEATURE_LANGGRAPH_NATIVE_ROUTES` removida de `settings.py`,
  `dynamic_config.py` e do seed de `config_dinamica` (migration 023).
- `eval_api.py::_evaluate_single` lê `.answer` direto (sem o polling de Stream).
- Cenário B do Planner (`_plano_sigaa`/`_plano_media`): `sigaa_node` e
  `media_download_node` montam a própria chain — sem chamador do Planner.

## Checklist pós-merge (validação em produção, não bloqueio)

Rodar por WhatsApp real depois do deploy: GREETING, CHECK_STATUS, SIGAA
(login CPF/senha completo), MEDIA_DOWNLOAD (link + busca por termo),
ESCALAR_HUMANO ("quero falar com um atendente"). Regressão aqui é rollback
do deploy, não do código.
