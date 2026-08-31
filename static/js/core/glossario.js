/* ============================================================================
   glossario.js — camada de tradução (Hub v2, Sprint 0)
   ----------------------------------------------------------------------------
   Converte termo de backend → rótulo humano + tooltip técnico opcional.
   Espelho JS de `templates/hub/_glossario.html` (a fonte para server-render).
   Mantenha os dois em sincronia — o mapa é pequeno de propósito.

   Regra dura do Hub v2: nenhuma página imprime identificador de código, nome
   de tabela, migration ou arquivo .py. Quando o dado vem do backend com o
   termo cru, passe por aqui antes de exibir.

   Uso:
     import { Glossario } from '/static/js/core/glossario.js';
     el.textContent = Glossario.rotulo('owner:langgraph');      // "Motor novo"
     el.title       = Glossario.tecnico('owner:langgraph');     // termo cru p/ debug
     el.append(Glossario.chip('FEATURE_LANGGRAPH_NATIVE_ROUTES')); // <span> + tooltip
   ========================================================================== */

/** termo cru -> { rotulo, tooltip? }  (tooltip = explicação curta, não o termo) */
export const TERMOS = {
  // Motor de execução / rotas
  'owner:langgraph':             { rotulo: 'Motor novo',            tooltip: 'Rota executada pelo mecanismo nativo' },
  'owner:langgraph_conditional': { rotulo: 'Motor novo (em teste)', tooltip: 'Só ativa com "Rotas pelo motor novo" ligado' },
  'owner:legacy':                { rotulo: 'Motor clássico',        tooltip: 'Rota ainda no orquestrador anterior' },
  'FEATURE_LANGGRAPH_NATIVE_ROUTES':   { rotulo: 'Rotas pelo motor novo',   tooltip: 'Chave de laboratório — desligada por padrão' },
  'FEATURE_LANGGRAPH_CELERY_DISPATCH': { rotulo: 'Processamento em fila pelo motor novo', tooltip: 'Chave de laboratório — desligada por padrão' },
  'dispatcher.py':               { rotulo: 'orquestrador clássico' },
  'dispatcher_langgraph.py':     { rotulo: 'orquestrador novo' },
  'route_registry':              { rotulo: 'Mapa de rotas' },
  'graph_node_config':           { rotulo: 'Estado do componente', tooltip: 'Ligado/desligado — o componente continua existindo' },
  'NodeRegistry':                { rotulo: 'Catálogo de componentes' },
  'BaseNode':                    { rotulo: 'Catálogo de componentes' },

  // Dev / flags
  'DEV_TEST_NO_DB_WRITE':        { rotulo: 'Modo simulação (não grava no banco)', tooltip: 'Uso em teste — nenhuma escrita é persistida' },
  'DEV_TEST_SKIP_REGISTRATION':  { rotulo: 'Pular cadastro automático',           tooltip: 'Uso em teste' },
  'RAG_CACHE_TTL_SECONDS':       { rotulo: 'Validade do cache de respostas (s)' },
  'RAG_RERANKER_ENABLED':        { rotulo: 'Reordenar resultados da busca' },
  'GEMINI_MODEL':                { rotulo: 'Modelo de linguagem ativo' },
  'PARSER_PDF_PRIORIDADE':       { rotulo: 'Ordem dos leitores de PDF' },
  'PARSER_DESABILITADOS':        { rotulo: 'Leitores de PDF desligados' },

  // Provedores / circuit breaker
  'circuito:fechado':            { rotulo: 'Operante' },
  'circuito:aberto':             { rotulo: 'Bloqueado por falhas', tooltip: 'Muitas falhas seguidas — pausado até esfriar' },
  'circuito:half_open':          { rotulo: 'Testando recuperação' },

  // Portas / tipos de dado
  'port:text':          { rotulo: 'texto' },
  'port:llm_response':  { rotulo: 'resposta de IA' },
  'port:embeddings':    { rotulo: 'vetor semântico' },
  'port:audio':         { rotulo: 'áudio' },
  'port:file':          { rotulo: 'arquivo' },
  'port:structured':    { rotulo: 'dados estruturados' },
  'port:boolean':       { rotulo: 'sim/não' },
  'port:number':        { rotulo: 'número' },
  'port:array':         { rotulo: 'lista' },
  'port:tokens':        { rotulo: 'tokens' },

  // Rotas (assuntos que o Oráculo reconhece)
  'rota:GERAL':           { rotulo: 'Pergunta geral' },
  'rota:CALENDARIO':      { rotulo: 'Calendário acadêmico' },
  'rota:EDITAL':          { rotulo: 'Editais' },
  'rota:CONTATOS':        { rotulo: 'Contatos e setores' },
  'rota:WIKI':            { rotulo: 'Base de conhecimento (wiki)' },
  'rota:SIGAA':           { rotulo: 'Consulta ao SIGAA' },
  'rota:CHECK_STATUS':    { rotulo: 'Status de solicitação' },
  'rota:GREETING':        { rotulo: 'Saudação' },
  'rota:MEDIA_DOWNLOAD':  { rotulo: 'Baixar mídia enviada' },
  'rota:CRUD':            { rotulo: 'Atualizar cadastro' },
  'rota:TICKET_ABERTURA': { rotulo: 'Abrir chamado' },

  // Ponto de entrada (o que trata a rota)
  'no:rag':            { rotulo: 'Busca em documentos' },
  'no:sigaa':          { rotulo: 'Integração SIGAA' },
  'no:greeting':       { rotulo: 'Resposta de saudação' },
  'no:media_download': { rotulo: 'Download de mídia' },
  'no:check_status':   { rotulo: 'Verificação de status' },
  'no:crud':           { rotulo: 'Edição de cadastro' },
  'no:ticket':         { rotulo: 'Abertura de chamado' },

  // Passos do planejador
  'step:rag_search':   { rotulo: 'buscar nos documentos' },
  'step:synthesis':    { rotulo: 'redigir resposta' },
  'step:rerank':       { rotulo: 'reordenar resultados' },

  // Agentes
  'agente:academic_knowledge': { rotulo: 'Conhecimento acadêmico' },
  'agente:sigaa':              { rotulo: 'SIGAA' },
  'agente:tickets':            { rotulo: 'Chamados' },
  'agente:registration':       { rotulo: 'Cadastro' },

  // Componentes (nós) — nome e descrição amigáveis
  'node:channel_whatsapp':    { rotulo: 'Envio pelo WhatsApp', tooltip: 'Envia texto, "digitando…" e mídia pelo WhatsApp' },
  'node:embeddings_default':  { rotulo: 'Gerador de vetor semântico', tooltip: 'Transforma texto em vetor para busca por significado' },
  'node:llm_default':         { rotulo: 'Modelo de linguagem', tooltip: 'Chama o provedor de IA ativo, com disjuntor de falhas' },
  'node:parser_default':      { rotulo: 'Leitor de documento', tooltip: 'Extrai texto de PDF/DOCX; escolhe o leitor automaticamente' },
  'node:stt_default':         { rotulo: 'Áudio → texto', tooltip: 'Transcreve áudio recebido' },
  'node:tts_default':         { rotulo: 'Texto → áudio', tooltip: 'Gera resposta em áudio' },
  'node:tool_default':        { rotulo: 'Executor de ferramentas', tooltip: 'Roda as ferramentas vinculadas aos agentes' },
  'node:lab_mcp':             { rotulo: 'Laboratório MCP', tooltip: 'Ambiente de estudo — não faz parte do fluxo de produção' },
  'node:lab_rest':            { rotulo: 'Laboratório REST', tooltip: 'Ambiente de estudo — não faz parte do fluxo de produção' },
};

function entrada(chave) {
  if (chave == null) return null;
  const k = String(chave).trim();
  return TERMOS[k] || null;
}

export const Glossario = {
  /** rótulo humano; se o termo não está no mapa, devolve o próprio texto */
  rotulo(chave, fallback) {
    const e = entrada(chave);
    return e ? e.rotulo : (fallback ?? String(chave ?? ''));
  },

  /** explicação curta (tooltip) ou string vazia */
  ajuda(chave) {
    const e = entrada(chave);
    return e && e.tooltip ? e.tooltip : '';
  },

  /** termo cru — só para `title=` / `data-tech=` de debug, nunca visível como texto */
  tecnico(chave) {
    return String(chave ?? '');
  },

  /** <span> pronto: rótulo visível + termo cru em data-tech + ajuda em title */
  chip(chave, fallback) {
    const span = document.createElement('span');
    span.textContent = this.rotulo(chave, fallback);
    span.dataset.tech = this.tecnico(chave);
    const ajuda = this.ajuda(chave);
    if (ajuda) span.title = ajuda;
    return span;
  },
};

// Acesso global para scripts não-módulo / conteúdo montado via HTMX
if (typeof window !== 'undefined') window.Glossario = Glossario;
