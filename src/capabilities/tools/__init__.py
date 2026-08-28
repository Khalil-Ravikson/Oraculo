"""
src/capabilities/tools/
==========================
Sprint 2 (Fase 2) — cada capability em seu próprio arquivo `tool_*.py`,
espelhando a convenção `worker_*.py` de `application/workers/`.
Autodescoberto por `capabilities/registry.py::_autodiscover_tools()` via `pkgutil`.

Plano A / Fase 5: os imports quebrados foram corrigidos
(`infrastructure/database/session` + `PessoaRepository`), cada `@tool(...)`
ganhou um manifesto (§S: descrição, permissões, `confirmacao`), e o vínculo
agente↔capability virou a tabela `agente_tools` (migration 012, ver
`capabilities/agent_tools.py`). As 3 capabilities agora registram de fato.

Ainda sem consumidor de produção que as dispare — conectar o fluxo CRUD
(como o agente confirma e executa) segue sendo trabalho de produto. A
infra estrutural (registro + manifesto + binding editável) está pronta.
"""
