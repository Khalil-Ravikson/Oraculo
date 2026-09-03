# ADR 0001 — LangGraph fica isolado em branch/worktree própria, não é aprovado para `main`

- **Status:** substituído por **ADR 0008** (2026-09-03). LangGraph é o
  orquestrador único de produção (`src/application/orchestration/`); o
  `dispatcher.py` legado e `langgraph_experiment/` não existem mais. Este ADR
  fica só como registro histórico da rejeição original.
- **Data da decisão original:** rejeição inicial anterior a 2026-07-27; reavaliação em 2026-07-31
- **Fonte:** extraído de `.claude.md` (versão anterior a 2026-08-25) e `notas.md` §7-9

## Contexto

Uma primeira tentativa de adotar LangGraph travou em bugs de `state`/`builder`
e foi rejeitada. Numa branch/worktree separada (`langgraph`), o experimento foi
retestado a fundo, principalmente via WhatsApp real.

## Decisão

Manter o experimento isolado em `langgraph_experiment/` (branch/worktree
própria) até que os pontos "não testado ainda" sejam fechados. Só depois
disso reabrir a conversa sobre migrar `router/`, `application/runtime/dispatcher.py`
ou `agents/sigaa/auth_flow.py` para o runtime `StateGraph`.

## O que já foi validado (não descartar em retestes futuros)

- Rotas de fluxo geral (RAG, CRUD, ticket) funcionam.
- Comando de saída explícito do HITL (match exato de "sair"/"cancelar"/
  "desistir"/"abortar"/"encerrar"/"parar") funciona — ver `_eh_saida()` em
  `langgraph_experiment/nodes.py`.
- RBAC portado do fluxo real (`agents/tickets/rbac.py::checar_permissao_chamado`).
- Dedup de webhook por `msg_key_id` — necessário porque a Evolution API
  reentrega o mesmo evento com frequência alta.
- Dois testes direcionados (múltiplos `interrupt()` no mesmo node; carga
  concorrente com 5 workers paralelos) não reproduziram os bugs catastróficos
  dos issues `langchain-ai/langgraph#5074` / `redis-developer/langgraph-redis#133`
  — o bloqueio técnico original parece resolvido no upstream. O lock por
  telefone (`lock:msg:{phone}`) continua necessário mesmo assim — evita um
  "last-write-wins silencioso" observado em teste de carga sem lock.

## Bloqueio atual para promoção a `main` (fechado em 2026-08-25)

RBAC completo não estava testado — lacuna preexistente do fluxo atual (não
era sobre o LangGraph em si), zero testes existiam pra `domain/permissions.py`
nem pra `agents/tickets/rbac.py::checar_permissao_chamado` antes desta
sessão. Fechado como parte da Fase 2 do plano de integração:
`tests/unit/domain/test_permissions.py` (matriz completa role × status,
`pode()`, `mensagem_sem_permissao()`, `lista_tools_permitidas()`) e
`tests/unit/agents/tickets/test_rbac.py` (pessoa autorizada/bloqueada por
status/role/flag administrativa, fallback de `DEV_TEST_SKIP_REGISTRATION`).

Achado ao escrever a suíte, registrado mas **não alterado** (é decisão de
regra de negócio, não bug de código óbvio): `_STATUS_BLOQUEADOS` em
`domain/permissions.py` só contém `inativo`/`pendente` — `trancado`
(matrícula trancada) mantém o mesmo acesso de um usuário `ativo`. Pode ser
intencional; vale confirmar com quem decide as regras de negócio do
Oráculo antes de mexer.

## Consequências (superadas pelo ADR 0008)

O que este ADR previa (`langgraph_experiment/` isolado, montado como volume,
dependências "fora de uso em produção") não vale mais: LangGraph é o runtime
de produção. Ver ADR 0008.
