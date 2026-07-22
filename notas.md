# notas.md

> Rascunho de anotações rápidas sobre problemas encontrados/corrigidos durante
> a rodada de testes de ponta-a-ponta de tickets/CRUD/cadastro via WhatsApp
> (2026-07-21). Não é documentação de arquitetura definitiva — ver
> `arquitetura_oraculo.md` e `notas_regras_negocio_chunkviz.md` para isso.

---

## 1. "Três cérebros" de roteamento brigando entre si (corrigido parcialmente)

### O bug observado

Mensagens em linguagem natural como "quero abrir um ticket" ou "quero
atualizar meu setor" não chegavam nos novos fluxos `TICKET_ABERTURA`/`CRUD`
— caíam no Planner genérico, que falhava com:

```
❌ [DISPATCHER] Falha ao localizar worker crud_confirm no registry
```

### Causa raiz (cadeia completa)

Para texto livre (sem `!`/`@`/`$`), `application/runtime/dispatcher.py`
consulta **dois classificadores independentes**:

1. `router/llm_fallback.py::orchestrate()` — decide uma ação de alto nível
   (`reply_direct`, `call_rag`, `call_sigaa`, `check_status`, `call_media`).
   Não conhecia `TICKET_ABERTURA`/`CRUD` (corrigido — ver abaixo).
2. `router/supervisor.py::rotear()` — 5 camadas próprias (regex → heurística
   → regex seeded → KNN → Flash), que sim tinha o regex/Flash pra
   `TICKET_ABERTURA`.

O `dispatcher.py` roda o Orquestrador **primeiro** e depois **sempre**
sobrescreve `decision.rota` (o que o Supervisor decidiu) pelo resultado do
Orquestrador, para qualquer mensagem que não seja comando — mesmo quando o
Supervisor tinha acertado.

**Bug adicional, mais sutil:** o override trocava só `decision.rota`, e
**deixava `decision.dag_hint` com o valor antigo** (calculado pra rota
original do Supervisor). O Planner (Gemini Pro, `agents/academic_knowledge/planning.py`)
recebia `"Rota detectada: GERAL"` mas `"Dica do router: {'steps': ['ticket_abertura']}"`
— informação contraditória. Diante disso, o modelo "resolvia" sozinho
escolhendo o worker mais parecido da sua própria whitelist (`VALID_WORKERS`),
que incluía `crud_confirm` — **um worker que nunca foi implementado de
verdade** (achado já documentado antes de mim em `agents/tickets/service.py`
e `capabilities/registry.py`, mas nunca removido da whitelist).

### O que foi corrigido

- `router/llm_fallback.py`: `orchestrate()` agora conhece `call_ticket` e
  `call_crud_update` como ações válidas, com descrição no prompt distinguindo
  as duas (ticket = problema/pedido novo; CRUD = corrigir dado já existente).
- `application/runtime/dispatcher.py`: mapeia essas duas ações pras rotas
  `TICKET_ABERTURA`/`CRUD`, **e recalcula `decision.dag_hint` junto com
  `decision.rota`** no override — rota e hint nunca mais ficam dessincronizados.
- `router/contracts.py` (`VALID_WORKERS`) e `agents/academic_knowledge/planning.py`:
  removido `crud_confirm` da whitelist e do prompt do Planner — não existe,
  nunca existiu implementado. Fallback de segurança: se `CRUD`/`TICKET_ABERTURA`
  chegar no Planner por algum caminho que não seja o `dispatcher.py` (não
  deveria acontecer — ele intercepta as duas rotas antes do Planner), agora
  cai num plano `greeting` inofensivo em vez de referenciar um worker fantasma.

### Variante do bug encontrada depois (2026-07-21, fechando a rodada) — ainda não corrigida

