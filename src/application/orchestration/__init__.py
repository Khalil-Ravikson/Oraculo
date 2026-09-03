"""
src/application/orchestration/
==============================
Orquestrador único de mensagem do Oráculo (camada application).

Sucessor de `langgraph_experiment/` (protótipo) e dos dois dispatchers
(`application/runtime/dispatcher.py` + `dispatcher_langgraph.py`). Constrói um
`StateGraph` do LangGraph a partir de nós registrados (`registry.py` +
`nodes/`), executa via `entrypoint.processar()`, e é a única fonte de verdade
de "o que acontece com uma mensagem".

Ver ADR 0008 e `docs/historico/aposentadoria_dispatcher_legado.md`.
"""
