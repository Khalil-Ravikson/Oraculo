"""
src/capabilities/tools/
==========================
Sprint 2 (Fase 2) — cada tool em seu próprio arquivo `tool_*.py`, espelhando
a convenção `worker_*.py` de `application/workers/`. Autodescoberto por
`capabilities/registry.py::_autodiscover_tools()` via `pkgutil`.

DÉBITO TÉCNICO conhecido (não corrigido nesta fase, só documentado):
as 3 tools aqui (`update_student_email`, `update_student_telefone`,
`get_student_info`) importam
`src.infrastructure.repositories.postgres_user_repository.PostgresUserRepository`,
módulo que NÃO EXISTE (o repositório vivo é `PessoaRepository` em
`src/infrastructure/repositories/pessoa_repository.py`). O import é feito
dentro da função, então não quebra a descoberta/registro — só explode com
`ModuleNotFoundError` se alguma dessas tools for de fato chamada. Nenhuma
tem consumidor vivo em produção hoje (ver achado da Fase 6 do
PLANO_REFATORACAO_SUPERVISOR.md em `capabilities/registry.py`), então o
risco prático é zero. Corrigir o import é trabalho de produto (decidir a
implementação real de CRUD confirmado via HITL), fora do escopo desta sprint.
"""
