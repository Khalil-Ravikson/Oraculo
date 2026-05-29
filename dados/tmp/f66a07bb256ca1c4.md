# Especificação Técnica: Agente RAG Académico UEMA v5

**Documento:** Especificação Interna de Desenvolvimento  
**Versão:** 5.0  
**Data:** Março de 2026  
**Autor:** CTIC/UEMA — Centro de Tecnologia da Informação e Comunicação  
**Classificação:** Teste Interno — RAG Evaluation Dataset

---

## Resumo Executivo

O **Bot UEMA** é um assistente virtual académico acessível via WhatsApp, desenvolvido para atender alunos, coordenadores e administradores da Universidade Estadual do Maranhão. O sistema utiliza arquitetura RAG (Retrieval-Augmented Generation) com busca híbrida (vectorial + BM25) sobre documentos académicos indexados no Redis Stack.

Este documento serve como **caso de teste primário** para o pipeline RAG, contendo tabelas estruturadas, siglas técnicas e informações hierárquicas que permitem validar a qualidade da ingestão e da recuperação.

---

## 1. Arquitectura do Sistema

### 1.1 Stack Tecnológico

| Componente | Tecnologia | Função |
|---|---|---|
| LLM | Gemini 2.0 Flash | Geração de respostas |
| Embedding | BAAI/bge-m3 (1024 dims) | Vetorização de texto |
| Vector Store | Redis Stack (HNSW) | Busca vectorial |
| BM25 | RediSearch nativo | Busca por keywords exactas |
| Fusão | RRF (Reciprocal Rank Fusion) | Combina vectorial + BM25 |
| Queue | Celery + Redis | Processamento assíncrono |
| Gateway | Evolution API v2.3 | WhatsApp integration |

### 1.2 Métricas de Performance

| Versão | Tokens/msg | Latência média | Alucinações |
|---|---|---|---|
| v2 (LangChain + Groq) | ~4.300 | 500–1500ms | Alta |
| v3 (Gemini + Redis) | ~1.070 | 800–1200ms | Média |
| v4 (Cache + CRAG) | ~750 | 600–1000ms | Baixa |
| v5 (Guardrails + Self-RAG) | ~520 | 400–900ms | Muito baixa |

### 1.3 Thresholds do CRAG (Corrective RAG)

| Threshold | Valor | Acção |
|---|---|---|
| CRAG_THRESHOLD_OK | 0.40 | Contexto bom — gera normalmente |
| CRAG_THRESHOLD_MIN | 0.20 | Contexto fraco — gera com disclaimer |
| Abaixo do mínimo | < 0.20 | Rejeita contexto — responde sem RAG |

---

## 2. Pipeline de Processamento (12 Passos)

### 2.1 Sequência Completa

```
PASSO 0  — Guardrails          (regex/heurística, 0ms, 0 tokens)
PASSO 1  — Working Memory      (Redis LPUSH/LRANGE, <1ms)
PASSO 2  — Long-Term Facts     (Redis KNN, ~3ms)
PASSO 3  — Semantic Routing    (Redis KNN, ~1ms, 0 tokens)
PASSO 4  — Self-RAG Decision   (heurística 0ms OU Gemini ~80 tokens)
PASSO 5  — Query Transform     (Gemini ~120 tokens, condicional)
PASSO 5.5— Cache CHECK         (Redis FLAT, ~3ms, 0 tokens)
PASSO 6  — Hybrid Retrieval    (BM25 + Vector → RRF, ~5ms)
PASSO 6.5— CRAG Evaluation     (score RRF médio + step-back)
PASSO 7  — Gemini Generation   (~950 tokens, 1 chamada)
PASSO 8  — Memory Persist      (Redis, <1ms)
PASSO 8.5— Cache STORE         (condicional: só se CRAG score ≥ 0.40)
PASSO 9  — Background Extract  (daemon thread, não bloqueia)
```

### 2.2 Economia de Tokens por Passo

| Passo | Tokens economizados | Mecanismo |
|---|---|---|
| Guardrails (P0) | ~300/dia | Short-circuit em saudações |
| Self-RAG skip (P4) | ~950/query | Não chama retriever nem Gemini |
| Cache hit (P5.5) | ~1.070/query | Resposta reutilizada sem LLM |
| CRAG step-back (P6.5) | -~120 extra | Custo da segunda busca |

---

## 3. Hierarquia de Acesso (RBAC)

### 3.1 Níveis de Permissão

