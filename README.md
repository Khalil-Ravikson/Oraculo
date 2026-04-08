
# 🔮 Oráculo

> **Aviso:** Este projeto está atualmente **em desenvolvimento ativo**. Algumas funcionalidades podem apresentar instabilidades ou **bugs**, pois a arquitetura e os recursos estão sendo aprimorados e refatorados constantemente.

O **Oráculo** é um agente de Inteligência Artificial pessoal, nascido como um fork evoluído do projeto *meuBotRAG*. Desenvolvido para atuar como um assistente inteligente e flexível, ele integra capacidades de **Clean Architecture**, **RAG Híbrido** e **memória em três camadas**. Tudo isso foi projetado para rodar de forma eficiente em ambientes conteinerizados.

---

## 📋 Índice

* [Introdução](#-introdução)
* [Stack Tecnológico](#-stack-tecnológico)
* [Desenvolvimento e Arquitetura](#-desenvolvimento-e-arquitetura)
* [Status Atual e Bugs Conhecidos](#-status-atual-e-bugs-conhecidos)
* [Como Executar](#-como-executar)
* [Conclusão](#-conclusão)

---

## 🚀 Introdução

O projeto foi criado com o objetivo de centralizar as interações com modelos de linguagem (LLMs) em um ambiente escalável e altamente performático. Aproveitando a base sólida do *meuBotRAG*, o **Oráculo** expande suas capacidades, deixando de ser um bot estritamente focado no contexto acadêmico para se transformar em um agente de IA versátil. A integração fluida com APIs de mensagens permite que ele processe linguagem natural, consulte bases de conhecimento externas de forma ágil e retenha o contexto de maneira inteligente.

---

## 🛠 Stack Tecnológico

A infraestrutura do Oráculo foi pensada para ser robusta e fácil de configurar, apoiando-se em ferramentas modernas:

* **Linguagem Principal:** Python 🐍
* **Framework Web:** FastAPI (garantindo alta performance e suporte assíncrono nativo).
* **LLM:** Google Gemini Flash.
* **Memória e Vector Store:** Redis Stack (unificando cache, sistema de memória e vector store em um único serviço).
* **Processamento Assíncrono:** Celery (gerenciamento eficiente de filas para evitar timeouts em requisições de mensageria).
* **Integração:** Evolution API.
* **Infraestrutura e Deploy:** Totalmente baseado em **Docker** e Docker Compose (isolamento completo do ambiente, sem a necessidade de expor o host localmente através de túneis como o Ngrok).

---

## 🏗 Desenvolvimento e Arquitetura

O desenvolvimento do Oráculo segue fielmente os princípios de **Clean Architecture**, o que garante uma forte separação de responsabilidades e facilita testes, manutenção e a inclusão de novas integrações no futuro.

### Sistema de Memória de 3 Camadas
Para garantir um fluxo conversacional natural e a retenção de contexto a longo prazo, a arquitetura implementa:
1. **Working Memory:** Gerencia o contexto imediato da sessão ativa do usuário (armazenado com baixa latência no Redis).
2. **Long-Term Factual Memory:** Responsável por reter fatos importantes e informações essenciais abstraídas ao longo do tempo.
3. **Cache Semântico:** Intercepta requisições repetidas, reduzindo o número de chamadas ao LLM para poupar tempo e uso de tokens.

### RAG Híbrido (Retrieval-Augmented Generation)
A recuperação de contexto baseia-se em uma estratégia híbrida:
* **Busca Lexical (BM25):** Ideal para correspondência exata de palavras-chave, códigos e nomes.
* **Busca Vetorial (HNSW):** Utilizando embeddings de modelos densos (como `BAAI/bge-m3`) para obter uma compreensão profunda da semântica e da intenção do usuário.

### Pipeline Otimizado
O fluxo de mensagens evita invocar o modelo de IA desnecessariamente. Através de um *Semantic Router*, o sistema avalia se a dúvida do usuário pode ser respondida diretamente através de regras de *guardrails* ou usando a memória em cache, acionando o pipeline completo com o Gemini apenas quando a síntese profunda se faz necessária.

---

## 🐛 Status Atual e Bugs Conhecidos

Como um projeto em desenvolvimento dinâmico e focado em experimentações arquiteturais, o Oráculo apresenta alguns bugs conhecidos que estão na pauta para correção:

* **Concorrência e Filas:** Em situações de pico de requisições simultâneas, os workers do Celery podem apresentar atrasos pontuais no processamento e na entrega da resposta pela API.
* **Falsos Positivos no Roteamento:** O Semantic Router pode ocasionalmente classificar mal a intenção, retornando uma resposta do cache ou memória quando a busca profunda com o RAG seria a ideal.
* **Sincronia do Redis:** Em caso de interrupção não graciosa dos containers Docker, os estados das sessões (como `menu_state`) podem perder sincronia.
* **Tratamento de Limites de API:** O manuseio dos limites de taxa e das flutuações de conectividade das APIs externas (como Gemini e Evolution API) ainda requer aprimoramentos para aumentar a resiliência.

---

## 🐳 Como Executar

O setup foi pensado para ser o mais simples possível, utilizando apenas containers.

1. **Clone o repositório:**
   ```
```text?code_stdout&code_event_index=6
File generated successfully.

```bash
   git clone [https://github.com/Khalil-Ravikson/Oraculo.git](https://github.com/Khalil-Ravikson/Oraculo.git)
   cd Oraculo
   ```

2. **Configure as Variáveis de Ambiente:**
   Configure as chaves e credenciais no arquivo `.env` na raiz do projeto (como a chave da API do Gemini e as URLs de integração).

3. **Suba a infraestrutura:**
   ```bash
   docker-compose up -d --build
   ```

A aplicação subirá orquestrando todos os serviços necessários (FastAPI, Redis, filas Celery), criando um ambiente isolado pronto para uso sem requerer instalações ou túneis na máquina host.

---

## 🎯 Conclusão

O **Oráculo** representa um laboratório de inovação em arquitetura de agentes autônomos. Ao pegar o núcleo do projeto anterior e elevar seu nível técnico com Clean Architecture, integração otimizada de vetores e processamento distribuído por containers, o sistema prova ser uma base poderosa para assistentes inteligentes. Apesar dos desafios normais de um software em desenvolvimento contínuo, a estrutura está preparada para evoluir em performance, flexibilidade e novas capacidades de processamento de IA.
"""
