# `langgraph_experiment/` — 🧪 PESQUISA / EXPERIMENTO

**Não é parte da arquitetura de produção do Oráculo.** Isolado em
branch/worktree própria, não aprovado para `main`.

Responde a uma pergunta concreta: o runtime `StateGraph` do LangGraph
consegue orquestrar RAG + funil de ticket (HITL multi-turn) tão bem quanto
o pipeline atual? Reaproveita capabilities reais do projeto como "tools"
dentro dos nodes do grafo — não duplica lógica de negócio.

Detalhes técnicos completos: docstring em [`__init__.py`](__init__.py).
Decisão e estado atual: [`docs/decisions/0001-langgraph-nao-aprovado-para-main.md`](../docs/decisions/0001-langgraph-nao-aprovado-para-main.md).
Histórico de sessão: `notas.md` §7-9.
