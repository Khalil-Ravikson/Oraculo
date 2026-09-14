# PGP Oráculo UEMA — 3.3 Análise de Custo (Comparativa de Fornecedores)

> ⚠️ **Leia antes de usar**: os valores marcados com 🌐 vêm de busca na web
> feita em 01/09/2026 — mas boa parte dos resultados de busca eram sites
> agregadores/SEO de terceiros, não a página oficial do provedor (um deles,
> `deepseek.ai/pricing`, nem é o domínio real da DeepSeek — o oficial é
> `deepseek.com`). Tratei como indício, não como fato confirmado — **antes
> de travar qualquer número no orçamento final, confirme direto em
> `groq.com/pricing`, `platform.deepseek.com` e `z-api.io`**. Os únicos
> números que são dado real e auditado são os marcados como "medido" (vêm
> do seu painel `/hub/llm-custo`).

---

## 1. Baseline real de custo hoje (medido)

| Indicador | Valor | Fonte |
|---|---|---|
| Provedor efetivamente em uso | Gemini (Google) — 100% do tráfego até agora | Medido, painel `/hub/llm-custo`, 01/09/2026 |
| Groq / DeepSeek | Cadastrados no sistema, **nunca exercitados com tráfego real** | Medido (0 chamadas, 0 custo nos dois) |
| Custo últimas 24h | US$ 0,0067 (R$ 0,03) — 13 mensagens, 19.263 tokens | Medido |
| Cotação usada | R$ 5,18/USD | Medido |

**Leitura:** hoje você não tem uma comparação de custo real entre provedores — tem só o preço de tabela do Gemini rodando em volume de teste. A decisão de qual provedor é mais barato **na prática** (considerando latência, taxa de erro, qualidade de resposta) ainda depende de rodar tráfego real em Groq/DeepSeek pelo menos uma vez.

---

## 2. Comparativo de Provedores de LLM

| Provedor | Status no projeto | Modelo de custo | Tier gratuito | Observação |
|---|---|---|---|---|
| **Gemini** (Google) | Em produção, único testado | Pago por token (tabela já cadastrada no painel: `gemini-2.5-flash` US$0,30/US$2,50 por 1M IN/OUT; `gemini-2.5-flash-lite` US$0,10/US$0,40) | 🔎 Google costuma oferecer cota gratuita mensal em nível de desenvolvedor — confirmar se ainda ativa e qual o limite | Já validado em produção; ponto único de dependência hoje (ver Premissa P3 / Restrição sobre provedor externo) |
| **Groq** | Cadastrado, **nunca testado com tráfego real** | 🌐 US$ 0,05/US$ 0,08 por 1M tokens IN/OUT no modelo mais barato (Llama 3.1 8B Instant); modelos maiores chegam a ~US$ 0,59/US$ 0,79 (Llama 3.3 70B). Batch API + prompt caching podem reduzir isso a ~25% do preço on-demand | 🌐 Sim — sem cartão de crédito, limite de 30 req/min, 6.000 tokens/min, 14.400 req/dia por organização | **Ordem de grandeza abaixo do Gemini** para o modelo mais barato — prioridade alta pra testar como fallback/rota de baixo custo |
| **DeepSeek** | Cadastrado, **nunca testado com tráfego real** | 🌐 Resultados de busca indicam algo como US$ 0,22/US$ 0,66 por 1M tokens IN/OUT (fora do horário de pico) — **mas essa cifra veio de agregador de terceiro, não confirmada na documentação oficial** | 🌐 Sem tier gratuito relevante segundo as fontes encontradas | Ainda assim tende a ser mais barato que o Gemini atual — **validar preço oficial em `platform.deepseek.com` antes de decidir** |
| **LLM local / self-hosted** (ex.: Ollama + modelo open-weight) | Não implementado — mencionado como direção desejada | Custo de infraestrutura (hardware/CPU-GPU), zero custo por token | N/A (custo é de capacidade, não de uso) | Reduz dependência de terceiro e elimina custo variável por mensagem; troca custo operacional recorrente por custo de capacidade computacional (e possível perda de qualidade vs. modelos de fronteira) |

