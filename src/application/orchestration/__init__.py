"""
src/application/orchestration/
==============================
Orquestrador único de mensagem do Oráculo (camada application).

Sucessor de `langgraph_experiment/` (protótipo) e dos dois dispatchers legados
(deletados na Fase 3 do ADR 0008). Constrói um `StateGraph` do LangGraph a
partir dos nós em `nodes.py` (`builder.py`), executa via
`entrypoint.processar()`, e é a única fonte de verdade de "o que acontece com
uma mensagem".

Ver ADR 0008 e `docs/historico/aposentadoria_dispatcher_legado.md`.
"""
