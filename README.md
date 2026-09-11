# 🔮 ORÁCULO UEMA — Documentação Completa

> Assistente institucional inteligente da Universidade Estadual do Maranhão (UEMA),
> acessível via WhatsApp e portal web administrativo.

---

## Índice

1. [O que é o Oráculo?](#1-o-que-é-o-oráculo)
2. [Visão Geral da Arquitetura](#2-visão-geral-da-arquitetura)
3. [Stack de Tecnologias](#3-stack-de-tecnologias)
4. [Estrutura de Pastas](#4-estrutura-de-pastas)
5. [Como o Sistema Funciona (fluxo completo)](#5-como-o-sistema-funciona-fluxo-completo)
6. [Redis — O Coração do Sistema](#6-redis--o-coração-do-sistema)
7. [O Sistema RAG (Busca Inteligente)](#7-o-sistema-rag-busca-inteligente)
8. [Ingestão de Documentos](#8-ingestão-de-documentos)
9. [Memória do Agente](#9-memória-do-agente)
10. [Sistema de Permissões (RBAC)](#10-sistema-de-permissões-rbac)
11. [HITL — Confirmação Humana](#11-hitl--confirmação-humana)
12. [Plataforma Web Admin](#12-plataforma-web-admin)
13. [Configuração e Variáveis de Ambiente](#13-configuração-e-variáveis-de-ambiente)
14. [Instalação e Execução (Docker)](#14-instalação-e-execução-docker)
15. [Celery — Tarefas em Background](#15-celery--tarefas-em-background)
16. [Observabilidade (Prometheus + Grafana + /hub/llm-custo)](#16-observabilidade-prometheus--grafana--hubllm-custo)
17. [Testes](#17-testes)
18. [Comandos Úteis](#18-comandos-úteis)
19. [Glossário para Leigos](#19-glossário-para-leigos)

---

## 1. O que é o Oráculo?

O **Oráculo UEMA** é um agente inteligente universitário. Pense nele como um
assistente 24h que responde perguntas dos alunos, professores e funcionários da
UEMA pelo WhatsApp — sem precisar ligar para a secretaria, sem esperar atendimento.

**No v1 ele é um bot de menu.** O usuário digita um número e o bot responde
de forma determinística. Isso não é uma limitação temporária de
implementação: é a decisão de produto, porque torna o custo previsível e a
taxa de erro baixa. Ver [`docs/ESTADO_ATUAL.md`](docs/ESTADO_ATUAL.md).

**O que ele responde hoje**

- 💻 Sistemas da UEMA — SIGAA e SIPAC, a partir da wiki da CTIC
- 🔑 Acesso — senha, usuário, e-mail institucional, Wi-Fi
- 📞 Telefones e setores da UEMA
- 🙋 Encaminhamento para um atendente humano da CTIC
- 🎙️ Nota de voz, transcrita por Gemini STT

**O que fica para depois** (cada um volta como um item de menu novo, sem
retrabalho de arquitetura)

- 📅 Calendário acadêmico
- 📋 Edital PAES
- 🎓 Notas e histórico no SIGAA — exige login por conversa
- 🎫 Abertura de chamado formal no GLPI — hoje coberto por "falar com um
  atendente"

**O que ele não faz, por decisão**

- Não age sozinho: não existe agente autônomo decidindo chamadas de
  ferramenta em loop
- Não classifica sua intenção com IA — quem roteia é o menu
- Não atende usuário bloqueado ou inativo

---

## 2. Visão Geral da Arquitetura

> Reescrita em 2026-09-09 (item A3). A versão anterior mostrava
> `dispatcher_langgraph.py` e um "Planner (DAG de workers)" — o Planner foi
> deletado pela ADR 0008 e o dispatcher virou
> `application/orchestration/entrypoint.py`.
>
> 📖 Detalhe completo: [`docs/architecture/arquitetura_oraculo.md`](docs/architecture/arquitetura_oraculo.md).
> Escopo do v1, flags e checklist de produção:
> [`docs/ESTADO_ATUAL.md`](docs/ESTADO_ATUAL.md).

```
WhatsApp
   │
   ▼
Evolution API ──→ Webhook FastAPI (responde 200 na hora)
   │
   ▼
[PORTEIRO Postgres]  telefone, status, RBAC
   │
   ▼
[LOCK Redis]  lock:msg:{phone}, TTL 90s — serializa a sessão
   │
   ▼
Celery worker (fila default)
   │
   ├─ gatekeeper.py ──→ pré-filtro
   │
   ▼
application/orchestration/entrypoint.py   ← ORQUESTRADOR ÚNICO (ADR 0008)
   │
   ├─ sessão em atendimento humano? → silencia
   ├─ fast-paths: áudio (STT), mídia sem legenda, labs
   ├─ guardrails de entrada
   ├─ funil pausado? → retoma de onde parou
   │
   ├─ MOTOR DE MENU (v1) ─┬─ menu / texto fixo → responde aqui   ⟵ 0 token
   │                      ├─ pedir atendente → nó human_handoff  ⟵ 0 token
   │                      └─ pergunta → grafo, rota já decidida
   ▼
StateGraph (LangGraph) — topologia é DADO (GraphSpec), não código
   │
   ├─ classify_node — não faz nada quando o menu já decidiu
   ▼
nó da rota
   └─ rag → busca híbrida no Redis → síntese (Gemini/DeepSeek/Groq)
   │
   ▼
resposta + tela "Isso ajudou?" → Evolution API → WhatsApp
```

**O ponto que resume o v1:** o LLM só é chamado na síntese de uma resposta de
RAG. Navegar o menu, ler um texto fixo e pedir atendente custam zero token.

**LangChain** é usado só para embeddings e chunking do RAG. **Não** é o
framework de orquestração — quem orquestra é o LangGraph, e o `OracleChain`
citado em versões antigas não existe mais.

**Clean Architecture** — quatro camadas:

```
domain/          regras de negócio puras, sem framework
application/     casos de uso, orquestração, workers
infrastructure/  Postgres, Redis, LLM, WhatsApp
api/             endpoints FastAPI
```

---

## 3. Stack de Tecnologias

| Componente | Tecnologia | Para que serve |
|---|---|---|
| Linguagem | Python 3.12 (imagem Docker usa 3.11-slim — ver nota) | Toda a aplicação |
| API Web | FastAPI | Webhook WhatsApp + portal admin |
| LLM | Google Gemini (padrão) + DeepSeek/Groq (alternativos, trocáveis em runtime via `/hub/llm-custo`) | Geração de respostas |
| Framework LLM | LangGraph para o grafo; camadas próprias em volta (`menu/` decide, `rag/knowledge/` responde, `capabilities/` integra). LangChain só para embeddings e chunking | Pipeline de RAG |
| Banco relacional | PostgreSQL | Usuários, identidade, auditoria |
| Banco vetorial | Redis Stack + RedisVL | Documentos, embeddings, buscas semânticas |
| Fila de tarefas | Celery + Redis | Processar mensagens em background |
| Canal WhatsApp | Evolution API | Recebe/envia mensagens |
| ORM | SQLAlchemy Async | Interface com PostgreSQL |
| Migrações | Alembic | Versionamento do banco |
| Métricas | Prometheus | Coleta de dados de performance |
| Dashboards | Grafana | Visualização de métricas |
| Containers | Docker + Compose | Deploy e orquestração |

---

## 4. Estrutura de Pastas

> Atualizada em 2026-09-09 (item A3): os dois `dispatcher*.py` foram
> deletados pela ADR 0008 e `src/graph/` virou `src/graph_studio/`.

oraculo-uema/
├── src/
│   ├── api/                    # Endpoints FastAPI (camada de apresentação)
│   │   ├── routers/web/hub.py          # Portal web admin (rotas HTML, /hub)
│   │   ├── routers/admin/admin_api.py  # REST API do admin (JSON)
│   │   ├── routers/admin/eval_api.py   # Avaliação RAG (SSE)
│   │   ├── routers/tools/chunkviz_tools.py  # Upload/visualização de chunks
│   │   ├── monitor.py           # Monitor live (SSE)
│   │   └── middleware/
│   │       └── auth_middleware.py  # JWT admin
│   │
│   ├── router/                  # Supervisor — FORA do caminho crítico no v1
│   │   ├── supervisor.py        # regex → heurística → KNN → Flash (só em rollback)
│   │   ├── gatekeeper.py        # pré-filtro (ignorar/registro/comando/LLM)
│   │   └── llm_fallback.py      # classificação estruturada via LLM
│   │
│   ├── rag/                     # Embeddings, ingestão e o RAG do v1
│   │   └── knowledge/           #   busca híbrida + síntese da resposta
│   ├── domain_services/         # SIGAA, chamados, cadastro — fora do v1
│   ├── capabilities/            # Tools/integrações autodescobertas
│   │
│   ├── application/            # Casos de uso, orquestração, workers
│   │   ├── orchestration/       # ★ ORQUESTRADOR ÚNICO (ADR 0008)
│   │   │   ├── entrypoint.py    #   processar() — o caminho de toda mensagem
│   │   │   ├── builder.py       #   GraphSpec → StateGraph
│   │   │   ├── nodes.py         #   classify_node + um nó por rota
│   │   │   ├── spec.py          #   topologia do grafo como DADO
│   │   │   └── specs/default.json
│   │   ├── menu/                # ★ MOTOR DE MENU do v1
│   │   │   ├── resolver.py      #   a decisão — função pura, sem LLM
│   │   │   ├── spec.py          #   menu como dado + validação
│   │   │   ├── state.py         #   posição do usuário no Redis
│   │   │   └── menus/default.json
│   │   ├── tasks/
│   │   │   └── process_message_task.py  # task Celery de entrada
│   │   ├── workers/            # worker_*.py
│   │   └── use_cases/
│   │
│   ├── graph_studio/           # Componentes do Hub + sandbox — NÃO é produção
│   │
│   ├── domain/                 # Regras de negócio puras
│   │   ├── entities/           # Modelos do domínio
│   │   ├── ports/              # Interfaces (contratos, Clean Architecture)
│   │   └── permissions.py      # RBAC
│   │
│   ├── infrastructure/         # Detalhes técnicos (banco, redis, LLM)
│   │   ├── adapters/           # Implementações dos ports
│   │   │   ├── gemini_provider.py            # Adapter Gemini
│   │   │   ├── openai_compatible_provider.py # Adapter DeepSeek/Groq
│   │   │   ├── llm_factory.py                # Ponto único de resolução de provider
│   │   │   ├── evolution_adapter.py          # Adapter WhatsApp
│   │   │   └── parsers/        # Parsers de documentos (PDF, DOCX, etc.)
│   │   ├── redis_client.py     # Cliente Redis + schemas RedisVL
│   │   ├── settings.py         # Configurações (.env)
│   │   ├── celery_app.py       # Configuração Celery
│   │   ├── logging_config.py   # Logging estruturado
│   │   └── observability/      # Prometheus (métricas + custo LLM)
│   │
│   ├── memory/                 # Sistema de memória em múltiplas camadas
│   │   ├── ports/              # Interfaces de memória
│   │   ├── adapters/           # Implementações Redis
│   │   └── services/           # MemoryService (orquestra)
│   │
│   ├── rag/                    # Transformação/roteamento de queries (caminho legado, não hot-path)
│   │   ├── embeddings.py       # Modelo de embeddings (Gemini/local)
│   │   └── ingestion/          # Pipeline de ingestão de documentos
│   │       ├── pipeline.py     # Pipeline principal
│   │       ├── parser_factory.py    # Fábrica de parsers
│   │       └── chunker_factory.py   # Fábrica de chunkers
│   │
│   └── main.py                 # Entry point da aplicação
│
├── docs/                       # Documentação (ver docs/README.md — índice)
│   ├── architecture/            # Arquitetura técnica (fonte oficial)
│   ├── business/                 # Regras de negócio (fonte oficial)
│   ├── decisions/                 # ADRs
│   ├── historico/                  # Docs concluídos/superados
│   └── assets/                    # Apresentações, relatórios, exports
│
├── templates/                  # HTML do portal web (Jinja2)
│   └── hub/                    # Templates do hub admin
│       ├── dashboard.html      # Página inicial
│       ├── chat.html           # Simulador de chat
│       ├── audit.html          # Log de auditoria
│       ├── users.html          # Gestão de usuários
│       ├── chunkviz.html       # Visualizador de chunks
│       └── config.html         # Configuração do sistema
│
├── rest_lab/ · mcp_lab/        # Laboratórios de pesquisa (não produto), com
│                                  # camada de Application própria (ADR 0005/0006)
├── dados/                      # PDFs e documentos para ingestão
├── static/                     # CSS, JS, imagens
├── migrations/                 # Migrações Alembic (PostgreSQL)
├── tests/                      # Testes unitários, integração, e2e
├── observability/              # Configs Prometheus/Grafana
├── docker-compose.yml          # Orquestração de containers
├── Dockerfile                  # Build da imagem
└── .env                        # Variáveis de ambiente (NÃO commitar!)

---

## 5. Como o Sistema Funciona (fluxo completo)

> Reescrita em 2026-09-09 (item A3). A versão anterior narrava o pipeline
> `OracleChain` (`route_intent` → `transform_query` → `grade_docs` → …), que
> não existe no código há tempos.

### 5.1 Uma conversa real, passo a passo

**Mensagem 1 — o usuário manda "oi"**

1. **Evolution API** entrega no webhook: `POST /webhook/evolution`.
2. **Porteiro (Postgres)** procura o telefone, confere status e monta a
   identidade. Bloqueio aqui não gasta token nenhum.
3. **Lock (Redis)** cria `lock:msg:{phone}` com TTL de 90s, para duas
   mensagens da mesma sessão não se atropelarem.
4. **Celery** assume; o webhook já respondeu 200.
5. **Entrypoint** vê que a sessão não está em atendimento humano, passa pelos
   guardrails, e chega ao **motor de menu**.
6. O menu não encontra estado para a sessão e responde o **menu principal**.

**Custo: zero token.** O grafo nem foi invocado.

**Mensagem 2 — o usuário manda "2"**

O menu resolve a tecla, empilha a posição e responde a tela de sistemas
(SIGAA e SIPAC). **Zero token de novo.**

**Mensagem 3 — o usuário manda "1" (SIGAA)**

O menu anota uma *pergunta pendente* com `rota=WIKI`,
`doc_type=wiki_ctic`, `filtros={sistema: sigaa}` e responde o convite:
"Escreva sua pergunta sobre o SIGAA." **Ainda zero token.**

**Mensagem 4 — "como emito declaração de vínculo?"**

Aqui, e só aqui, o grafo entra:

1. O menu vê a pergunta pendente e devolve rota e taxonomia **já decididas**.
   Nada é classificado por LLM.
2. O grafo é invocado com `route="rag"`. O `classify_node` vê a rota
   preenchida e não faz nada.
3. **`rag_node`** consulta o cache semântico. Acerto no cache responde sem
   chamar o modelo.
4. Se não houver cache: **busca híbrida** no Redis — dois `FT.SEARCH` (HNSW
   vetorial e BM25 textual), fusão por RRF calculada em memória, e rerank por
   cross-encoder local (CPU).
5. **Síntese**: os chunks vão ao Gemini, que escreve a resposta ancorada
   neles. **Esta é a única chamada de LLM da conversa inteira.**
6. O entrypoint anexa a tela "Isso ajudou?" e o menu fica parado nela.

**Mensagem 5 — o usuário manda "0" a qualquer momento**

Handoff: o bot silencia a sessão (`handoff:session:{id}`, TTL 24h), enfileira
em `handoff:queue` e avisa a equipe no `SUPPORT_GROUP_JID`. Zero token.

### 5.2 Resumo do custo por tipo de ação

| Ação | Chamadas de LLM |
|---|---|
| Abrir menu, navegar, voltar | 0 |
| Ler um texto fixo | 0 |
| Pedir atendente | 0 |
| Fazer uma pergunta | 1 síntese, ou 0 se cair no cache |

---

## 6. Redis — O Coração do Sistema

O Redis é usado para **7 responsabilidades diferentes**. Cada uma tem prefixo
de chave distinto para não conflitar:

| Prefixo | O que armazena | TTL |
|---|---|---|
| `rag:chunk:{source}:{id}` | Chunks de documentos + embeddings | Permanente |
| `chat:{session_id}` | Histórico da conversa (últimas 10 mensagens) | 30 min |
| `mem:facts:list:{user_id}` | Fatos de longo prazo do usuário | 30 dias |
| `lock:msg:{phone}` | Lock anti-spam por usuário | 90s |
| `hitl:{session_id}` | Ação pendente de confirmação | 5 min |
| `admin:system_prompt` | Prompt customizado pelo admin | Permanente |
| `admin:maintenance_mode` | Flag de manutenção | Permanente |
| `cache:{hash}` | Cache semântico de respostas | 7 dias |
| `monitor:logs` | Logs de métricas em tempo real | 24h |
| `audit:log` | Log de ações admin | 90 dias |

### 6.1 Índices do Redis Stack (busca semântica)

O Redis Stack inclui o **RediSearch** — um motor de busca completo dentro do Redis.
Criamos dois índices:

**`idx:rag:chunks`** — para busca nos documentos
- Busca por texto (BM25): encontra por palavras exatas
- Busca vetorial (HNSW): encontra por similaridade semântica
- Filtros por metadata: `source`, `doc_type`, `semester`, `event_type`

**`idx:tools`** — para roteamento semântico
- Guarda embeddings das intenções das tools
- KNN (K-Nearest Neighbors): encontra a tool mais adequada para a pergunta

### 6.2 Busca Híbrida (RRF)

Pergunta: "quando é a matrícula de veteranos?"
BM25 encontra:         Vetor encontra:

chunk_001 (0.9)    1. chunk_001 (dist=0.12)
chunk_005 (0.7)    2. chunk_003 (dist=0.18)
chunk_002 (0.6)    3. chunk_002 (dist=0.22)

RRF fusão:

chunk_001: 1/61 + 1/61 = 0.032  ← vencedor claro
chunk_002: 1/63 + 1/63 = 0.031
chunk_005: 1/62 + 1/64 = 0.031

O **RRF (Reciprocal Rank Fusion)** combina os dois rankings de forma matemática,
dando mais peso a documentos que aparecem bem em ambas as buscas.

---

## 7. O Sistema RAG (Busca Inteligente)

RAG significa **Retrieval-Augmented Generation** — em português:
"Geração Aumentada por Recuperação".

**Para leigos:** Imagine que o LLM é um estudante que não sabe nada de UEMA.
O RAG é como dar ao estudante um "livro de consulta" com todos os documentos
relevantes, ANTES de ele responder. Assim, ele responde com base em fatos reais,
não em invenções.

### 7.1 CRAG Score (qualidade do retrieval)

O **CRAG (Corrective RAG)** avalia se o que foi encontrado é relevante para a pergunta:
Score 0.0 → 0.3: retrieval muito ruim → resposta pode ser inventada
Score 0.3 → 0.6: retrieval parcial → resposta com ressalvas
Score 0.6 → 1.0: retrieval excelente → resposta confiável
### 7.2 Fluxo de Roteamento
Mensagem do usuário
│
▼
[Regex 0ms, 0 tokens]
Detecta padrões: "matrícula", "paes", "email"...
│
├─ Alta confiança (>0.85) → vai direto para RAG
│
└─ Baixa confiança → KNN semântico (Redis, ~10ms)
│
├─ Alta confiança → RAG
└─ Baixa confiança → Gemini decide

---

## 8. Ingestão de Documentos

Ingestão = transformar um PDF em dados pesquisáveis no Redis.

### 8.1 Pipeline de Ingestão
PDF/DOCX/CSV/TXT
│
[PARSER]
Extrai texto limpo
PyMuPDF, Docling, Marker, Unstructured
│
[CHUNKER]
Divide em pedaços (chunks)
Recursive, Markdown, Semantic
│
[EMBEDDING]
Gera vetor numérico para cada chunk
Google Gemini Embedding / BAAI/bge-m3 (local)
│
[REDIS]
Salva chunk + embedding + metadata
Prefixo: rag:chunk:{source}:{id}

### 8.2 Escolha automática de parser

| Tipo de arquivo | Parser recomendado | Por quê |
|---|---|---|
| PDF com texto | Docling (IBM) | Preserva layout, converte tabelas |
| PDF escaneado (imagem) | Marker | OCR via ML, extrai texto de imagens |
| DOCX | Docling ou Unstructured | Suporte nativo |
| CSV | CsvAdapter | Transforma linhas em frases semânticas |
| TXT/MD | TxtAdapter | Leitura direta |

### 8.3 Chunking — por que dividir?

O LLM tem um limite de tokens. Um PDF de 100 páginas não cabe inteiro.
O chunking divide o documento em pedaços de ~400 caracteres com sobreposição
(overlap) de ~60 caracteres entre pedaços adjacentes, para não perder contexto.

### 8.4 Como ingerir um documento

**Via portal web:**
1. Acessar `/hub/chunkviz`
2. Fazer upload do arquivo
3. Visualizar os chunks gerados
4. Clicar em "Ingerir ao Redis"

**Via WhatsApp (admin):**
Enviar arquivo + mensagem: !ingerir

---

## 9. Memória do Agente

O Oráculo tem **3 camadas de memória**:

### 9.1 Memória de Trabalho (Working Memory)
- O que é: histórico da conversa ATUAL
- Onde fica: Redis `chat:{session_id}`
- Quanto guarda: últimas 10 mensagens (5 pares pergunta/resposta)
- TTL: 30 minutos de inatividade
- Para que serve: saber o que foi dito agora ("você me disse anteriormente que...")

### 9.2 Memória de Longo Prazo (Long-Term Memory)
- O que é: fatos sobre o usuário extraídos de conversas anteriores
- Onde fica: Redis `mem:facts:list:{user_id}`
- Quanto guarda: 50 fatos por usuário
- TTL: 30 dias
- Para que serve: personalizar respostas ("você estuda Eng. Civil, turno noturno")

### 9.3 Memória de Identidade (Identity Memory)
- O que é: dados cadastrais do usuário
- Onde fica: PostgreSQL
- Para que serve: validação, RBAC, contexto base

---

## 10. Sistema de Permissões (RBAC)

RBAC = Role-Based Access Control = controle de acesso baseado em papéis.

| Role | Quem é | O que pode fazer |
|---|---|---|
| `publico` | Visitante não cadastrado | Informações gerais (calendário, edital, contatos) |
| `estudante` | Aluno ativo cadastrado | Tudo do público + abrir chamados GLPI + notificações |
| `professor` | Professor da UEMA | Tudo do estudante + dados de turmas |
| `servidor` | Servidor administrativo | Tudo do estudante |
| `coordenador` | Coordenador de curso | Tudo + ingerir documentos do curso |
| `admin` | Administrador CTIC | Acesso total |

**Admin** é definido por número de telefone no `.env` (`ADMIN_NUMBERS`).

---

## 11. HITL — quando uma pessoa entra no loop

> Reescrita em 2026-09-09 (item A3). A descrição anterior — "o LLM detecta
> intenção de ação crítica" e grava `hitl:{session_id}` — descrevia **um**
> mecanismo, feito por LLM. Existem **três**, nenhum deles decidido por LLM.

| Mecanismo | Onde | Para quê | No v1 |
|---|---|---|---|
| `interrupt()` do LangGraph | Nós travados dos funis de ticket e CRUD | Uma pergunta por nó, com validador e re-pergunta. O estado vive no checkpoint em Redis, então a conversa sobrevive a troca de processo. | **Fora** — ticket e CRUD saem do v1 |
| Máquina de estado em Redis | `hitl:session:{id}`, autenticação do SIGAA | CPF e senha, que não podem passar pelo checkpoint | **Fora** — SIGAA sai do v1 |
| Handoff terminal | `handoff:session:{id}`, TTL 24h | O usuário pede uma pessoa. O bot silencia a sessão, enfileira em `handoff:queue` e avisa a equipe. | **Ativo — é o único HITL do v1** |

**Um detalhe de implementação que não é acidental:** cada funil tem *um
`interrupt()` por nó*, e não vários no mesmo nó. O pacote
`langgraph-checkpoint-redis` tem um bug conhecido com múltiplos interrupts
pendentes no mesmo nó — funciona no primeiro resume e quebra no segundo.
Por isso os funis são uma sequência de nós travados, um por pergunta.

**No v1, o handoff é a saída de emergência do usuário.** Ele funciona de
qualquer tela, inclusive no meio de um convite para escrever uma pergunta, e
é reconhecido por `0` ou por frases como "quero falar com um atendente". A
detecção é regex, nunca LLM: é a única saída garantida do usuário, então não
pode depender de cota de API nem de acerto de classificador.

**E o bot precisa conseguir voltar.** A pausa dura 24 horas e termina sozinha,
mas há duas formas de encerrar antes: a página **Atendimento humano**
(`/hub/handoffs`) no painel, com um botão por conversa, ou `$voltar <jid>`
pelo WhatsApp. A página existe porque a segunda opção não alcança quem testou
pelo simulador de chat — e uma conversa sem saída é pior que não ter pausa.

---

## 12. Plataforma Web Admin (Hub v2)

Em `http://localhost:9000/hub/`. Login por `ADMIN_USERNAME` e
`ADMIN_PASSWORD` no `.env`.

> Reescrita em 2026-09-09 (item A3). A lista anterior tinha nove páginas e
> era do Hub antigo, anterior à ADR 0007. Hoje são cerca de vinte rotas.

**Stack:** Jinja2 + HTMX + Alpine.js vendorados, **sem build step** (ADR
0007). Regra dura do design system: nenhuma página imprime identificador de
código, tabela ou migration fora de `data-tech`/tooltip — travado por
`tests/unit/hub/test_no_backend_jargon.py`.

### Operação do dia a dia

| URL | O que faz |
|---|---|
| `/hub/` | Dashboard com status dos serviços |
| `/hub/chat` | Simulador de conversa — testa o agente sem WhatsApp |
| `/hub/users` | Usuários cadastrados |
| `/hub/audit` | Log de ações administrativas |
| `/hub/config` | Configuração dinâmica (sem restart) |

### Agentes, rotas e custo

| URL | O que faz |
|---|---|
| `/hub/agents` | Liga e desliga agentes (circuit-breaker), prompt e provider por agente |
| `/hub/routes` | Rotas do `route_registry` — `doc_type`, `k`, cacheável |
| `/hub/llm-custo` | **Custo por provider e por rota**, cotação do dólar, reset de circuito. É a observabilidade oficial do v1 |
| `/hub/providers` | Cadastra provider compatível com a API da OpenAI, sem deploy. A chave fica no `.env`, nunca no banco |

### Conhecimento e qualidade

| URL | O que faz |
|---|---|
| `/hub/chunkviz` | Upload de documento, simulação de chunking e ingestão |
| `/hub/eval` | Avaliação da qualidade do RAG |
| `/hub/infra/search` | Índices RediSearch, contagem de chunks, teste de busca |
| `/hub/infra/storage` · `/hub/infra/health` | Uso do Redis e saúde dos serviços |

### Grafo e ferramentas

| URL | O que faz | Afeta produção? |
|---|---|---|
| `/hub/graph-studio` — aba "Grafo de produção" | Diagrama da `GraphSpec` ativa, criação de rota nova, histórico e revert | **Sim**, no próximo restart dos workers |
| `/hub/graph-studio` — aba "Laboratório" | Canvas de componentes isolados | Não, nunca foi o grafo de produção |
| `/hub/graph-nodes` | Catálogo de componentes do `graph_studio` | Não (congelado, TD-020) |
| `/hub/capabilities` · `/hub/mcp-servers` | Ferramentas HTTP/MCP cadastradas pelo painel | Sim |
| `/hub/channels` | Conecta instância existente da Evolution API | Parcial — o caminho quente ainda lê `settings.EVOLUTION_*` |

### Menu do bot

| URL | O que faz | Afeta produção? |
|---|---|---|
| `/hub/menu` | Edita as telas do bot: texto de abertura, opções numeradas e respostas prontas. Mostra a pré-visualização exata do que a pessoa recebe no WhatsApp, com histórico e reverter | **Sim, na hora** — a próxima mensagem já usa o texto novo, sem restart |

É a página configurável que mais importa no v1: mudar o que o assistente
responde deixou de exigir deploy. O que está gravado no banco vence o texto
que veio com o código; "Restaurar o texto original" volta ao embutido sem
perder o histórico.

### Ferramentas externas

| Ferramenta | URL padrão | Observação |
|---|---|---|
| Prometheus | `localhost:9090` | Coleta a API e o Redis, **não os workers** — ver §16 |
| Grafana | `localhost:3001` | Provisionamento historicamente não montado |

---

## 13. Configuração e Variáveis de Ambiente

Copiar `.env.example` para `.env` e preencher:

```bash
# ── Banco de Dados ─────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://user:SENHA@postgres:5432/oraculo
POSTGRES_PASSWORD=sua_senha_aqui

# ── Redis ──────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ── LLM (Google Gemini) ────────────────────────────────
GEMINI_API_KEY=sua_chave_gemini_aqui
GEMINI_MODEL=gemini-2.0-flash-lite      # modelo padrão (barato e rápido)
GEMINI_TEMP=0.2                         # 0=determinístico, 1=criativo
GEMINI_MAX_TOKENS=1024

# ── Embeddings ─────────────────────────────────────────
EMBEDDING_PROVIDER=google               # google ou local (BAAI/bge-m3)
# EMBEDDING_PROVIDER=local              # use local se não tiver API key

# ── WhatsApp (Evolution API) ───────────────────────────
EVOLUTION_BASE_URL=http://evolution:8080
EVOLUTION_API_KEY=sua_chave_evolution
EVOLUTION_INSTANCE_NAME=OraculoUEMA
WHATSAPP_HOOK_URL=http://api:9000/webhook/evolution

# ── Admin ──────────────────────────────────────────────
ADMIN_USERNAME=admin                    # login do portal web
ADMIN_PASSWORD=senha_forte_aqui        # OBRIGATÓRIO em produção!
ADMIN_JWT_SECRET=segredo_jwt_32chars   # OBRIGATÓRIO em produção!
ADMIN_API_KEY=chave_api_admin
ADMIN_NUMBERS=5598999999999            # números autorizados como admin (WhatsApp)
ADMIN_CONFIRMATION_TOKEN=token_extra   # token extra para comandos críticos

# ── Modo de desenvolvimento ────────────────────────────
DEV_MODE=True                          # False em produção
DEV_WHITELIST=5598999999999            # só esses números recebem resposta em dev

# ── Dados ──────────────────────────────────────────────
DATA_DIR=/app/dados                    # pasta dos PDFs
PDF_PARSER=pymupdf                     # pymupdf, docling, marker

# ── Segurança extra (opcional) ─────────────────────────
LLAMA_CLOUD_API_KEY=                   # para LlamaParse (PDFs difíceis)
HF_TOKEN=                              # para modelos HuggingFace privados
```

---

## 14. Instalação e Execução (Docker)

### Pré-requisitos
- Docker 24+
- Docker Compose 2+
- 4GB RAM mínimo (8GB recomendado)
- Conta Google Cloud com Gemini API habilitada

### 1. Clonar e configurar

```bash
git clone https://github.com/seu-repo/oraculo-uema.git
cd oraculo-uema
cp .env.example .env
# Editar .env com suas credenciais
```

### 2. Criar pasta de dados

```bash
mkdir -p dados/uploads
```

### 3. Subir os serviços

```bash
# Primeira vez (constrói as imagens)
docker compose up --build -d

# Verificar se está tudo rodando
docker compose ps

# Ver logs em tempo real
docker compose logs -f api worker
```

### 4. Verificar saúde

```bash
# Health check da API
curl http://localhost:9000/health

# Deve retornar:
# {"status": "online", "redis_ok": true, "chain_ok": true}
```

### 5. Acessar o portal admin

Abrir no navegador: `http://localhost:9000/hub/`

Login com as credenciais definidas em `ADMIN_USERNAME` e `ADMIN_PASSWORD`.

### Comandos Docker úteis

```bash
# Parar tudo
docker compose down

# Parar e apagar volumes (CUIDADO: apaga dados!)
docker compose down -v

# Reconstruir apenas a API
docker compose up --build -d api

# Ver logs de um serviço específico
docker compose logs -f worker

# Executar comando dentro do container
docker compose exec api python -c "from src.main import app; print('OK')"

# Rodar migrations do banco
docker compose run --rm migration
```

---

## 15. Celery — Tarefas em Background

O Celery processa tarefas que demoram mais do que uma request HTTP deve aguardar.

### Workers ativos

```bash
# Ver workers rodando
docker compose logs -f worker
# Deve mostrar: "celery@xxx ready."
```

### Filas disponíveis

> ⚠️ Corrigido em 2026-08-25 — a fila `notificacoes` nunca existiu de fato;
> lembretes de prazos rodam na fila `default` via `beat`. Tabela abaixo
> reflete `celery_app.py` real.

| Fila | Container | Para que serve |
|---|---|---|
| `default` | `worker` | Processar mensagens WhatsApp, comandos admin, notificações agendadas |
| `admin` | `worker` | Ingestão de documentos, comandos admin |
| `rag_search` | `worker_rag` | Busca híbrida Redis + rerank |
| `synthesis` | `worker_synthesis` | Geração da resposta final (LLM) |
| `media` | `worker_media` | Download/transcrição/síntese de áudio e vídeo (STT/TTS, YouTube, Instagram) |
| `graph` | — | Extração de grafo institucional — código existe, **worker desligado** desde 2026-07-31 (sem uso real em produção) |

### Beat (tarefas agendadas)

O `beat` (agendador) executa tarefas periodicamente:

| Tarefa | Horário | O que faz |
|---|---|---|
| `verificar_e_notificar_prazos` | 08h (seg-sex) + 09h (sáb-dom) | Notifica alunos sobre prazos próximos |
| `stream_recovery` | A cada 5 min | Recupera mensagens perdidas (Redis Streams) |

---

## 16. Observabilidade

> ⚠️ **Leia isto antes do resto da seção** (atualizado em 2026-09-09, item
> A3/B7). O `prometheus.yml` coleta três alvos: a API, o `redis_exporter` e o
> próprio Prometheus. **Os workers Celery não são coletados** — não expõem
> `/metrics` e não estão em nenhum `scrape_config`. Como quase todo
> `oraculo_*` é emitido dentro de um worker, **a lista de métricas mais
> abaixo não chega ao Prometheus hoje** (TD-019).
>
> A observabilidade que **funciona de verdade** no v1 é:
>
> * **`/hub/llm-custo`** — custo, tokens e cache por provider e por rota,
>   lendo a tabela `metricas_llm` no Postgres. Independe do Prometheus.
> * **`/hub/infra/{health,storage,search}`** — saúde dos serviços, uso do
>   Redis e estado dos índices.
> * **Alerta de provider fora do ar** — o circuit-breaker manda WhatsApp para
>   `SUPPORT_GROUP_JID`. É o único alerta que chega a uma pessoa.
>
> O `alert_rules.yml` foi reescrito: dos sete alertas, seis consultavam
> métricas inexistentes. Sobraram três, todos de infraestrutura.

Não usamos Langfuse/LangSmith — avaliado e descartado (ver
`pesquisa_arquitetura_producao.md` §4.5): a telemetria de custo/rota/cache
já roda nativamente em Postgres + Prometheus, sem depender de ferramenta
externa.

### `/hub/llm-custo` — custo por provider/rota (Postgres, nativo)

Acesse pelo portal admin. Mostra custo USD/BRL, tokens e chamadas por
provider (Gemini/DeepSeek/Groq) e por rota, além de hit rate do cache
semântico — lê a tabela `metricas_llm`, alimentada por
`MonitoredLLMProvider` (`src/infrastructure/adapters/llm_factory.py`) em
toda chamada de geração de texto.

### Prometheus — Métricas

Acesse: `http://localhost:9090`

Métricas **emitidas pelo código** (a maioria dentro de workers, portanto
hoje não coletadas — ver o aviso no topo desta seção):
oraculo_requests_total                    → total de mensagens processadas
oraculo_requests_blocked_total            → bloqueadas pelo Porteiro
oraculo_semantic_cache_result_total       → hit/miss do cache semântico, por rota
oraculo_router_cache_hit_total            → qual camada do Supervisor decidiu a rota
oraculo_llm_cost_usd_total                → custo acumulado por provider
oraculo_memory_layer_access_total         → acesso por camada de memória (L1-L4)
oraculo_request_latency_ms                → histograma de latência
oraculo_db_latency_ms                     → latência do PostgreSQL

### Grafana — Dashboards

Acesse: `http://localhost:3001`
Login padrão: `admin` / `admin` (via `.env` `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`)

Dashboards versionados em `observability/grafana/provisioning/dashboards/`:

- `llm_custo_providers.json` — custo/tokens/cache por provider
- `comportamento_ia.json` — roteamento, memória, falhas do pipeline

⚠️ Estes dashboards consultam métricas emitidas em workers, que o Prometheus
não coleta. Para custo real, use `/hub/llm-custo`, que lê o Postgres.

### Jaeger — Tracing distribuído (OpenTelemetry)

Acesse: `http://localhost:16686`. Desativado por padrão — precisa de
`ENABLE_TRACING=true` no `.env` (o container `jaeger` já sobe com o profile
`monitoring`, mas sem a flag ligada o SDK do OpenTelemetry nem tenta
exportar). Correlaciona uma mensagem completa (FastAPI → Celery → chamadas
Gemini/STT/TTS) num trace só, com atributos `gen_ai.*` (convenção semântica
oficial). Instrumentação manual nos mesmos pontos-único já usados pra custo
(`llm_factory.py`, `audio_service.py`) — Celery não usa auto-instrumentação
de propósito (signals customizados de event loop, ver `celery_app.py`).

---

## 17. Testes

```bash
# Todos os testes unitários (sem necessidade de Redis ou banco)
docker compose exec api pytest tests/unit/ -v

# Testes de integração (requer Redis rodando)
docker compose exec api pytest tests/integration/ -v -m integration

# Teste específico
docker compose exec api pytest tests/unit/test_registration_service.py -v

# Com cobertura
docker compose exec api pytest tests/unit/ --cov=src --cov-report=term-missing
```

### Estrutura de testes
tests/
├── unit/           # Sem IO. Sem banco. Puro Python.
│   ├── domain/     # Entidades, validações
│   └── application/# Use cases com mocks
├── integration/    # Requer Redis local
└── e2e/            # Requer servidor rodando

---

## 18. Comandos Úteis

### Comandos via WhatsApp (apenas admin)

| Comando | O que faz |
|---|---|
| `!status` | Mostra saúde do sistema (Redis, manutenção, API) |
| `!ban 5598...` | Bane um número |
| `!unban 5598...` | Desbane um número |
| `!prompt <texto>` | Altera o system prompt global |
| `!prompt reset` | Restaura o prompt padrão |
| `!manutencao on` | Ativa modo manutenção (bloqueia todos os usuários) |
| `!manutencao off` | Desativa manutenção |
| `!cache clear` | Limpa o cache semântico |
| `!audit 10` | Mostra as últimas 10 ações no log |

### Comandos administrativos (via portal web `/hub/config`)

- Alterar system prompt
- Ativar/desativar manutenção
- Limpar cache semântico
- Verificar status dos workers Celery

### Scripts Python úteis

```bash
# Reiniciar índices Redis (usar com cautela!)
docker compose exec api python -c "
import asyncio
from src.infrastructure.redis_client import inicializar_indices
asyncio.run(inicializar_indices())
"

# Ingerir todos os PDFs da pasta dados/
docker compose exec api python -c "
from src.rag.ingestion.pipeline import IngestionPipeline
p = IngestionPipeline.build_auto('dados/edital_paes_2026.pdf', 'edital')
result = p.run('dados/edital_paes_2026.pdf', 'edital')
print(result)
"

# Verificar chunks no Redis
docker compose exec api python -c "
from src.infrastructure.redis_client import get_redis, PREFIX_CHUNKS
r = get_redis()
_, keys = r.scan(0, match=f'{PREFIX_CHUNKS}*', count=100)
print(f'{len(keys)} chunks no Redis')
"
```

---

## 19. Glossário para Leigos

| Termo | O que significa na prática |
|---|---|
| **LLM** | Large Language Model — a "IA" que gera texto (ex: ChatGPT, Gemini) |
| **RAG** | O sistema que busca documentos antes de responder (evita invenções) |
| **Embedding** | Transformar texto em números para que o computador possa comparar textos por significado |
| **Chunk** | Pedaço de documento (~400 letras). O PDF é dividido em muitos chunks |
| **Vector Store** | Banco de dados especial que armazena embeddings e faz buscas por significado |
| **Redis** | Banco de dados ultra-rápido em memória. Aqui guarda tudo: cache, estado, filas |
| **Celery** | Sistema que processa tarefas demoradas em segundo plano (fila de trabalho) |
| **Webhook** | URL que recebe notificações automáticas (WhatsApp nos avisa quando chega mensagem) |
| **HITL** | Pausa no processo para o humano confirmar uma ação importante |
| **RBAC** | Sistema de permissões: cada usuário só acessa o que tem direito |
| **Token** | Unidade de texto para o LLM (~0.75 palavras). Cada token tem custo $$ |
| **CRAG** | Avaliação automática de quanto o que foi encontrado é relevante para a pergunta |
| **BM25** | Algoritmo clássico de busca por palavras-chave (como o Google dos anos 90) |
| **FastAPI** | Framework Python para criar APIs web rapidamente |
| **Docker** | "Contêiner" que empacota o sistema inteiro para rodar em qualquer máquina |
| **Alembic** | Sistema de versionamento do banco de dados (controla mudanças na estrutura) |
| **Evolution API** | Software que conecta o sistema ao WhatsApp Business |
| **Prometheus** | Coleta métricas do sistema (quantas mensagens, latência, erros) |
| **Grafana** | Cria gráficos bonitos com as métricas do Prometheus |

---

## Contribuindo

1. Fork o repositório
2. Crie uma branch: `git checkout -b feat/minha-feature`
3. Faça commits pequenos e descritivos
4. Rode os testes: `pytest tests/unit/ -v`
5. Abra um Pull Request

---

## Licença

Projeto institucional UEMA — uso interno. Entre em contato com o CTIC para mais informações.

---

*Documentação mantida pelo CTIC/UEMA. Última atualização: 25 de agosto de 2026
(reorganização documental — ver `docs/README.md` para o mapa completo da
documentação e `notas.md` §15 para o histórico da mudança).*