Ticket funcionou via texto livre ("Iniciar cadastro"), CRUD não (mensagem
"Crud"). Causa: quando `orchestrate()` **falha** (exceção/JSON inválido —
acontece com frequência alta, ver logs cheios de `❌ [ORCHESTRATOR] JSON
Inválido: 'Here is'`), o except handler retorna um fallback HARDCODED
(`action="call_rag", route_hint="GERAL"`). Esse fallback é tratado pelo
`dispatcher.py` como se fosse uma decisão real do Orquestrador — e por isso
**sempre sobrescreve** a classificação do Supervisor, mesmo quando o
Supervisor acertou (`rota=CRUD conf=1.00` no log, apagado por baixo do
"GERAL" de emergência). Resultado: cai no Planner genérico, que também
falha, e a mensagem nunca chega no `crud_tool.py`/`ticket_flow.py`.

Distinção que falta no código: "Orquestrador decidiu X" (deve poder
sobrescrever o Supervisor) vs. "Orquestrador falhou e isto é só um valor de
emergência" (NÃO deveria sobrescrever nada — deveria deixar o Supervisor
decidir sozinho). Hoje os dois casos são indistinguíveis pro `dispatcher.py`
porque o fallback usa o mesmo formato de uma decisão válida. Fix sugerido
(não aplicado ainda, propositalmente — é candidato natural pro plano de
unificação dos classificadores da próxima conversa, não outro remendo
pontual): os except handlers de `orchestrate()` deveriam sinalizar falha de
forma distinguível (ex: `action="orchestrator_failed"`), e o `dispatcher.py`
tratar esse caso como `decision_rota = None` (não sobrescreve nada) em vez
de forçar "GERAL".

### O que NÃO foi feito (decisão consciente, não é dívida esquecida)

Não fundi os dois classificadores (Orquestrador + Supervisor) num só. Isso
seria uma limpeza arquitetural válida — hoje são 2-3 chamadas Gemini por
mensagem decidindo intenção sem se coordenar, e esse tipo de bug de
precedência pode se repetir de outras formas — mas é uma decisão de
arquitetura maior, fora do escopo de "consertar o bug desta rodada". Fica
registrado aqui como candidato a discussão futura, não como algo pra
resolver sem avisar.

---

## 2. Bug do RegistrationFunnel: botões iam pro JID errado (corrigido)

### O bug observado

Depois de "Nome" + "Curso" preenchidos, o funil de cadastro tentava mandar os
botões de confirmação e sempre falhava:

```
❌ Evolution sendButtons → HTTP 400 | Resp: {"jid":"175174737518829@s.whatsapp.net","exists":false}
```

Como a exceção era engolida silenciosamente **dentro** de
`capabilities/messaging/evolution_tool.py::enviar_botoes_confirmacao()` (log
de erro lá, sem re-lançar), o `try/except` do `RegistrationFunnel` nunca
disparava o fallback de texto — o usuário não recebia confirmação
NENHUMA (nem botão, nem texto), achava que o cadastro não tinha funcionado, e
reenviava nome/curso de novo. Isso reiniciava o funil (3x no log de teste).

### Causa raiz

`agents/conversation/registration.py` chamava
`enviar_botoes_confirmacao(number=sender, ...)` — `sender` é o JID do
**remetente individual dentro do grupo**, não o JID do grupo. Em grupo,
toda entrega tem que ser endereçada ao JID do **grupo** (`chat_id`/`remote_jid`),
igual o resto do funil já faz (`gateway.enviar_mensagem(chat_id, reply)`).

Piorou porque o WhatsApp mudou o addressing de contatos pra `@lid`
(identificador de privacidade) em vez do número de telefone puro — o
`175174737518829` no erro é o LID, não o telefone real da pessoa
(`559887680098`, visível como `participantAlt` no webhook). Tentar montar um
JID `@s.whatsapp.net` a partir do LID nunca vai existir de verdade.

### O que foi corrigido

- `RegistrationFunnel.process()` ganhou parâmetro `chat_id` — usado no envio
  de botões (`number=chat_id or sender`) em vez de `sender`.
- `process_message_task.py` e `ConversationAgent.execute()` (call sites)
  atualizados para passar `chat_id`.

### Pendência relacionada (não mexida ainda)

O fallback interno de `enviar_botoes_confirmacao()` continua engolindo a
exceção sem propagar — se o envio de botão falhar de novo por outro motivo no
futuro, o usuário vai ficar sem NENHUMA mensagem de confirmação de novo,
silenciosamente. Vale revisar `capabilities/messaging/evolution_tool.py`
depois pra re-lançar (ou pelo menos retornar um booleano de sucesso) em vez
de só logar.

