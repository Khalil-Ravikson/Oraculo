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
16. [Observabilidade (Langfuse + Prometheus + Grafana)](#16-observabilidade-langfuse--prometheus--grafana)
17. [Testes](#17-testes)
18. [Comandos Úteis](#18-comandos-úteis)
19. [Glossário para Leigos](#19-glossário-para-leigos)

---

## 1. O que é o Oráculo?

O **Oráculo UEMA** é um agente inteligente universitário. Pense nele como um
assistente 24h que responde perguntas dos alunos, professores e funcionários da
UEMA pelo WhatsApp — sem precisar ligar para a secretaria, sem esperar atendimento.

**O que ele sabe responder?**

- 📅 Datas do calendário acadêmico (matrícula, provas, feriados, início de semestre)
- 📋 Informações do edital PAES 2026 (vagas, cotas, procedimentos)
- 📞 Contatos de departamentos (PROG, CTIC, CECEN, reitoria)
- 💻 Suporte técnico e sistemas (SIGAA, senha, Wi-Fi)
- 📂 Qualquer documento que o administrador ingira no sistema

**O que ele NÃO faz (por segurança)?**

- Não executa ações críticas sem confirmação humana (ex: alterar cadastro)
- Não responde perguntas fora do contexto da UEMA
- Não atende usuários não cadastrados ou inativos

---

## 2. Visão Geral da Arquitetura
WhatsApp (usuário)
│
▼
Evolution API ──→ Webhook FastAPI
│
▼
[PORTEIRO PostgreSQL]
Valida: telefone, status, role
│
▼
[LOCK Redis]
Anti-spam, anti-duplicação
│
▼
Celery Worker (background)
│
├─── OracleChain ──→ LangChain Runnables
│         │
│         ├── load_memory  (Redis)
│         ├── route_intent (KNN Redis + regex)
│         ├── transform_query
│         ├── retrieve     (busca híbrida Redis)
│         ├── grade_docs   (CRAG score)
│         ├── generate     (Gemini LLM)
│         └── save_memory  (Redis)
│
└─── resposta → Evolution API → WhatsApp


**Clean Architecture** — o código segue 4 camadas:
domain/       → regras de negócio puras (sem frameworks)
application/  → casos de uso (orquestra o domínio)
infrastructure/ → banco, redis, LLM, WhatsApp (detalhes técnicos)
api/          → endpoints FastAPI (apresentação)

---

## 3. Stack de Tecnologias

| Componente | Tecnologia | Para que serve |
|---|---|---|
| Linguagem | Python 3.11 | Toda a aplicação |
| API Web | FastAPI | Webhook WhatsApp + portal admin |
| LLM | Google Gemini | Geração de respostas |
| Framework LLM | LangChain Runnables | Pipeline de RAG |
| Banco relacional | PostgreSQL | Usuários, identidade, auditoria |
| Banco vetorial | Redis Stack + RedisVL | Documentos, embeddings, buscas semânticas |
| Fila de tarefas | Celery + Redis | Processar mensagens em background |
| Canal WhatsApp | Evolution API | Recebe/envia mensagens |
| ORM | SQLAlchemy Async | Interface com PostgreSQL |
| Migrações | Alembic | Versionamento do banco |
| Tracing LLM | Langfuse | Monitorar chamadas ao Gemini |
| Métricas | Prometheus | Coleta de dados de performance |
| Dashboards | Grafana | Visualização de métricas |
| Containers | Docker + Compose | Deploy e orquestração |

---

## 4. Estrutura de Pastas
oraculo-uema/
├── src/
│   ├── api/                    # Endpoints FastAPI (camada de apresentação)
│   │   ├── hub.py              # Portal web admin (rotas HTML)
│   │   ├── admin_api.py        # REST API do admin (JSON)
│   │   ├── rag_admin.py        # Gerenciamento de documentos RAG
│   │   ├── eval_dashboard.py   # Dashboard de avaliação RAG (SSE)
│   │   ├── admin_portal.py     # Portal admin completo
│   │   ├── monitor.py          # Monitor live (SSE)
│   │   ├── metrics.py          # Endpoint Prometheus
│   │   └── middleware/
│   │       └── auth_middleware.py  # JWT admin
│   │
│   ├── application/            # Casos de uso (orquestra domínio)
│   │   ├── chain/
│   │   │   └── oracle_chain.py # Pipeline RAG principal (LangChain)
│   │   ├── tasks/
│   │   │   ├── process_message_task.py  # Task Celery: processa mensagem
│   │   │   ├── tasks_admin.py           # Tasks admin (ingestão, comandos)
│   │   │   └── ingestion_tasks.py       # Task Celery: ingere documentos
│   │   └── use_cases/          # Casos de uso específicos
│   │
│   ├── domain/                 # Regras de negócio puras
│   │   ├── entities/           # Modelos do domínio
│   │   ├── ports/              # Interfaces (contratos)
│   │   ├── tools/              # Tools do agente (GLPI, email, etc.)
│   │   └── services/           # Serviços de domínio (roteador, permissões)
│   │
│   ├── infrastructure/         # Detalhes técnicos (banco, redis, LLM)
│   │   ├── adapters/           # Implementações dos ports
│   │   │   ├── gemini_provider.py      # Adapter Gemini
│   │   │   ├── evolution_adapter.py    # Adapter WhatsApp
│   │   │   └── parsers/        # Parsers de documentos (PDF, DOCX, etc.)
│   │   ├── redis_client.py     # Cliente Redis + schemas RedisVL
│   │   ├── settings.py         # Configurações (.env)
│   │   ├── celery_app.py       # Configuração Celery
│   │   ├── logging_config.py   # Logging estruturado
│   │   └── observability/      # Prometheus, Langfuse
│   │
│   ├── memory/                 # Sistema de memória em múltiplas camadas
│   │   ├── ports/              # Interfaces de memória
│   │   ├── adapters/           # Implementações Redis
│   │   └── services/           # MemoryService (orquestra)
│   │
│   ├── rag/                    # Sistema RAG (busca em documentos)
│   │   ├── embeddings.py       # Modelo de embeddings (Gemini/local)
│   │   ├── ingestion/          # Pipeline de ingestão de documentos
│   │   │   ├── pipeline.py     # Pipeline principal
│   │   │   ├── parser_factory.py    # Fábrica de parsers
│   │   │   └── chunker_factory.py   # Fábrica de chunkers
│   │   └── query/              # Transformação e roteamento de queries
│   │
│   └── main.py                 # Entry point da aplicação
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

### 5.1 Uma mensagem do WhatsApp, passo a passo
USUÁRIO envia mensagem via WhatsApp
"quando é a matrícula de veteranos?"
EVOLUTION API recebe e envia para o webhook:
POST /webhook/evolution  { phone: "5598...", text: "quando é..." }
PORTEIRO (PostgreSQL):
→ busca o telefone no banco
→ verifica: está cadastrado? status=ativo?
→ se não: BLOQUEIA (sem gastar tokens do LLM!)
→ se sim: monta IdentidadeRica { nome, curso, role, ... }
LOCK (Redis):
→ cria lock:5598... no Redis (TTL 90s)
→ evita processar duas mensagens ao mesmo tempo do mesmo usuário
→ se já está bloqueado E mensagem é inútil (ok, 👍): ignora silenciosamente
CELERY (background task):
→ webhook retorna 200 imediatamente
→ task processar_mensagem executa em background
ORACLECHAIN (pipeline RAG):
a) load_memory
→ carrega histórico dos últimos 5 turnos do Redis
→ carrega fatos de longo prazo do usuário
b) route_intent
→ regex rápido: "matrícula" → CALENDARIO (conf=0.90)
→ se confiança baixa: KNN semântico no Redis
c) transform_query
→ enriquece com contexto: "matrícula veteranos 2026.1 CECEN"
d) retrieve (busca híbrida)
→ BM25 (busca por palavras-chave exatas)
→ Vector (busca semântica via embeddings)
→ RRF (fusão dos dois rankings)
→ filtra por metadata (source=calendario-2026.pdf)
e) grade_docs (CRAG)
→ avalia qualidade dos chunks encontrados
→ score 0.0 a 1.0
→ se score baixo: tenta busca mais ampla
f) generate (Gemini)
→ monta prompt com contexto + histórico + fatos
→ chama Gemini (gemini-2.0-flash ou similar)
→ verifica se LLM quer chamar alguma tool
→ se tool crítica: ativa HITL (aguarda confirmação)
g) save_memory
→ salva turno no Redis (chat:5598...)
EVOLUTION API:
→ envia a resposta para o WhatsApp do usuário
LOCK liberado (Redis):
→ próxima mensagem do usuário pode ser processada
### 5.2 Diagrama de componentes

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

