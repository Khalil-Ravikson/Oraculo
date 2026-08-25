# `rest_lab/` — 🧪 PESQUISA / EXPERIMENTO

**Não é parte da arquitetura de produção do Oráculo.** Continua assim
mesmo após a Fase 3 do plano de integração LangGraph/REST/MCP (Decisão
03/ADR 0005) — a mudança foi só de camada (chamadas HTTP agora passam por
`src/application/use_cases/rest_lab_use_case.py::RestLabUseCase` em vez de
`httpx` direto), não de propósito.

Laboratório de estudo de consumo de API REST — prova de capacidade
técnica, sem integração com o núcleo além de um único ponto de entrada
gated por prefixo de comando (`rest `) em
`src/application/runtime/dispatcher_langgraph.py`.

Detalhes técnicos completos (APIs usadas, escopo, decisão de roteamento por
regex): docstring em [`__init__.py`](__init__.py). Decisões registradas:
[`docs/decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md`](../docs/decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md),
[`docs/decisions/0005-rest-lab-camada-application.md`](../docs/decisions/0005-rest-lab-camada-application.md).
Histórico de sessão: `notas.md` §10.