---

## 3. Flags de teste ativas nesta rodada (lembrar de desligar depois)

- `DEV_TEST_NO_DB_WRITE=true` — cadastro/ticket/CRUD gravam JSON em
  `dados/tmp/*_dev/` em vez de tocar `pessoas` de verdade.
- `DEV_TEST_SKIP_REGISTRATION=true` — libera qualquer remetente a pular o
  funil de cadastro (senão, com a flag acima ligada, ninguém "vira
  registrado" de verdade e o gatekeeper força `REGISTER_MODE` pra sempre,
  loop sem saída).

Ambas em `src/infrastructure/settings.py`, opt-in via `.env`, default
`False`. **Religar antes de ir pra produção.**

---

## 4. Ainda não investigado: RAG retornando 0 chunks

Buscas em `CONTATOS`/`EDITAL` retornaram `0 chunks` mesmo com conteúdo
existente no Redis (ex: mock de contato do PROG). Log mostra "RAG busca
vazia. Acionando Step-Back Fallback" seguido de 0 chunks de novo. Suspeita
inicial (não confirmada): descompasso entre o `doc_type` pedido pela busca
(`contatos`, `edital`) e a tag real do chunk indexado (o nome do mock sugere
`doc_type=geral`). Não investigado a fundo ainda — problema separado do
roteamento, não mexido nesta rodada.

**Confirmado 2026-07-21 18:10:** quando a rota cai como `geral` (ex:
Orquestrador falhou e caiu no fallback), a busca acha os 5 chunks sem
problema (`doc=geral` bate com a tag real do chunk). Quando a rota vira
`contatos` explicitamente, dá 0 chunks sempre — bate com a suspeita: o filtro
de `doc_type` na busca híbrida é estrito e a tag real de TODOS os chunks
mock é `geral`, não `contatos`/`edital`/etc. Ou re-tagueia os chunks mock
com o `doc_type` certo, ou a busca por rota devia cair pra "geral" como
superset quando o filtro específico não retorna nada.

**CAUSA RAIZ EXATA (`redis_client.py::salvar_chunk` + `worker_rag_search.py`):**
`salvar_chunk()` grava um campo `tipo_doc` (o campo REALMENTE usado no
filtro RediSearch, um TAG separado de `doc_type`) que, se não vier explícito
na ingestão, cai no default `doc_type.capitalize()`. Esse mock foi ingerido
com `doc_type="geral"` sem `tipo_doc` explícito → gravou `tipo_doc="Geral"`
em TODO chunk, incluindo os de contato. O worker de busca
(`worker_rag_search.py:59-61`) filtra por `tipo_doc = doc_type.capitalize()`
— pra rota `CONTATOS` isso é `tipo_doc="Contatos"`. `"Geral" != "Contatos"`
→ **zero chunks sempre**, para qualquer pergunta, independente da pessoa
perguntada. Corrigido via retag pontual (`dados/tmp/retag_chunks.py`, rodado
em 2026-07-21) — script identifica pelo próprio texto do chunk
(`[CONTATOS MOCK...]`, `[EDITAL MOCK...]`) e corrige `doc_type`/`tipo_doc`
dos chunks já indexados, sem precisar reingestão.

**Por que parecia "aleatório" (Dr. Fulano "funcionava", Dra. Ana Carvalho
"não"):** não era sobre a pessoa — nenhuma pergunta sobre ninguém funcionava
via rota `CONTATOS` (sempre 0 chunks, 100% determinístico dado o bug acima).
O que parecia aleatório era **qual rota o classificador escolhia** para cada
mensagem (ver item 5.1 — 3 classificadores LLM brigando, um deles falhando
o parse de JSON com frequência alta). Quando a mensagem caía em `GERAL`
(sem o filtro problemático) por acaso, a busca funcionava e retornava
QUALQUER pessoa cujo chunk tivesse mais similaridade semântica com aquela
frase específica — às vezes Fulano, às vezes Ana, às vezes Roberto Melo.
Dois bugs independentes (dado mal tagueado + classificação de rota
inconsistente) se combinando pareciam um único bug "aleatório" de IA, mas
os dois são 100% determinísticos e rastreáveis no código — não é "a IA
decidindo à toa".