## 11. HITL — Confirmação Humana

HITL = Human-in-the-Loop = "humano no loop de decisão".

Algumas ações são **irreversíveis ou críticas** (alterar email, abrir chamado formal).
O Oráculo não executa automaticamente — pede confirmação:
Usuário: "quero mudar meu email para joao@aluno.uema.br"
Oráculo: ⚠️ Confirmação necessária
Alterar e-mail para joao@aluno.uema.br
Responda SIM para confirmar ou NÃO para cancelar.
Usuário: "sim"
Oráculo: ✅ E-mail atualizado com sucesso!

**Como funciona tecnicamente:**
1. LLM detecta intenção de ação crítica
2. Salva no Redis: `hitl:{session_id}` com `action`, `args`, `expires_at`
3. Envia mensagem pedindo confirmação
4. Na próxima mensagem, antes de qualquer coisa, verifica Redis
5. Se "sim" → executa a tool; se "não" → cancela; se outra coisa → repete

---

## 12. Plataforma Web Admin

Acessível em: `http://localhost:9000/hub/`

**Login:** usuário e senha definidos em `ADMIN_USERNAME` e `ADMIN_PASSWORD` no `.env`

### Páginas disponíveis

| URL | O que faz |
|---|---|
| `/hub/` | Dashboard principal com cards e status dos serviços |
| `/hub/chat` | Simulador de chat — testa o agente diretamente |
| `/hub/chunkviz` | Upload de documentos e visualização de chunks |
| `/hub/audit` | Log de todas as ações administrativas |
| `/hub/users` | Lista e gerencia usuários cadastrados |
| `/hub/config` | Configuração: prompt, manutenção, cache, workers |
| `/eval/` | Avaliação da qualidade do RAG (métricas técnicas) |
| `/monitor/` | Dashboard ao vivo das conversas |
| `/admin/` | Portal admin completo (configurações avançadas) |

