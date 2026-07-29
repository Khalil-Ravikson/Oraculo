# Regras de Negócio do Oráculo — documento-base para discussão com a liderança

> **Metodologia:** todo item abaixo foi extraído lendo o código que roda hoje
> (branch `langgraph`, 2026-07-28) — não é uma visão aspiracional de "como
> deveria ser". Cada regra tem uma citação `arquivo:linha` pra qualquer
> pessoa técnica conferir. Onde o comportamento é uma inconsistência ou bug
> conhecido (não uma decisão de produto), isso está marcado explicitamente
> como tal, pra não virar "regra oficial" por engano numa reunião.
>
> Este documento NÃO é uma proposta de reescrita ("Oráculo 2.0"). É um
> retrato do que existe, pensado como ponto de partida pra decisões de
> negócio — não uma decisão de arquitetura.

---

## 0. Alerta de risco imediato — ler antes de qualquer outra coisa

Duas variáveis de ambiente estão **ativas agora** neste ambiente e mudam
comportamento de forma material:

| Flag | Valor atual | Efeito |
|---|---|---|
| `DEV_TEST_NO_DB_WRITE` | `True` (`.env:12`, default `True` no código) | Cadastro, abertura de chamado e atualização cadastral **não gravam no Postgres real** — tudo vira arquivo JSON local em `dados/tmp/*_dev/`. |
| `DEV_TEST_SKIP_REGISTRATION` | `True` (`.env:14`) | **Qualquer remetente é tratado como já cadastrado**, mesmo sem nunca ter passado pelo funil — o "cadastro obrigatório" está desligado na prática. |

Fontes: `src/infrastructure/settings.py:21-33`, `.env:12,14`,
`src/agents/conversation/registration.py:132-134`,
`src/agents/tickets/ticket_flow.py:14-18`, `src/agents/tickets/crud_tool.py:119-124`,
`langgraph_experiment/nodes.py:357-361`, `src/application/tasks/process_message_task.py:329`,
`src/agents/tickets/rbac.py:22-31`.

**Isso precisa ser revertido antes de qualquer uso com dados reais de
usuários.** Enquanto essas flags estiverem ligadas, nenhuma das regras de
persistência descritas abaixo tem efeito real no banco.

---

## 1. Admissão e roteamento de mensagens

1. **O bot hoje atende só UM grupo do WhatsApp**, definido por configuração
   (`ALLOWED_GROUP_ID`) — toda mensagem de outro grupo é descartada
   silenciosamente, checado duas vezes (webhook + worker).
   `src/application/webhook/webhook_controller.py:76-77`,
   `src/application/tasks/process_message_task.py:316-317`
2. **Mensagem privada (DM) só é processada se vier de um número admin.**
   Qualquer outra pessoa em DM é ignorada — o produto hoje é, na prática,
   "bot de grupo único", não um assistente de DM aberto. O próprio código
   comenta que isso é modo "beta" (`gatekeeper.py:67`) — **vale confirmar
   com o time se é decisão permanente ou só do piloto atual.**
   `src/router/gatekeeper.py:72-74`
3. O bot nunca responde a si mesmo (mensagens `fromMe: true` descartadas).
   `src/application/webhook/webhook_controller.py:22-24`
4. Dentro do grupo, o bot só reage a gatilhos explícitos: `$` (comando
   admin), `!` (comando público) ou menção `@oraculo`. Conversa entre
   pessoas no grupo é ignorada — não é "escuta tudo".
   `src/router/gatekeeper.py:85-89`
5. Existe um segundo canal de admissão (fluxo 1-a-1, `_processar_async`),
   mais restritivo: exige identidade cadastrada e ativa no banco; sem isso,
   orienta a procurar secretaria/CTIC. Usuário cadastrado mas com status
   diferente de "ativo" é ignorado silenciosamente (sem aviso).
   `src/application/tasks/process_message_task.py:56-73`
6. Trava de concorrência por usuário: mensagem nova do mesmo telefone
   enquanto uma anterior ainda processa é reenfileirada (retry em 5s) em
   vez de rodar em paralelo — evita resposta fora de ordem/duplicada.
   `src/application/tasks/process_message_task.py:88-93`
