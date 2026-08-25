# Índice de documentação — Oráculo UEMA

Mapa de qual documento é a **fonte oficial** de cada assunto. Quando dois
documentos parecerem cobrir o mesmo tema, este índice decide qual vale.

| Assunto | Fonte oficial | Observação |
|---|---|---|
| Onboarding, como rodar, glossário | [`README.md`](../README.md) (raiz) | Ponto de partida para quem chega agora |
| Arquitetura técnica (camadas, Redis, Celery, DB, fluxo de mensagem) | [`architecture/arquitetura_oraculo.md`](architecture/arquitetura_oraculo.md) | Revisado 2026-08-25 contra o código real |
| Mapa rápido — "onde fica X?" | [`architecture/system-map.md`](architecture/system-map.md) | Navegação, não duplica a arquitetura |
| Regras de negócio (RBAC, HITL, escopo de agentes) — para liderança não-técnica | [`business/regras_negocio_oraculo.md`](business/regras_negocio_oraculo.md) | Citações `arquivo:linha` do código real |
| Por que uma decisão foi tomada (não só o quê) | [`decisions/`](decisions/) | ADRs — ver índice abaixo |
| Problemas conhecidos, não resolvidos de propósito | [`technical-debt.md`](technical-debt.md) | TD-001 a TD-012, cada um com evidência |
| Contexto/regras operacionais para agentes de IA (Claude) | [`.claude.md`](../.claude.md) (raiz) | Curto de propósito — aponta pra cá quando precisa de detalhe |
| Log cronológico de sessões de engenharia (bugs reais, testes, descobertas) | [`../notas.md`](../notas.md) (raiz) | Cresce por sessão — não é para ficar pequeno |
| Laboratórios de pesquisa (REST, MCP, LangGraph) — não são produção | [`../rest_lab/README.md`](../rest_lab/README.md), [`../mcp_lab/README.md`](../mcp_lab/README.md), [`../langgraph_experiment/README.md`](../langgraph_experiment/README.md) | Cada um remete ao ADR/seção de `notas.md` relevante |
| Como funciona o cliente MCP (protocolo, sessão, tool-call) | [`../mcp_lab/ARQUITETURA.md`](../mcp_lab/ARQUITETURA.md) | Onboarding do próximo servidor MCP |
| Planos/pesquisas já concluídos ou superados | [`historico/`](historico/) | Mantidos como registro, não como estado atual |
| Apresentações, relatórios, exports (pptx/docx/json/htm) | [`assets/`](assets/) | Sem valor de código — organização apenas |

## Decisões arquiteturais (ADRs)

| ADR | Decisão |
|---|---|
| [0001](decisions/0001-langgraph-nao-aprovado-para-main.md) | LangGraph isolado em branch própria, não aprovado para `main` |
| [0002](decisions/0002-tts-kokoro-sobre-piper.md) | TTS local: Kokoro-82M no lugar de Piper (licença) |
| [0003](decisions/0003-sem-s3-cdn-para-midia.md) | Sem S3/CDN para mídia — envio via base64 direto |
| [0004](decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md) | Multi-provider LLM via `ILLMProvider`; roteamento por regex nos laboratórios de pesquisa |
| [Repositórios homologados](decisions/repositorios-homologados.md) | Referências de benchmarking validadas (não é ADR, é material de consulta) |

## Documentos históricos (`historico/`)

Cada um tem um banner de status no topo explicando o que já foi resolvido e
o que ainda é válido — leia o banner antes de confiar no corpo do documento.

- [`PLANO_REFATORACAO_SUPERVISOR.md`](historico/PLANO_REFATORACAO_SUPERVISOR.md) — migração já concluída
- [`analise_custo_real_llm.md`](historico/analise_custo_real_llm.md) — gap de telemetria já fechado
- [`pesquisa_arquitetura_producao.md`](historico/pesquisa_arquitetura_producao.md) — parcialmente superado (CI/CD e telemetria já existem)
- [`notas_regras_negocio_chunkviz.md`](historico/notas_regras_negocio_chunkviz.md) — 2 de 3 itens resolvidos, 1 ainda aberto

## O que NÃO está aqui

- **Documentação de deployment/operações dedicada** — ainda não existe como
  documento próprio; o mais próximo é `README.md` §14-16 (Docker, Celery,
  observabilidade) e as notas operacionais em `.claude.md`.
- **Runbook de troubleshooting** — incidentes reais estão narrados em
  `notas.md`, mas não existe um guia "sintoma → causa → ação" consolidado.
- **Documentação de API** (contratos HTTP do portal `/hub`) — não existe
  fora do próprio código das rotas.

Essas lacunas foram identificadas na auditoria de 2026-08-24 e continuam
abertas — não foram criadas nesta rodada de organização para não inventar
conteúdo sem dono/revisão técnica.
