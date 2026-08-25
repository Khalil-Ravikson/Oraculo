# `langgraph_experiment/` — grafo do dispatcher de produção

**Nome do diretório é histórico** ("experiment") — desde a Decisão 01 do
plano de integração LangGraph/REST/MCP (2026-08-25), este é o `StateGraph`
usado por `src/application/runtime/dispatcher_langgraph.py`, o
orquestrador real de produção (não mais isolado em branch própria).
Migração em andamento na branch `integration/langgraph-rest-mcp`: SIGAA/
MEDIA_DOWNLOAD/GREETING/CHECK_STATUS sendo portados pra nodes nativos
aqui, atrás de `settings.FEATURE_LANGGRAPH_NATIVE_ROUTES` (desligada até
fechar).

Reaproveita capabilities reais do projeto como "tools" dentro dos nodes do
grafo — não duplica lógica de negócio.

Detalhes técnicos completos: docstring em [`__init__.py`](__init__.py).
Decisão e histórico completo (inclusive a rejeição original que motivou o
nome do diretório): [`docs/decisions/0001-langgraph-nao-aprovado-para-main.md`](../docs/decisions/0001-langgraph-nao-aprovado-para-main.md)
(status "substituído"). Histórico de sessão: `notas.md` §7-9.