7. **Funil de cadastro tem prioridade máxima no roteamento**: se o usuário
   está cadastrando ou não está cadastrado, TODA mensagem dele — mesmo que
   pareça comando — vai pro funil de cadastro, nunca pra IA/comandos.
   `src/router/gatekeeper.py:76-82`

## 2. Classificação de intenção — "Supervisor" (5 camadas) + "Orquestrador"

8. O classificador de rota (Supervisor) resolve a intenção em 5 camadas, da
   mais barata pra mais cara, só descendo de camada quando a anterior não
   resolve com confiança: **(1)** regex fixo no código (link
   YouTube/Instagram, saudação pura, frases óbvias de ticket, vocabulário
   SIGAA) → **(2)** heurísticas hardcoded (ex: "sigaa"+"senha" → WIKI,
   "calendário" → CALENDARIO) → **(3)** regex configurável pelo painel
   admin, sem precisar de deploy → **(4)** busca por similaridade semântica
   (aceita só se ≥82% de confiança) → **(5)** Gemini Flash, com fallback de
   regex de emergência se a IA falhar.
   `src/router/supervisor.py:8-13,71-226`
9. Cache hit/miss por camada é medido — dá visibilidade de quanto do
   tráfego é resolvido de graça vs. indo pra IA paga.
   `src/router/supervisor.py:15-16,42-51`
10. As 9 rotas de negócio hoje são: `GERAL`, `CALENDARIO`, `EDITAL`,
    `CONTATOS`, `WIKI`, `TICKET_ABERTURA`, `CRUD`, `SIGAA`, `GREETING`,
    `MEDIA_DOWNLOAD`. `src/router/llm_fallback.py:59-69`
11. **Para toda mensagem em linguagem natural (sem `!`/`@`/`$`), um SEGUNDO
    classificador de IA (o "Orquestrador") também roda, e a decisão dele
    sempre prevalece sobre a do Supervisor** — mesmo quando o Supervisor
    acertou. `src/application/runtime/dispatcher.py:141-216`,
    `src/router/llm_fallback.py:6-24`
12. `TICKET_ABERTURA` e `CRUD` têm atalho e nunca passam pelo planejador
    genérico de IA — reduz custo/risco nessas duas jornadas.
    `src/application/runtime/dispatcher.py:365-379`

