"""
langgraph_experiment/
=======================
Experimento ISOLADO (branch `langgraph`, worktree separada) para responder
uma pergunta concreta: "LangGraph consegue orquestrar RAG + funil de ticket
(HITL multi-turn) tão bem quanto o pipeline atual (SDK google-genai direto +
state machine custom em Redis)?"

Não substitui nada em produção. Reaproveita as capabilities reais do projeto
(RAGSearchService, SynthesisService, dev_dump) como "tools" dentro dos nodes
do grafo — não duplica lógica de negócio, só troca o orquestrador por um
StateGraph do LangGraph.

Ver `.claude.md` (raiz do projeto original) — regra "Sem LangGraph" documenta
que uma tentativa anterior travou em `state`/`builder`. Este experimento
existe para checar se isso ainda acontece com a versão atual do LangGraph,
antes de descartar de vez ou reabrir a discussão.
"""
