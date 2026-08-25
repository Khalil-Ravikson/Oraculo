# ADR 0001 — LangGraph fica isolado em branch/worktree própria, não é aprovado para `main`

- **Status:** ativo (em reavaliação contínua)
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

## Bloqueio atual para promoção a `main`

RBAC completo ainda não está testado corretamente em `main` — essa é uma
lacuna preexistente do fluxo atual (não é sobre o LangGraph em si), mas é o
único item que falta fechar antes de reconsiderar a promoção. Ver `notas.md`
(última entrada) para o estado mais recente.

## Consequências

- `docker-compose.yml` precisa manter `langgraph_experiment/` montado como
  volume nos serviços que montam `./src`.
- `requirements.txt` mantém `langgraph`/`langgraph-checkpoint-redis` pinados
  (histórico de bugs conhecidos no pacote de checkpointer) mesmo fora de uso
  em produção — isso significa que essas dependências vão para a imagem
  Docker de `main` também, já que o `Dockerfile` instala `requirements.txt`
  por inteiro. Ver auditoria de 2026-08-24, seção de configuração, para esse
  risco específico.