> ⚠️ **Bug conhecido, não decisão de produto (item 11):** quando a chamada
> ao Orquestrador FALHA (descrito no próprio `notas.md` como "frequência
> alta"), o sistema usa um valor de emergência hardcoded e trata esse valor
> como se fosse uma decisão válida — sobrescrevendo mesmo assim a rota
> correta que o Supervisor já tinha identificado. Efeito observado: pedidos
> de CRUD em linguagem natural podem falhar mesmo com o Supervisor
> acertando 100%. **Não corrigido até a data destas notas.**
> `notas.md:64-87`, `src/router/llm_fallback.py:271-276`

## 3. Controles administrativos

13. Cada "agente" (`academic_knowledge`, `sigaa`, `tickets`) pode ser
    ligado/desligado no painel web, com efeito imediato pra todos os
    usuários — pergunta na rota associada recebe "função temporariamente
    desativada" em vez de resposta. `GREETING`/`MEDIA_DOWNLOAD` não são
    "agentes" e não têm esse toggle.
    `src/application/runtime/dispatcher.py:68-78,218-232`
14. Falha de infraestrutura (Postgres fora do ar) nunca desliga uma
    funcionalidade sem intervenção humana — o padrão é sempre "ativo" na
    ausência de decisão explícita. `src/capabilities/persistence/agent_config.py:32-62`
15. Toggle de agente é auditável (quem, quando). `agent_config.py:65-83`
16. Só números na lista `ADMIN_NUMBERS` (variável de ambiente, obrigatória
    pro sistema subir) podem rodar comandos `$` no WhatsApp.
    `src/application/tasks/process_message_task.py:462-465`,
    `src/infrastructure/settings.py:72,102-103`
17. O painel web (`/hub`) usa autenticação própria (usuário/senha + JWT
    24h), **separada** da lista de admins do WhatsApp — são dois
    mecanismos de "ser admin" diferentes. `src/application/use_cases/admin_auth.py:1-17`

> ⚠️ **Inconsistência real, não nomenclatura (achado nesta pesquisa):**
> existem HOJE duas chaves de "modo manutenção" diferentes e **não
> equivalentes**:
> - `admin:maintenance_mode` — controlada pelo painel web, é a única
>   checada no filtro de entrada do fluxo 1-a-1 (bloqueia a conversa
>   inteira pra não-admin). `src/api/routers/admin/admin_api.py:320-339`
> - `admin:gemini_blocked` — controlada pelo comando `$M` no grupo, mas
>   **não é checada** na entrada do fluxo de grupo nem no `MessageRouter`;
>   só corta a chamada ao Gemini dentro do agente acadêmico.
>   `src/application/commands/cmd_maintenance.py:7-19`,
>   `src/agents/academic_knowledge/synthesis.py:13-31,92`
>
> **Consequência:** um admin que digita `$M` no grupo pensando estar
> ativando a manutenção do painel na verdade liga um interruptor diferente,
> que não bloqueia a entrada de mensagens nem aparece no status do hub.
> Vale confirmar com o time técnico qual caminho está de fato em uso antes
> de tratar isso como confiável numa comunicação oficial de manutenção.

## 4. RBAC — papéis e o que cada um pode fazer

18. 6 papéis existem: `publico`, `estudante`, `servidor`, `professor`,
    `coordenador`, `admin`. `src/domain/entities/enums.py:4-10`
19. **Público** (não cadastrado): informação institucional geral, PAES,
    calendário, ajuda de sistemas. Não pode: histórico próprio, abrir
    chamado, notificação de prazo. `src/domain/permissions.py:86-91`
20. **Estudante**: tudo do público + histórico acadêmico próprio + abrir
    chamado + notificações de prazo. `permissions.py:93-101`
21. **Servidor**: tudo do público + chamado + notificações — **mas sem
    histórico acadêmico** (não faz sentido pro papel, mas vale confirmar
    se é intencional). `permissions.py:103-110`
22. **Professor**: equivalente a estudante hoje (comentário no código já
    sinaliza intenção futura de "ver histórico dos próprios alunos", ainda
    não implementado). `permissions.py:112-120`
23. **Coordenador**: tudo do professor + gestão de documentos (ingerir
    material na base de conhecimento). `permissions.py:122-131`
24. **Admin**: tudo dos demais + dashboard + gestão de usuários.
    `permissions.py:133-145`
25. **Status de matrícula tem prioridade sobre o papel**: usuário
    "inativo"/"pendente" só acessa recursos públicos, mesmo que o papel
    cadastrado seja estudante/professor/etc.
    `permissions.py:148-149,172-176,235-239`
26. Filosofia de recusa: o sistema nunca só nega — explica o que falta e
    oferece caminho pra resolver ali mesmo na conversa.
    `permissions.py:33-43,198-220`

## 5. Cadastro (registro de novo usuário)

27. Funil de 2 perguntas: nome completo, depois curso. Nome e curso exigem
    mínimo de 3 caracteres, normalizados em Title Case.
    `src/agents/conversation/registration.py:27-31,59-72`
28. Rascunho do funil expira em **10 minutos** de inatividade (Redis) — se
    o usuário some, reinicia do zero na próxima mensagem. `registration.py:14-18,55-56`
29. Falha técnica na gravação não limpa o estado — tenta salvar de novo a
    cada nova tentativa. `registration.py:73-81`
30. Ao concluir, envia botões de confirmação ("Sim, corretos"/"Refazer")
    via WhatsApp; se falhar, cai pra texto simples. `registration.py:83-104`
31. Cadastro é o **gatekeeper de acesso**: sem ele (e sem a flag de skip
    ligada), usuário não cadastrado é bloqueado do resto do sistema.
    `process_message_task.py:56-64,322-353`

## 6. Abertura de chamado (ticket) — duas implementações paralelas hoje

> Existem HOJE duas versões coexistindo: a state machine original
> (`ticket_flow.py`, em produção) e a versão nova via LangGraph
> (`langgraph_experiment/`, experimento desta branch). Escopos diferentes.

32. **Original** coleta: tipo (Incidente/Requisição, inferido por IA com
    confiança ≥0.7 ou perguntado), categoria (7 categorias ITIL fixas),
    local, campos de cadastro faltantes, CPF (só validado, nunca salvo),
    tombamento opcional, descrição livre, anexo opcional (≤2MB) e
    confirmação. `ticket_flow.py:41-93,235-267`
33. CPF nunca é persistido — usado só pra validar formato (11 dígitos) na
    hora. `ticket_flow.py:20-22,235-239`
34. Rascunho expira em 18 minutos de inatividade. `ticket_flow.py:10-12`
35. **Não existe integração real com GLPI hoje** — ao confirmar, grava um
    JSON local; a mensagem de sucesso é template fixo (não gerado por IA),
    justamente pra nunca inventar um número de chamado real que não existe.
    `ticket_flow.py:14-18,108-111,288-300`
36. **Versão LangGraph (nova)**: reduz pra 3 perguntas (tipo, categoria,
    queixa) + confirmação, 1 pergunta por vez (decisão técnica deliberada,
    ver `notas.md` item 7/8). `langgraph_experiment/nodes.py:180-277`
37. **Diferença importante:** na versão LangGraph, o node de salvamento do
    ticket **sempre** grava em JSON de teste, sem checar a flag
    `DEV_TEST_NO_DB_WRITE` — ou seja, mesmo desligando a flag de teste, a
    versão nova ainda não tem caminho de escrita real em banco pra tickets.
    É 100% stub hoje. `langgraph_experiment/nodes.py:268-277`
38. Ambas exigem RBAC (`Recurso.CHAMADO_GLPI` + flag `pode_abrir_chamado`
    da pessoa) antes de iniciar/finalizar. `src/agents/tickets/rbac.py:13-52`

## 7. CRUD de cadastro (autoatendimento)

39. Escopo propositalmente restrito a 2 campos: setor/centro e telefone —
    nada mais é editável por essa via. `crud_tool.py:1-14`, `nodes.py:281-287`
40. Validação: telefone entre 8-13 dígitos; setor precisa bater com uma
    sigla oficial cadastrada no sistema (`CentroEnum`) — a versão LangGraph
    mostra a lista de siglas válidas ao usuário; a versão original valida
    mais frouxo (só ≥2 caracteres, sem checar contra a lista real).
    `nodes.py:158-173,306-322`, `crud_tool.py:80-92`
41. RBAC igual ao ticket. `crud_tool.py:37-45`
42. **Escopo por identidade garantido na própria query SQL** (`WHERE
    telefone = :t` do remetente) — não é possível, por desenho, editar
    cadastro de outra pessoa. `src/capabilities/persistence/ticket_repository.py:37-64`
43. Gravação real segue o mesmo gate `DEV_TEST_NO_DB_WRITE` (diferente do
    ticket LangGraph — aqui SIM respeita a flag). `crud_tool.py:116-138`, `nodes.py:350-367`

## 8. Integração SIGAA (sistema acadêmico)

44. Via scraping (Playwright, fallback Selenium) — consulta notas, CR/IRA,
    histórico, horas complementares, estrutura curricular, turmas,
    calendário, biblioteca, extensão, processos seletivos.
    `src/agents/sigaa/service.py:59-320`
45. Consultas pessoais (notas, índice, histórico) exigem **CPF e senha do
    próprio SIGAA do aluno**, coletados por conversa (HITL). Consultas
    públicas (biblioteca, extensão) não exigem login.
    `auth_flow.py:66-99`, `service.py:326-357`
46. Senha do SIGAA nunca é persistida em disco — fica só em token temporário
    no Redis, TTL de **5 minutos**. `auth_flow.py:83,97-101`
47. Resultado de consulta fica em cache por **30 minutos** por sessão, pra
    não repetir scraping a cada pergunta. `service.py:29,43-57`
48. Se já existe sessão/cookies válidos, pula a reautenticação.
    `auth_flow.py:220-258`

## 9. RAG / Conhecimento institucional

49. Cada rota busca só no tipo de documento correspondente (não é busca
    livre): `CALENDARIO`→calendário, `EDITAL`→edital, `CONTATOS`→contatos,
    `WIKI`→wiki CTIC, `GERAL`→sem filtro. `src/router/supervisor.py:266-269`
50. Se a busca filtrada não retorna nada, usa o resultado geral em vez de
    devolver vazio — evita "não encontrei" desnecessário.
    `src/agents/academic_knowledge/service.py:200-203`
51. Cada chunk carrega taxonomia institucional (eixo, setor, tipo_doc, ano,
    campus, sistema, módulo), com padrões seguros quando o documento não
    informa. `src/infrastructure/redis_client.py:79-86,238-246`
52. Busca híbrida (texto+vetor) em paralelo pra pergunta original e
    variantes, combinadas e deduplicadas. `service.py:96-168`
53. Se a busca principal falha, aciona automaticamente uma segunda
    tentativa reformulada ("step-back") antes de desistir. `service.py:171-192`
54. Reordenamento adicional (cross-encoder local) reduz chance de resposta
    baseada em trecho pouco relevante. `service.py:205-209`
55. Não existe etapa de aprovação/revisão de documento: assim que a
    ingestão roda, o conteúdo já fica pesquisável — não há "pendente" antes
    de "publicado". `src/rag/ingestion/pipeline.py:163-225`

> ⚠️ Nota da pesquisa: `notas.md:264` registra que buscas com
> `doc_type=contatos` já retornaram 0 resultados por bug de índice, corrigido
> na rodada citada — **vale validar operacionalmente antes de afirmar como
> "funciona hoje" numa comunicação oficial**, já que não foi reverificado
> nesta pesquisa.

> ⚠️ **TODO já registrado, não corrigido**: o parser de ingestão sempre
> seleciona "Docling" pra PDF/DOCX, mesmo quando alguém tenta "desativar"
> — não existe flag de configuração pra isso hoje (`notas.md` item 8.5).

## 10. Guardrails (entrada e saída)

56. Entrada: mensagem acima de 1.200 caracteres é recusada; mais de 8
    mensagens em 60s bloqueia temporariamente; tentativas de manipular a
    IA (jailbreak, pedir pra revelar o prompt) são bloqueadas com mensagem
    genérica ao usuário, mas registradas em log pra auditoria.
    `src/application/chain/guardrails.py:53,58-59,64-82,136-198`
57. Não há filtro de palavrão nem de "fora do tópico UEMA" nessa camada — o
    controle de assunto é feito pelo roteamento e pelo prompt da IA.
58. Saída: resposta vazia/curta/que vaza instrução interna é descartada e
    substituída por mensagem padrão; CPF e e-mail pessoal (fora de
    `uema.br`) são automaticamente censurados; resposta acima de 4.000
    caracteres é truncada com aviso. `guardrails.py:85-87,229-295`
59. Não existe validação automática de "sempre citar a fonte" — a citação é
    montada na formatação do contexto ANTES da IA responder, não checada
    depois. `service.py:232-257`

## 11. Cache semântico

60. TTL do cache varia por rota, refletindo a volatilidade real da
    informação: Calendário 6h, Edital 24h, Contatos 48h, Wiki 12h,
    Saudação 2h, Geral 30min. Rotas de escrita (CRUD) nunca são cacheadas.
    `src/infrastructure/cache/semantic_cache.py:70-78`
61. Aceita pergunta "parecida" (similaridade semântica), com exigência mais
    rígida pra dado factual (calendário/edital/contatos) do que pra
    conteúdo geral — reduz risco de cache "generoso" em dado sensível a
    prazo. `semantic_cache.py:80-89`
62. Ingestão de documento novo invalida automaticamente o cache daquela
    fonte/rota. `semantic_cache.py:26-29`

## 12. Avaliação do usuário (`!1` a `!5`)

63. Toda resposta bem-sucedida convida a avaliação de 1 a 5.
    `process_message_task.py:423,510-512`
64. Nota é gravada em Redis (dashboard tempo real) e Postgres (histórico
    longo), junto com rota usada e score de recuperação.
    `src/application/commands/cmd_feedback.py:17-37`
65. **Não há evidência de que a nota alimente retreinamento automático ou
    dispare alerta** — hoje é registro pra análise manual/dashboard, não
    ação automática.

## 13. Memória / sessão

66. Duas camadas: memória "conversacional" (histórico + fatos extraídos) e
    memória "operacional" (estado de tarefa em andamento), ambas Redis.
    `src/memory/services/memory_service.py`
67. Histórico de conversa expira em **30 minutos** de inatividade.
    `src/memory/adapters/redis_working_memory.py:17,35`
68. Fatos de longo prazo (extraídos por IA em background) persistem por
    **30 dias**. `memory_service.py:144-163`
69. Memória "de usuário" (preferências agregadas) tem TTL de **7 dias**.
    `redis_memory_service.py:99-102`
70. Memória operacional é limpa assim que a entrega é confirmada, ou
    registra falha explícita se a entrega falhar. `process_message_task.py:550-559,581-586`

## 14. Entrega / notificação

71. Simula "digitando..." antes de responder.
    `process_message_task.py:220-222,378-385`
72. Após ~3s sem resposta pronta, envia aviso automático de espera,
    cancelado se a resposta chegar antes. `process_message_task.py:27-28,104-112`
73. Se a entrega via WhatsApp falhar, registra falha na memória
    operacional mas **não reenvia automaticamente** nesse caminho.
    `process_message_task.py:550-559`
74. Exceção não tratada no caminho síncrono aciona retry automático com
    backoff exponencial, até 3 tentativas. `process_message_task.py:31-35,191-203`

---

## Riscos e inconsistências conhecidos (resumo pra decisão)

| # | Item | Tipo | Referência |
|---|---|---|---|
| 1 | `DEV_TEST_NO_DB_WRITE`/`DEV_TEST_SKIP_REGISTRATION` ligados agora | Config de risco | Seção 0 |
| 2 | Orquestrador sobrescreve Supervisor mesmo quando falha (fallback tratado como decisão válida) | Bug conhecido, não corrigido | Item 11 |
| 3 | Duas chaves de "modo manutenção" diferentes e não equivalentes | Inconsistência real | Item 13 (seção 3) |
| 4 | Ticket via LangGraph sempre grava em JSON dev, ignora a flag de escrita real | Gap de implementação (stub) | Item 37 |
| 5 | GLPI não tem integração real em nenhuma das duas versões de ticket | Decisão de escopo já conhecida, não corrigida | Item 35 |
| 6 | Parser de ingestão sempre escolhe Docling, sem flag de desativar | TODO registrado, não corrigido | Item 55 |
| 7 | Bot só atende 1 grupo + DM só pra admin | Possível decisão de piloto, não confirmada como permanente | Itens 1-2 |
| 8 | `langgraph_experiment/` é experimento isolado nesta branch, ainda não mesclado ao `main` | Escopo desta branch, não é o sistema "oficial" ainda | — |

---

## Perguntas para a reunião com a liderança

1. As flags de teste (`DEV_TEST_NO_DB_WRITE`/`DEV_TEST_SKIP_REGISTRATION`)
   podem ser desligadas já, ou ainda estamos em fase de piloto controlado?
2. O escopo "só um grupo do WhatsApp + DM restrito a admin" é a visão
   definitiva de produto, ou é só o estágio atual do piloto?
3. Vale priorizar a integração real com GLPI, ou o fluxo de ticket
   continua como registro interno (JSON/Postgres) por enquanto?
4. A inconsistência dos dois "modos de manutenção" precisa virar um único
   controle confiável antes de divulgar esse recurso pra quem opera o bot?
5. O bug do Orquestrador sobrescrevendo o Supervisor tem prioridade de
   correção, dado que já causou falha real em pedidos de CRUD?
6. Qual o critério pra decidir se/quando o experimento LangGraph
   (`langgraph_experiment/`, branch isolada) deveria ser avaliado pra
   produção — e sob qual arquitetura de deploy (ver `notas.md` desta
   branch, item 7, "roteiro pós-validação")?
7. Faz sentido investir em unificar os 3 classificadores de intenção
   (Supervisor + Orquestrador + Planner) — já custam 2-3 chamadas de IA
   por mensagem e já causaram pelo menos 2 bugs de precedência
   (`notas.md` item 5.1)?
