# Índice de documentação — Oráculo UEMA

Mapa de qual documento é a **fonte oficial** de cada assunto. Quando dois
documentos parecerem cobrir o mesmo tema, este índice decide qual vale.

> Revisado em 2026-09-09 (item A6). O índice anterior listava
> `langgraph_experiment/` como vivo (foi deletado pela ADR 0008) e não
> mencionava as ADRs 0007 e 0008, que são a arquitetura real de hoje.

| Assunto | Fonte oficial | Observação |
|---|---|---|
| **O que o sistema É hoje** — escopo do v1, flags reais, produção × morto, checklist de produção | [`ESTADO_ATUAL.md`](ESTADO_ATUAL.md) | **Vence qualquer outro documento em caso de contradição** |
| Onboarding, como rodar, glossário | [`README.md`](../README.md) (raiz) | Ponto de partida para quem chega agora |
| Arquitetura técnica (camadas, Redis, Celery, DB, fluxo de mensagem) | [`architecture/arquitetura_oraculo.md`](architecture/arquitetura_oraculo.md) | ⚠️ §3/§4.3/§5 **desatualizados** (descrevem o dispatcher e o Planner, deletados pela ADR 0008). Reescrita pendente — item A2 |
| Grafo de produção como dado (`GraphSpec`), Graph Studio | [`architecture/graph-studio.md`](architecture/graph-studio.md) | Atual |
| O que é `src/graph_studio/` — e por que NÃO é o grafo de produção | [`architecture/graph-studio-sandbox.md`](architecture/graph-studio-sandbox.md) | Congelado (TD-020) |
| Mapa rápido — "onde fica X?" | [`architecture/system-map.md`](architecture/system-map.md) | Navegação, não duplica a arquitetura |
| Plano de reorganização de frontend (CSS/JS) e redesign UI/UX do Hub/Admin | [`architecture/plano_frontend_ui_ux.md`](architecture/plano_frontend_ui_ux.md) | Plano técnico por fases, não implementado ainda |
| Regras de negócio (RBAC, HITL, escopo de agentes) — para liderança não-técnica | [`business/regras_negocio_oraculo.md`](business/regras_negocio_oraculo.md) | Citações `arquivo:linha` do código real |
| Por que uma decisão foi tomada (não só o quê) | [`decisions/`](decisions/) | ADRs — ver índice abaixo |
| Problemas conhecidos, não resolvidos de propósito | [`technical-debt.md`](technical-debt.md) | TD-001 a TD-031. Vinte fechadas, cada uma com a evidência e o que a corrigiu |
| Diagnóstico do estado real + plano de recuperação (2026-09-08) | [`diagnostico_2026-09_estado_e_plano.md`](diagnostico_2026-09_estado_e_plano.md) | O laudo que originou esta rodada. **Executado** — o resultado está em [`ESTADO_ATUAL.md`](ESTADO_ATUAL.md), que é a fonte do que vale hoje |
| Contexto/regras operacionais para agentes de IA (Claude) | [`.claude.md`](../.claude.md) (raiz) | Curto de propósito — aponta pra cá quando precisa de detalhe |
| Log cronológico de sessões de engenharia (bugs reais, testes, descobertas) | [`../notas.md`](../notas.md) (raiz) | Cresce por sessão — não é para ficar pequeno |
| Laboratórios de pesquisa (REST, MCP) — não são produção | [`../rest_lab/README.md`](../rest_lab/README.md), [`../mcp_lab/README.md`](../mcp_lab/README.md) | Cada um remete ao ADR/seção de `notas.md` relevante |
| Como funciona o cliente MCP (protocolo, sessão, tool-call) | [`../mcp_lab/ARQUITETURA.md`](../mcp_lab/ARQUITETURA.md) | Onboarding do próximo servidor MCP |
| Planos/pesquisas já concluídos ou superados | [`historico/`](historico/) | Mantidos como registro, não como estado atual |
| Apresentações, relatórios, exports (pptx/docx/json/htm) | [`assets/`](assets/) | Sem valor de código — organização apenas |

## Decisões arquiteturais (ADRs)

| ADR | Decisão |
|---|---|
| [0001](decisions/0001-langgraph-nao-aprovado-para-main.md) | **Substituído** — LangGraph aprovado como dispatcher definitivo (Decisão 01 do plano de integração, 2026-08-25); mantido como registro histórico da rejeição anterior |
| [0002](decisions/0002-tts-kokoro-sobre-piper.md) | TTS local: Kokoro-82M no lugar de Piper (licença) |
| [0003](decisions/0003-sem-s3-cdn-para-midia.md) | Sem S3/CDN para mídia — envio via base64 direto |
| [0004](decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md) | Multi-provider LLM via `ILLMProvider`; roteamento por regex nos laboratórios de pesquisa |
| [0005](decisions/0005-rest-lab-camada-application.md) | `rest_lab` ganha camada de Application (`RestLabUseCase`), continua laboratório de estudo |
| [0006](decisions/0006-mcp-lab-camada-application-e-evolution-adapter.md) | `mcp_lab` ganha camada de Application (`McpLabUseCase`) e para de acessar `EvolutionAdapter` direto |
| [0007](decisions/0007-hub-v2-htmx-alpine-e-registries-dinamicos.md) | Hub v2: HTMX+Alpine sem build step, registries dinâmicos (Postgres + espelho Redis), GraphExecutor MVP atrás de flag |
| [0008](decisions/0008-orquestrador-unico-langgraph.md) | **Orquestrador único** sobre o StateGraph. `dispatcher.py`, o Planner e `langgraph_experiment/` deletados; topologia do grafo vira dado (`GraphSpec`) |
| [Repositórios homologados](decisions/repositorios-homologados.md) | Referências de benchmarking validadas (não é ADR, é material de consulta) |

## Documentos históricos (`historico/`)

Cada um tem um banner de status no topo explicando o que já foi resolvido e
o que ainda é válido — leia o banner antes de confiar no corpo do documento.

- [`PLANO_REFATORACAO_SUPERVISOR.md`](historico/PLANO_REFATORACAO_SUPERVISOR.md) — migração já concluída
- [`analise_custo_real_llm.md`](historico/analise_custo_real_llm.md) — gap de telemetria já fechado
- [`pesquisa_arquitetura_producao.md`](historico/pesquisa_arquitetura_producao.md) — parcialmente superado (CI/CD e telemetria já existem)
- [`notas_regras_negocio_chunkviz.md`](historico/notas_regras_negocio_chunkviz.md) — 2 de 3 itens resolvidos, 1 ainda aberto
- [`arquitetura_nos_declarativa.md`](historico/arquitetura_nos_declarativa.md) — banner corrigido: **foi** implementado (ADR 0008 Fase 5)
- [`estado_e_roteiro_planos.md`](historico/estado_e_roteiro_planos.md) — superado; a Fase 2 que ele dá como bloqueada foi concluída
- [`roadmap-2026-superado/`](historico/roadmap-2026-superado/) — os nove roteiros que viviam na raiz de `docs/`, arquivados em 2026-09-09

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