---

## 5. Plano futuro — pipeline de roteamento + observabilidade

> Registrado a pedido do usuário em 2026-07-21, depois de resolver o crash
> do `crud_confirm`. Isto é uma PROPOSTA, não uma decisão tomada — nada aqui
> foi implementado ainda.

### 5.1 Unificar os classificadores de intenção

Hoje existem até 3 chamadas LLM independentes decidindo "o que fazer" com
uma mensagem, sem se coordenarem:

1. `router/llm_fallback.py::orchestrate()` — ação de alto nível.
2. `router/supervisor.py::rotear()` — 5 camadas próprias (regex/heurística/
   regex seeded/KNN/Flash), com override do Orquestrador por cima.
3. `agents/academic_knowledge/planning.py::criar_plano()` — o Planner (Pro)
   ainda decide o worker final por conta própria dentro da whitelist
   `VALID_WORKERS`, às vezes ignorando a rota já decidida.

Isso já causou pelo menos 2 bugs nesta rodada (dag_hint dessincronizado,
worker fantasma `crud_confirm`) e custa 2-3 chamadas Gemini por mensagem.

**Proposta (não decidida):** avaliar fundir (1) e (2) numa única chamada de
classificação — o Orquestrador e o Supervisor hoje respondem perguntas quase
idênticas ("qual é a intenção desta mensagem?") com vocabulários
diferentes. Um único schema Pydantic com a união de rotas/ações resolveria
de vez esse tipo de conflito de precedência. Fazer isso com cuidado: são
dois códigos com histórico de bugs sutis de HITL/memória dependentes da
ordem atual (ver docstring de `llm_fallback.py`) — não é refactor trivial,
merece sessão própria com testes de regressão nos 3 agentes ativos.

### 5.2 Observabilidade — Prometheus

Métricas que ajudariam a pegar esse tipo de bug antes de virar erro em
produção (hoje só existem métricas de latência/cache-hit/tokens):

- `oraculo_router_override_total{orchestrator_action, supervisor_rota}` —
  contador toda vez que o Orquestrador sobrescreve a rota do Supervisor.
  Teria mostrado o volume real desse conflito antes de virar bug visível.
- `oraculo_planner_worker_not_found_total{worker}` — contador quando
  `_despachar_workers` não acha um worker no registry (o que aconteceu
  silenciosamente com `crud_confirm` por sabe-se lá quanto tempo antes desta
  rodada).
- `oraculo_orchestrator_json_parse_failures_total` — contador dos "JSON
  Inválido" que aparecem toda hora no log (Gemini retornando prosa tipo
  "Here is..." em vez de JSON puro — sinal de que o `response_schema`/
  `response_mime_type` não está sendo respeitado com confiabilidade,
  provavelmente por causa do `max_output_tokens` baixo cortando a resposta
  no meio, ver "Unterminated string" no log de "Crud").
- `oraculo_rag_zero_chunks_total{doc_type}` — contador de buscas que
  retornam 0 chunks, quebrado por `doc_type`. Teria apontado o problema do
  item 4 acima imediatamente (100% das buscas `doc_type=contatos` dando 0).

### 5.3 Observabilidade — Grafana

Painel novo "Roteamento & Planner" no dashboard existente, com:

- Distribuição de rotas decididas por mensagem (stacked bar por rota/hora).
- Taxa de override do Orquestrador sobre o Supervisor (dado o contador 5.2).
- Taxa de falha de parse JSON do Orquestrador/Flash/Planner (os 3 usam o
  mesmo padrão `response_mime_type=application/json` + parse manual — se um
  falha por causa de `max_output_tokens` curto, os outros provavelmente
  também falham às vezes, só não apareceu ainda).
- Taxa de "0 chunks" por `doc_type` na busca RAG.

### 5.4 Logs — Docker/Celery