### Ferramentas externas (links rápidos em /hub/config)

| Ferramenta | URL padrão | Para que serve |
|---|---|---|
| Grafana | `localhost:3001` | Dashboards visuais de métricas |
| Prometheus | `localhost:9090` | Raw metrics e alertas |
| Langfuse | `localhost:3000` | Tracing de chamadas ao LLM |

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

# ── Observabilidade ────────────────────────────────────
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_HOST=http://langfuse:3000

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

| Fila | Para que serve |
|---|---|
| `default` | Processar mensagens WhatsApp |
| `admin` | Ingestão de documentos, comandos admin |
| `notificacoes` | Lembretes de prazos acadêmicos |

### Beat (tarefas agendadas)

O `beat` (agendador) executa tarefas periodicamente:

| Tarefa | Horário | O que faz |
|---|---|---|
| `verificar_e_notificar_prazos` | 08h (seg-sex) + 09h (sáb-dom) | Notifica alunos sobre prazos próximos |
| `stream_recovery` | A cada 5 min | Recupera mensagens perdidas (Redis Streams) |

---

## 16. Observabilidade (Langfuse + Prometheus + Grafana)

### Langfuse — Tracing LLM

Acesse: `http://localhost:3000`

O que monitorar:
- Latência de cada chamada ao Gemini
- Custo em tokens (entrada + saída)
- Traces completos do pipeline RAG
- CRAG scores ao longo do tempo

### Prometheus — Métricas

Acesse: `http://localhost:9090`

Métricas coletadas:
oraculo_requests_total          → total de mensagens processadas
oraculo_requests_blocked_total  → bloqueadas pelo Porteiro
oraculo_cache_hits_total        → hits no cache semântico
oraculo_request_latency_ms      → histograma de latência
oraculo_db_latency_ms           → latência do PostgreSQL

### Grafana — Dashboards

Acesse: `http://localhost:3001`  
Login padrão: `admin` / `admin`

Dashboards recomendados:
- Redis Overview (importar da comunidade: #763)
- Custom: tokens/min, latência P95, hit rate de cache

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
| **Langfuse** | Ferramenta para monitorar e debugar chamadas ao LLM |
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

*Documentação mantida pelo CTIC/UEMA. Última atualização: Abril 2026.*