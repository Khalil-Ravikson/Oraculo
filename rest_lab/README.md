# `rest_lab/` — 🧪 PESQUISA / EXPERIMENTO

**Não é parte da arquitetura de produção do Oráculo.**

Laboratório de estudo de consumo de API REST (branch/worktree
`research/rest-mcp-estudos`) — prova de capacidade técnica, sem integração
com o núcleo além de um único ponto de entrada gated por prefixo de comando
(`rest `) em `src/application/runtime/dispatcher_langgraph.py`.

Detalhes técnicos completos (APIs usadas, escopo, decisão de roteamento por
regex): docstring em [`__init__.py`](__init__.py). Decisão registrada:
[`docs/decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md`](../docs/decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md).
Histórico de sessão: `notas.md` §10.