**Fontes da busca (01/09/2026)** — tratar como indício, não como preço confirmado:
- [Groq Pricing — CloudZero](https://www.cloudzero.com/blog/groq-pricing/)
- [Groq Pricing — eesel AI](https://www.eesel.ai/blog/groq-pricing)
- [DeepSeek API Pricing — Morph](https://www.morphllm.com/deepseek-api)
- [DeepSeek API Pricing — BenchLM.ai](https://benchlm.ai/deepseek/api-pricing)

**Recomendação de ação (sem custo, só esforço de engenharia):** rodar um lote de tráfego real (ou replay de conversas já registradas) em Groq e DeepSeek para ter comparação de custo/latência/qualidade real antes da próxima revisão deste plano — o painel de custo já existe e já segrega por provedor, então a medição está pronta, só falta o teste.

---

## 3. Comparativo de Hospedagem/Infraestrutura

| Opção | Custo | Vantagem | Risco/Desvantagem |
|---|---|---|---|
| **Atual — Docker Compose** | **R$ 0 direto** — roda na sua máquina local, sem VPS/nuvem paga hoje | Ambiente já validado e funcional, custo zero de hospedagem | Não é ambiente de produção real (uptime depende da sua máquina estar ligada); não escala nem sobrevive a você desligar o PC |
| **Servidor da CTIC (institucional)** | Provavelmente custo marginal ≈ zero (infraestrutura já paga pela instituição) | Elimina custo de nuvem; dados ficam em infraestrutura institucional (ponto a favor de LGPD/governança) | Depende de aprovação/disponibilidade de recursos do CTIC; pode ter limitação de hardware (GPU, RAM) comparado a nuvem sob demanda |
| **Nuvem paga (VPS/cloud genérico)** | 🔎 Variável conforme provedor e dimensionamento (CPU/RAM dos workers Celery + Redis Stack + Postgres) | Escala sob demanda, sem depender de aprovação institucional | Custo recorrente que cresce com uso; é exatamente o tipo de gasto que uma migração pro CTIC eliminaria |

> ⚠️ Preciso que você preencha "onde o Docker roda hoje" — isso muda se essa linha é custo real atual ou é só ambiente de desenvolvimento sem custo de hospedagem dedicado.

**Recomendação:** migrar para o servidor da CTIC é o movimento de menor custo total, condicionado a: (a) o hardware do CTIC suportar os workers de mídia (STT/TTS) sem GPU dedicada, e (b) aprovação institucional formal — vale virar item de ação, não só intenção.

---

## 4. Comparativo de Gateway de WhatsApp

| Gateway | Status no projeto | Modelo de custo | Observação |
|---|---|---|---|
| **Evolution API** | Testado, em uso hoje | Gratuito/open-source, self-hosted — custo é só a infraestrutura que já roda | Gateway atual do projeto (não oficial, ver Restrição R4 já registrada) |
| **WAHA** (WhatsApp HTTP API) | Testado | 🔎 Open-source/self-hosted também, com edição "Core" gratuita e planos pagos para suporte/recursos avançados | Alternativa validada ao Evolution API — vale registrar por que Evolution foi o escolhido (recurso, estabilidade, comunidade?) para não perder essa decisão |
| **Z-API** | Avaliado, **descartado por custo** | 🌐 Confirmado: R$ 55–R$ 99,99/mês por instância conectada (cobra por número ativo, não por mensagem) | Confirma decisão correta de não usar, dado que o Oráculo já resolve isso sem custo com Evolution/WAHA. [Fonte: Wafly](https://wafly.com.br/comparativos/quanto-custa-api-whatsapp/) |
| **API oficial da Meta (WhatsApp Business Cloud API)** | Não testada | 🔎 Cobrança por conversa iniciada (categoria "utility"/"marketing"/"service"), modelo bem diferente dos gateways não-oficiais | Caminho "oficial" de longo prazo se o volume crescer a ponto de o risco do gateway não-oficial (Restrição R4) pesar mais que o custo por conversa |
| **Twilio / 360dialog (BSPs oficiais)** | Não testados | 🔎 Cobram taxa de plataforma + repasse do custo por conversa da Meta | Opção institucional "enterprise" — normalmente só compensa em escala grande |

**Recomendação:** manter Evolution API (gratuito, já validado) é a decisão correta hoje do ponto de vista de custo. A migração para API oficial só se justifica se o risco de depender de gateway não-oficial (R4) se tornar inaceitável em produção real com a comunidade acadêmica — isso é uma decisão de risco, não de custo.

---

## 5. Processamento de Documentos (Parser)

| Item | Custo | Observação |
|---|---|---|
| Parser de documentos | **Ainda não decidido** — testados PyMuPDF, LlamaParse/Llama-Index e Docling, nenhum adotado oficialmente | Todos os testados têm biblioteca/tier gratuito — custo hoje é R$ 0 | PyMuPDF é biblioteca local (sem custo, sem limite de uso); LlamaParse é serviço em nuvem com tier gratuito por volume mensal de páginas; Docling é biblioteca local (IBM, open-source, sem custo) — a decisão final entre eles é técnica (qualidade de extração), não de custo, já que os três são gratuitos no volume atual |

> **Recomendação:** como nenhum tem custo agora, decida entre PyMuPDF/Docling (locais, sem depender de tier gratuito de terceiro) por resiliência — evita que uma mudança de política de tier gratuito de um serviço em nuvem (como o LlamaParse) vire uma dependência de custo surpresa mais tarde.

---

## 6. Síntese e Recomendação de Estratégia de Custo

| Frente | Situação hoje | Ação recomendada | Custo da ação |
|---|---|---|---|
| LLM | 100% Gemini, sem comparação real | Testar Groq/DeepSeek com tráfego real | Zero (infraestrutura de medição já existe) |
| LLM (resiliência) | Dependência de um único provedor externo | Avaliar LLM local como fallback/redução de custo variável | Custo de engenharia + eventual hardware |
| Hospedagem | `[a confirmar]` | Avaliar migração para servidor CTIC | Zero (se aprovado institucionalmente) |
| Gateway WhatsApp | Evolution API, gratuito, validado | Manter — já é a opção de menor custo | Zero |
| Parser de documentos | Tier gratuito em uso | Monitorar limite do tier antes de escalar | Zero até estourar o limite |

**Conclusão para o PGP:** o Oráculo já opera, por escolha deliberada, na configuração de menor custo possível em quase todas as frentes (gateway gratuito, tier gratuito de parser, LLM com tabela de preço mais barata do mercado hoje). O único ponto de custo real e recorrente comprovado é o consumo de tokens do Gemini — hoje irrisório (R$ 0,03/dia em volume de teste), mas sem projeção formal para volume de produção real. A ação de maior retorno por menor esforço é **testar Groq/DeepSeek com tráfego real**, já que a infraestrutura de comparação de custo já está pronta e não foi usada ainda.
