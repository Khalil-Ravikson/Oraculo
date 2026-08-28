# Aposentadoria do `dispatcher.py` legado — avaliação

> **Status: avaliação (não é decisão fechada).** Produzida em 2026-08-27 como
> parte do escopo da Fase 2 do Plano A
> (`docs/historico/plataforma_orientada_a_configuracao.md` §L / TD-001).
> A Fase 2 executou os **pré-requisitos de código** (abaixo); a retirada em si
> depende de uma janela de validação em produção que ainda não aconteceu.

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

## O que falta para efetivamente aposentar

| # | Bloqueio | Tipo | Quem resolve |
|---|---|---|---|
| 1 | `FEATURE_LANGGRAPH_NATIVE_ROUTES=true` nunca validado via WhatsApp real (`notas.md §16.9`). Enquanto `false`, `dispatcher.py` é caminho de produção ativo para SIGAA/MEDIA_DOWNLOAD/GREETING/CHECK_STATUS. | **Rollout** (janela de teste com WhatsApp de produção) | Operação |
| 2 | Os nodes nativos (`langgraph_experiment/nodes.py`: `sigaa_node`, `media_download_node`, `greeting_node`, `check_status_node`) **não** rodam circuit-breaker por agente nem semantic cache — `dispatcher.py` roda. Migrar SIGAA sem isso é regressão (o kill-switch de `/hub/agents` deixa de valer para SIGAA). | Código | — |
| 3 | Fluxo do Planner completo (`criar_plano` → `_despachar_workers` → chord) só existe em `dispatcher.py`. Rotas não-RAG que hoje passariam pelo Planner via delegação não têm equivalente no grafo. | Código / avaliação | — |
| 4 | `chain_sse.py` — órfão (`sse_router` não registrado). | Limpeza | — |
| 5 | `_aguardar_resposta_final` (usado por `eval_api.py`) só é dispensável se `FEATURE_LANGGRAPH_CELERY_DISPATCH` ficar garantidamente ligada (hoje `true` no `.env`, `False` no default do código). | Config | — |

## Recomendação

- **Aposentadoria é factível**, mas **não agora** — o gargalo real é o item 1
  (rollout, não código).
- **Sequência sugerida** quando houver janela:
  1. Fechar itens 2 e 4 (código): replicar circuit-breaker + semantic cache nos
     4 nodes nativos; deletar `chain_sse.py`.
  2. Ligar `FEATURE_LANGGRAPH_NATIVE_ROUTES` via `/hub/config` (config dinâmica,
     sem restart) e validar SIGAA/MEDIA/GREETING/CHECK_STATUS por WhatsApp real.
  3. Com a flag estável em produção por N dias: `route_registry` das 4 rotas
     passa de `langgraph_conditional` para `langgraph`; `dispatcher.py` deixa de
     ser chamado.
  4. Avaliar o item 3 (Planner) — se nenhuma rota o usa, remover
     `dispatcher.py`, `_despachar_workers`, `_aguardar_resposta_final` e o
     re-export de `OSResult`/`audio_intake`.
- Até lá, `dispatcher.py` fica — agora sem contrato nem helpers próprios,
  só a "cola" de orquestração + os fast-paths delegados.