| Nível | Código | Rate Limit | Tools Disponíveis |
|---|---|---|---|
| Visitante | GUEST | 10/min, 50/hora | RAG (leitura) |
| Aluno | STUDENT | 30/min, 200/hora | RAG + GLPI |
| Administrador | ADMIN | Ilimitado | Todas + Comandos Admin |

### 3.2 Comandos Admin via WhatsApp

| Comando | Acção | Assíncrono |
|---|---|---|
| `!status` | Estado do sistema | Não |
| `!tools` | Lista tools registadas | Não |
| `!limpar_cache` | Invalida Semantic Cache | Sim (Celery) |
| `!ingerir` | Ingere ficheiro anexado | Sim (Celery) |
| `!ragas` | Exporta logs para dataset RAGAS | Sim (Celery) |
| `!fatos [user_id]` | Lista fatos long-term | Não |
| `!reload` | Reinicia AgentCore | Sim (Celery) |

---

## 4. Formatos de Documentos Suportados

### 4.1 Tabela de Parsers

| Extensão | Parser | Custo | Qualidade em Tabelas |
|---|---|---|---|
| .pdf | LlamaParse (cloud) | $0.003/pág | Excelente |
| .pdf | PyMuPDF (local) | $0 | Boa |
| .csv | pandas | $0 | Perfeita |
| .docx | python-docx | $0 | Muito boa |
| .xlsx | openpyxl | $0 | Muito boa |
| .txt / .md | leitura directa | $0 | N/A |
| .html | BeautifulSoup | $0 | Boa |

### 4.2 Recomendação por Tipo de Conteúdo

| Conteúdo | Formato Recomendado | Motivo |
|---|---|---|
| Tabelas de vagas/cotas | CSV | BM25 encontra "AC: 40" exacto |
| Calendário académico (tabelas) | CSV | Datas exactas sem alucinação |
| Regras textuais (editais) | PDF + LlamaParse | Preserva estrutura |
| Guias e manuais | DOCX | Preserva headings |
| FAQ e documentação TI | Markdown | Headers para chunking |
| Wiki institucional | Scraper + Markdown | Auto-actualização |

---

## 5. Configuração dos Índices Redis

### 5.1 Parâmetros HNSW (Chunks RAG)

| Parâmetro | Valor | Justificativa |
|---|---|---|
| VECTOR_DIM | 1024 | Dimensão BAAI/bge-m3 |
| M | 16 | Links por nó (recall vs RAM) |
| EF_CONSTRUCTION | 200 | Qualidade do grafo na ingestão |
| DISTANCE_METRIC | COSINE | Embeddings normalizados |

### 5.2 Parâmetros FLAT (Tools e Semantic Cache)

| Índice | Algoritmo | Motivo |
|---|---|---|
| IDX_TOOLS | FLAT | < 10 tools → busca exacta eficiente |
| IDX_SEMANTIC_CACHE | FLAT | Dataset pequeno (<1000 entradas) |

---

## 6. Estrutura de Pastas de Dados

```
dados/
├── PDF/
│   ├── academicos/          ← documentos de produção
│   │   ├── calendario-academico-2026.pdf
│   │   ├── edital_paes_2026.pdf
│   │   └── guia_contatos_2025.pdf
│   └── testes/              ← documentos de teste (este ficheiro)
│       ├── agente_rag_uema_spec.md
│       └── instrucoes_uso_agente.md
├── CSV/
│   ├── academicos/          ← CSVs de produção
│   └── testes/              ← CSVs de teste (mock data)
└── uploads/                 ← ficheiros ingeridos via WhatsApp (!ingerir)
```

---

## 7. Perguntas de Referência para RAG Eval

As seguintes perguntas devem ser usadas como `ground_truth` no `rag_eval.py`:

| ID | Pergunta | Resposta Esperada | Fonte |
|---|---|---|---|
| T01 | "Quantas dimensões tem o embedding?" | 1024 | §5.1 |
| T02 | "Qual o threshold CRAG para contexto bom?" | 0.40 | §1.3 |
| T03 | "Quais são os rate limits do nível STUDENT?" | 30/min, 200/hora | §3.1 |
| T04 | "Qual o custo do LlamaParse por página?" | $0.003 | §4.1 |
| T05 | "Quantos passos tem a pipeline do agente?" | 12 passos | §2.1 |
| T06 | "Qual formato é recomendado para tabelas de vagas?" | CSV | §4.2 |
| T07 | "Qual o algoritmo de fusão entre BM25 e vectorial?" | RRF | §1.1 |
| T08 | "Qual o nível de acesso para usar !limpar_cache?" | ADMIN | §3.2 |

---

*Documento gerado para fins de teste do pipeline RAG. Versão 5.0 — Março 2026.*