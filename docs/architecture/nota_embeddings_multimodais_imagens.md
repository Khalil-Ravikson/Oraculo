# Nota — Embeddings Multimodais para Imagens (DokuWiki Scraping)

> **Status: Ideia registrada, não implementada.** Anotado em 2026-08-28 a
> pedido do dono, pra não perder o contexto quando isso virar prioridade.

## Problema atual

O scraping do DokuWiki (`ingestion/pipeline.py` e afins) processa texto das
páginas mas **ignora imagens completamente** — nenhuma indexação, nenhum
entendimento visual. Confirmado em `dispatcher_langgraph.py`: mídia sem
legenda enviada pelo usuário retorna "ainda não consigo analisar
imagens/vídeos/documentos" (Vision não implementado, Fase 4/5 do plano
original).

Isso é uma lacuna real: páginas de wiki institucional frequentemente têm
diagramas, fluxogramas, prints de tela de sistemas (SIGAA, SIPAC) que
carregam informação que o texto ao redor não repete.

## Proposta: Vertex AI Multimodal Embeddings

Fonte trazida pelo dono: [Google Cloud — Get multimodal embeddings](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/embeddings/get-multimodal-embeddings?hl=pt-br)

Modelo `multimodalembedding@001` (Vertex AI) coloca **texto e imagem no
mesmo espaço vetorial** (1408 dimensões) — permite:
- Indexar imagens de páginas wiki durante o scraping.
- Buscar imagens usando query em **texto puro** (sem precisar OCR prévio
  nem legenda manual) — busca cross-modal de verdade.
- Também aceita vídeo, se algum dia for relevante (fora de escopo hoje).

## Trade-off técnico (o que muda na arquitetura)

| Aspecto | Hoje (texto) | Com multimodal (imagem) |
|---|---|---|
| Modelo | `gemini-embedding-001` (Google) | `multimodalembedding@001` (Vertex AI — API/billing diferente do `google.genai` usado hoje) |
| Dimensão | 3072d | 1408d |
| Índice Redis | `idx:rag:chunks` (HNSW, 3072d) | Precisa índice **separado**: `idx:rag:images` (1408d) — dimensões diferentes não cabem no mesmo índice |
| Custo | Já contabilizado em `pricing.py` | Novo — precisa entrar na telemetria de custo (`metricas_llm`) como rota `"embedding_image"` |
| Auth | `google.genai` (mesma chave Gemini) | Vertex AI usa autenticação de GCP (service account / ADC) — **não é a mesma chave de API do Gemini direto**, requer setup de credencial adicional |

## Onde isso se encaixaria na arquitetura (Camada 1 / Fase 6)

Com `BaseNode` já existindo (`src/graph/base_node.py`), a extensão natural
seria um `ImageEmbeddingsNode` (ou estender `EmbeddingsNode` com um modo
`image_bytes` além de `texts`/`query`) — mesmo padrão dos nós de Fase 6
(STT/TTS/Embeddings, implementados em 2026-08-28):

```
input_ports:  image_bytes (novo PortType.IMAGE, ou reusar AUDIO/FILE genérico)
output_ports: embedding (PortType.EMBEDDINGS, 1408d — diferente do embedding de texto)
```

**Pipeline de ingestão** precisaria de um passo novo: durante o scraping do
DokuWiki, quando a página tem `<img>`, baixar a imagem, gerar embedding via
esse node, salvar em `idx:rag:images` com metadados (`page_url`, `alt_text`,
`sistema`/`setor` — reaproveitando a taxonomia que os chunks de texto já
usam).

**Busca**: RAG híbrido atual (BM25 + HNSW texto + RRF + reranker) ganharia
um terceiro braço opcional — busca na `idx:rag:images` usando o mesmo
embedding de texto da query (já que texto e imagem compartilham espaço),
com resultado mesclado ou apresentado separado ("encontrei este diagrama
relacionado").

## Decisão

**Não implementar agora.** Motivos:
1. Sem demanda concreta ainda confirmada (é possibilidade levantada, não
   pedido de feature).
2. Requer setup de credencial GCP separado do fluxo `google.genai` atual —
   não é "trocar 1 linha de config".
3. Fase 6 (STT/TTS/Embeddings como `BaseNode`) acabou de ser concluída
   (2026-08-28) — extensão de imagem é natural continuação, não bloqueio.

**Gatilho de reavaliação**: pedido concreto de indexar/buscar conteúdo
visual do DokuWiki, ou demanda de um caso de uso real (ex: "mostra o
fluxograma de matrícula").

## Referências

- [Google Cloud — Get multimodal embeddings](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/embeddings/get-multimodal-embeddings?hl=pt-br)
- `src/graph/base_node.py`, `src/graph/nodes/embeddings_node.py` (padrão a seguir)
- `src/infrastructure/redis_client.py` (VECTOR_DIM=3072, índice atual)
- `docs/historico/fases_6_11_langgraph_studio.md` (roadmap de Fase 6+)