O log hoje é só stdout do container, sem correlação fácil entre serviços
(ex: seguir um `plan_id` do `oraculo_worker` até o `oraculo_evolution` exige
grep manual em dois containers diferentes). Propostas, por ordem de
esforço:

1. **Baixo esforço:** garantir que TODO log relevante (roteamento, planner,
   dispatch, delivery) sempre inclua `plan_id`/`session_id` no formato —
   hoje a maioria já inclui, mas alguns (ex: erros do Orquestrador) não.
2. **Médio esforço:** adicionar Loki + Promtail ao `docker-compose.yml` (já
   tem Prometheus/Grafana rodando) — os logs dos containers passam a ser
   consultáveis no próprio Grafana via LogQL, filtrando por `plan_id` sem
   precisar de `docker logs` manual em cada serviço.
3. Também vale corrigir o healthcheck dos workers Celery — hoje TODOS
   aparecem "(unhealthy)" no `docker ps` porque o healthcheck da imagem
   parece assumir um servidor HTTP na porta 9000 que só a `api` roda de
   verdade. Isso não afeta o funcionamento, mas mascara sinais reais de
   problema (não dá pra saber se um worker está realmente doente ou é só
   ruído do healthcheck errado).

### 5.5 Painel admin único (`/hub`) — levantamento factual (2026-07-21)

Usuário perguntou "o que são Router/Orquestrador no painel, posso ter um
painel pra eles" — levantamento do que EXISTE hoje (não é proposta, é
estado atual confirmado no código):

**Já existe e funciona:**
- `/hub` (`src/api/routers/web/hub.py`, login por cookie) — dashboard,
  liga/desliga por agente (`/hub/agents`, grava Postgres+Redis via
  `agent_config.py`), edição de prompt versionado por agente, gestão de
  usuários, audit log, simulador de chat, dashboard de avaliação RAG,
  chunkviz.
- `/hub/capabilities` só LISTA tools registradas em `capabilities/registry.py`
  — nenhuma tem consumidor vivo em produção hoje (decorativa).
- Catálogo de agentes é híbrido: lista de 4 agentes é HARDCODED em
  `agents/bootstrap.py` (autodiscovery seria "especulativo" pra só 4
  agentes, por comentário do próprio arquivo); Postgres (`agentes_catalogo`)
  só guarda enabled/disabled + prompt editável, não decide QUAIS agentes
  existem.

**Duplicação a limpar:** existe uma segunda API admin paralela
(`src/api/routers/admin/*`, auth por header `X-Admin-Key` estático, não
cookie) com funcionalidade sobreposta (usuários, audit, métricas de novo).
Dois sistemas de auth/admin fazendo parte da mesma coisa.

**NÃO existe hoje (gap real, não é só percepção do usuário):**
- Nenhum toggle de Router (`router/supervisor.py`) nem do Orquestrador
  (`router/llm_fallback.py::orchestrate()`) — só existe liga/desliga por
  AGENTE. Faz sentido a confusão: são peças de infraestrutura do pipeline
  sem representação nenhuma no painel hoje, e são literalmente os "2
  cérebros" do problema documentado na seção 5.1.
- RBAC (`ContextoPermissao`/`_PERMISSOES` em `domain/permissions.py`) é
  dicionário fixo no código-fonte — nenhuma tela edita isso.
- Redis: RedisInsight já roda (container separado, porta 8001) mas não
  está integrado/logado no hub — aba separada sem SSO.
- Postgres: nenhum admin, nem embutido nem separado (só `psql`).
- Logs: nenhuma visão centralizada no hub (ver 5.4 acima).

**Proposta pra próxima conversa (não decidida):** avaliar se `/hub` vira o
"ponto de ignição" único de verdade — unificar com `admin_api.py` (não dois
sistemas de auth), dar visibilidade real ao Router/Orquestrador (nem que
seja só um painel de leitura mostrando qual decidiu o quê por mensagem,
antes mesmo de ter toggle), trazer RBAC pra dentro do painel como
configuração editável, e embutir/linkar Redis+Postgres+logs no mesmo lugar
em vez de ferramentas espalhadas.
