/* config.js — Configuração do Hub (v2 Sprint 3a): abas, provedores dinâmicos,
   barra de salvar para as chaves de comportamento. */
import { api, ApiError, hub, testConnection } from '/static/js/core/api-client.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar, formModal } from '/static/js/core/modal.js';
import { fmt } from '/static/js/core/format.js';
import { Glossario } from '/static/js/core/glossario.js';
import { SaveBar } from '/static/js/components/save-bar.js';

const $ = (id) => document.getElementById(id);

// ─── System prompt + manutenção + cache + credenciais ──────────────────────
const KEYS = [
  ['GEMINI_API_KEY', 'Gemini API key', 'password'],
  ['EMBEDDING_PROVIDER', 'Provedor de embeddings', 'select', ['google', 'local']],
  ['DATABASE_URL', 'DATABASE_URL', 'password'],
  ['REDIS_URL', 'REDIS_URL', 'text'],
  ['ADMIN_JWT_SECRET', 'ADMIN_JWT_SECRET', 'password'],
  ['ADMIN_API_KEY', 'ADMIN_API_KEY', 'password'],
  ['ADMIN_NUMBERS', 'Números admin (WhatsApp)', 'text'],
  ['LLAMA_CLOUD_API_KEY', 'LlamaCloud key', 'password'],
  ['HF_TOKEN', 'HuggingFace token', 'password'],
];

function renderKeys(sys) {
  $('keys').innerHTML = KEYS.map(([env, label, type, opts]) => {
    const val = fmt.esc(sys[env.toLowerCase()] ?? '');
    const ctl = type === 'select'
      ? `<select class="select" data-env="${env}">${opts.map((o) => `<option ${o === sys[env.toLowerCase()] ? 'selected' : ''}>${o}</option>`).join('')}</select>`
      : `<input class="input" type="${type}" data-env="${env}" value="${type === 'password' ? '' : val}" placeholder="${type === 'password' ? '•••• (deixe vazio para manter)' : ''}">`;
    return `<div class="col-6"><div class="field"><label class="field__label">${label}</label>${ctl}</div></div>`;
  }).join('');
}

async function saveKeys() {
  const env = {};
  document.querySelectorAll('#keys [data-env]').forEach((el) => { if (el.value.trim()) env[el.dataset.env] = el.value.trim(); });
  if (!Object.keys(env).length) return showToast('Nada para salvar', 'error');
  try { await api.post('/system/env', { env }); showToast('Credenciais salvas — reinicie os serviços'); }
  catch (e) { showToast(e.message, 'error'); }
}

async function loadSystem() {
  try {
    const sys = await api.get('/system');
    renderKeys(sys);
    if (sys.prompt_custom) { $('sys-prompt').value = sys.prompt_custom; }
    atualizarContador();
    $('maint-status').textContent = sys.manutencao ? 'Manutenção ativa — usuários bloqueados.' : 'Operando normalmente.';
  } catch (e) { showToast(e.message, 'error'); }
}

function atualizarContador() {
  const n = $('sys-prompt').value.length;
  const el = $('prompt-count');
  el.textContent = `${n.toLocaleString('pt-BR')} caracteres`;
  el.classList.toggle('field__count--over', n > 8000);
}

// ─── Chaves de comportamento (config dinâmica) + SaveBar ───────────────────
const VER = {};
const PENDENTES = {};   // chave -> novo valor
let bar;

function widget(c) {
  const off = c.reconectada ? '' : ' disabled';
  if (c.tipo === 'bool') {
    return `<select class="select" data-dv="${c.chave}" style="max-width:120px"${off}>
      <option value="true" ${c.valor === 'true' ? 'selected' : ''}>ligado</option>
      <option value="false" ${c.valor === 'false' ? 'selected' : ''}>desligado</option></select>`;
  }
  return `<input class="input" data-dv="${c.chave}" type="${c.tipo === 'int' ? 'number' : 'text'}" value="${fmt.esc(c.valor)}" style="max-width:240px"${off}>`;
}

async function loadDyn() {
  Object.keys(PENDENTES).forEach((k) => delete PENDENTES[k]);
  bar?.reset();
  try {
    const d = await api.get('/config');
    $('dyn-list').innerHTML = `<table class="table"><thead><tr><th>Chave</th><th>Valor</th><th>Efeito</th><th></th></tr></thead><tbody>${
      d.chaves.map((c) => {
        VER[c.chave] = c.versao;
        const rotulo = Glossario.rotulo(c.chave, c.chave);
        return `<tr${c.reconectada ? '' : ' style="opacity:.55"'}>
          <td><span data-tech="${c.chave}">${fmt.esc(rotulo)}</span><div class="caption">${c.tipo}${c.reconectada ? ` · v${c.versao}` : ''}</div></td>
          <td>${widget(c)}</td>
          <td>${c.reconectada ? '<span class="badge badge--ok">imediato</span>' : '<span class="badge badge--unknown">sem efeito imediato</span>'}</td>
          <td><button class="btn btn--sm" data-hist="${c.chave}">Histórico</button></td>
        </tr>`;
      }).join('')
    }</tbody></table>`;

    $('dyn-list').querySelectorAll('[data-dv]').forEach((el) => el.onchange = () => {
      const k = el.dataset.dv;
      PENDENTES[k] = String(el.value);
      bar.markDirty(k);
    });
    $('dyn-list').querySelectorAll('[data-hist]').forEach((b) => b.onclick = () => histDyn(b.dataset.hist));
  } catch (e) { $('dyn-list').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`; }
}

async function salvarPendentes() {
  const chaves = Object.keys(PENDENTES);
  let erros = 0;
  for (const chave of chaves) {
    try {
      const d = await api.post('/config', { chave, valor: PENDENTES[chave], versao: VER[chave] });
      VER[chave] = d.versao;
    } catch (e) {
      erros++;
      showToast(e instanceof ApiError && e.isConflict ? `${chave} mudou noutro lugar` : `${chave}: ${e.message}`, 'error');
    }
  }
  showToast(erros ? `${chaves.length - erros}/${chaves.length} salvas` : `${chaves.length} chave(s) salvas`);
  await loadDyn();
}

async function histDyn(chave) {
  $('hist-title').textContent = 'Histórico — ' + Glossario.rotulo(chave, chave);
  $('hist').hidden = false;
  $('hist-body').textContent = 'Carregando…';
  try {
    const d = await api.get(`/config/${chave}/historico`);
    $('hist-body').innerHTML = d.historico.map((h) => `
      <div class="config-hist">
        <span class="mono">v${h.versao} · <b>${fmt.esc(h.valor_novo)}</b> <span class="caption">(era ${h.valor_antigo == null ? '—' : fmt.esc(h.valor_antigo)})</span></span>
        <span class="caption">${fmt.esc(h.atualizado_por || '?')} · ${fmt.dateTime(h.atualizado_em)}
          <button class="btn btn--sm" data-rev="${h.versao}">Reverter</button></span>
      </div>`).join('') || '<span class="caption">Sem histórico.</span>';
    $('hist-body').querySelectorAll('[data-rev]').forEach((b) => b.onclick = async () => {
      if (!await confirmar({ titulo: 'Reverter', corpo: `Volta ao valor da versão ${b.dataset.rev}.`, acao: 'Reverter' })) return;
      try {
        const d2 = await api.post(`/config/${chave}/reverter`, { para_versao: Number(b.dataset.rev), versao: VER[chave] });
        showToast(`Revertido (v${d2.versao})`); $('hist').hidden = true; loadDyn();
      } catch (e) { showToast(e.message, 'error'); }
    });
  } catch (e) { $('hist-body').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`; }
}

// ─── Provedores de LLM ────────────────────────────────────────────────────
function saude(s) {
  if (s === true) return '<span class="badge badge--ok status-pill">Conectado</span>';
  if (s === false) return '<span class="badge badge--danger status-pill">Sem credencial</span>';
  return '<span class="badge badge--unknown status-pill">Desconhecido</span>';
}

function providerCard(p, ativo) {
  const ehAtivo = p.nome === ativo;
  return `<div class="card card--resource" data-prov="${fmt.esc(p.nome)}">
    <div class="card__head">
      <span class="card__title">${fmt.esc(p.nome)}${ehAtivo ? ' <span class="badge badge--active">ativo agora</span>' : ''}</span>
      ${saude(p.saude)}
    </div>
    <dl class="res__meta">
      <dt>modelo</dt><dd>${fmt.esc(p.modelo_default || '—')}</dd>
      <dt>tipo</dt><dd>${p.origem === 'codigo' ? 'nativo' : 'compatível com OpenAI'}</dd>
      <dt>chave</dt><dd>${p.api_key_definida ? 'definida' : `falta ${fmt.esc(p.api_key_env || '?')}`}</dd>
    </dl>
    <div class="res__actions">
      <button class="btn btn--sm" data-test="${fmt.esc(p.nome)}">Testar Conexão</button>
      ${!ehAtivo && p.habilitado ? `<button class="btn btn--sm" data-ativar="${fmt.esc(p.nome)}">Tornar ativo</button>` : ''}
      ${p.origem === 'painel' ? `<button class="btn btn--sm" data-toggle="${p.id}" data-h="${p.habilitado ? 0 : 1}">${p.habilitado ? 'Desligar' : 'Ligar'}</button>
      <button class="btn btn--sm btn--danger" data-del="${p.id}" data-nome="${fmt.esc(p.nome)}">Excluir</button>` : ''}
    </div>
  </div>`;
}

async function loadProviders() {
  try {
    const d = await hub.get('/providers');
    $('prov-ativo').textContent = d.ativo_global || '—';
    const box = $('providers');
    box.innerHTML = d.providers.map((p) => providerCard(p, d.ativo_global)).join('');

    box.querySelectorAll('[data-test]').forEach((b) => b.onclick = () => testarProvider(b.dataset.test));
    box.querySelectorAll('[data-ativar]').forEach((b) => b.onclick = () => ativarProvider(b.dataset.ativar));
    box.querySelectorAll('[data-toggle]').forEach((b) => b.onclick = () => togglarProvider(Number(b.dataset.toggle), b.dataset.h === '1'));
    box.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => excluirProvider(Number(b.dataset.del), b.dataset.nome));
  } catch (e) {
    $('providers').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`;
  }
}

async function testarProvider(nome) {
  showToast(`Testando ${nome}…`);
  const r = await testConnection('llm', '/providers/test-connection', { nome });
  showToast(r.ok ? `${nome}: ${r.mensagem}` : `${nome}: ${r.mensagem}`, r.ok ? 'ok' : 'error');
}

async function ativarProvider(nome) {
  try { await hub.post('/llm/provider', { provider: nome }); showToast(`Provedor ativo: ${nome}`); await loadProviders(); }
  catch (e) { showToast(e.message, 'error'); }
}

async function togglarProvider(id, habilitado) {
  try { await hub.post('/providers/toggle', { provider_id: id, habilitado }); await loadProviders(); }
  catch (e) { showToast(e.message, 'error'); }
}

async function excluirProvider(id, nome) {
  if (!await confirmar({ titulo: `Excluir ${nome}`, corpo: 'O provedor some da lista. Se ele estava ativo, o sistema volta ao padrão.', acao: 'Excluir', perigo: true })) return;
  try { await hub.post('/providers/remove', { provider_id: id }); showToast(`${nome} excluído`); await loadProviders(); }
  catch (e) { showToast(e.message, 'error'); }
}

$('btn-add-provider').onclick = async () => {
  const corpo = document.createElement('div');
  corpo.innerHTML = `
    <div class="field"><label class="field__label">Nome</label><input class="input" name="nome" placeholder="ex: openai-uema" required></div>
    <div class="field"><label class="field__label">URL base (compatível com OpenAI)</label><input class="input" name="base_url" placeholder="https://api.openai.com/v1" required></div>
    <div class="field"><label class="field__label">Modelo padrão</label><input class="input" name="modelo_default" placeholder="ex: gpt-4o-mini" required></div>
    <div class="field"><label class="field__label">Variável de ambiente da chave</label><input class="input" name="api_key_env" placeholder="ex: OPENAI_UEMA_KEY" required>
      <span class="field__hint">A chave fica no .env do servidor. Aqui vai só o nome da variável.</span></div>`;
  const r = await formModal({
    titulo: 'Adicionar Provedor de LLM', corpo, acao: 'Testar e salvar',
    onSubmit: async (form) => {
      const d = Object.fromEntries(new FormData(form));
      if (!d.nome || !d.base_url || !d.modelo_default || !d.api_key_env) throw new Error('Preencha todos os campos');
      const teste = await testConnection('llm', '/providers/test-connection', {
        tipo: 'openai_compat', nome: d.nome, base_url: d.base_url,
        api_key_env: d.api_key_env, modelo_default: d.modelo_default,
      });
      if (!teste.ok) throw new Error(`Conexão falhou: ${teste.mensagem}`);
      return hub.post('/providers', {
        nome: d.nome, tipo: 'openai_compat', base_url: d.base_url,
        api_key_env: d.api_key_env, modelo_default: d.modelo_default,
        modelos: [d.modelo_default],
      });
    },
  });
  if (r) { showToast(`Provedor "${r.nome}" adicionado`); await loadProviders(); }
};

// ─── Canais ───────────────────────────────────────────────────────────────
const ESTADO_CANAL = {
  open: ['badge--ok', 'Conectado'], connecting: ['badge--warn', 'Conectando'],
  close: ['badge--danger', 'Desconectado'], nao_encontrada: ['badge--danger', 'Instância não existe'],
  erro: ['badge--danger', 'Erro'], desligado: ['badge--neutral', 'Desligado'],
  sem_configuracao: ['badge--unknown', 'Sem configuração'],
};
function estadoCanal(conexao) {
  const [cls, txt] = ESTADO_CANAL[conexao?.estado] || ['badge--unknown', conexao?.estado || 'Desconhecido'];
  return `<span class="badge ${cls} status-pill">${txt}</span>`;
}

function channelCard(c) {
  return `<div class="card card--resource" data-canal="${c.id}">
    <div class="card__head">
      <span class="card__title">${fmt.esc(c.nome)}</span>
      ${estadoCanal(c.conexao)}
    </div>
    <dl class="res__meta">
      <dt>webhook</dt><dd>${fmt.esc(c.webhook_url || '—')}</dd>
      <dt>instância</dt><dd>${fmt.esc(c.instance || '—')}</dd>
      <dt>chave</dt><dd>${c.api_key_definida ? 'definida' : `falta ${fmt.esc(c.api_key_env || '?')}`}</dd>
    </dl>
    <div class="res__actions">
      <button class="btn btn--sm" data-qr="${c.id}">Reconectar QR Code</button>
      <button class="btn btn--sm" data-wh="${c.id}" data-url="${fmt.esc(c.webhook_url || '')}">Editar Webhook</button>
      ${c.origem === 'painel' ? `<button class="btn btn--sm" data-ctoggle="${c.id}" data-h="${c.habilitado ? 0 : 1}">${c.habilitado ? 'Desligar' : 'Ligar'}</button>
      <button class="btn btn--sm btn--danger" data-cdel="${c.id}" data-nome="${fmt.esc(c.nome)}">Excluir</button>` : ''}
    </div>
  </div>`;
}

async function loadChannels() {
  try {
    const d = await hub.get('/channels');
    const box = $('channels');
    box.innerHTML = d.channels.map(channelCard).join('') ||
      '<div class="empty empty--inline"><div class="empty__title">Nenhum canal</div></div>';
    box.querySelectorAll('[data-qr]').forEach((b) => b.onclick = () => reconectarQR(Number(b.dataset.qr)));
    box.querySelectorAll('[data-wh]').forEach((b) => b.onclick = () => editarWebhook(Number(b.dataset.wh), b.dataset.url));
    box.querySelectorAll('[data-ctoggle]').forEach((b) => b.onclick = async () => {
      try { await hub.post('/channels/toggle', { canal_id: Number(b.dataset.ctoggle), habilitado: b.dataset.h === '1' }); await loadChannels(); }
      catch (e) { showToast(e.message, 'error'); }
    });
    box.querySelectorAll('[data-cdel]').forEach((b) => b.onclick = async () => {
      if (!await confirmar({ titulo: `Excluir ${b.dataset.nome}`, corpo: 'O canal some do painel. A instância na Evolution não é apagada.', acao: 'Excluir', perigo: true })) return;
      try { await hub.post('/channels/remove', { canal_id: Number(b.dataset.cdel) }); showToast('Canal excluído'); await loadChannels(); }
      catch (e) { showToast(e.message, 'error'); }
    });
  } catch (e) { $('channels').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`; }
}

async function reconectarQR(id) {
  showToast('Gerando QR Code…');
  try {
    const r = await hub.post('/channels/reconnect', { canal_id: id });
    if (r.erro) return showToast(`Falhou: ${r.erro}`, 'error');
    const corpo = document.createElement('div');
    corpo.style.textAlign = 'center';
    corpo.innerHTML = r.qr_base64
      ? `<img src="${r.qr_base64.startsWith('data:') ? r.qr_base64 : 'data:image/png;base64,' + r.qr_base64}" alt="QR Code" style="max-width:260px;border-radius:var(--radius-card)">`
      : (r.code ? `<p>Código de pareamento:</p><p class="mono" style="font-size:var(--text-lg)">${fmt.esc(r.code)}</p>` : '<p>Sem QR disponível — a instância pode já estar conectada.</p>');
    await formModal({ titulo: 'Reconectar WhatsApp', corpo, acao: 'Fechar', onSubmit: () => true });
    loadChannels();
  } catch (e) { showToast(e.message, 'error'); }
}

async function editarWebhook(id, atual) {
  const corpo = document.createElement('div');
  corpo.innerHTML = `<div class="field"><label class="field__label">Endereço do webhook</label>
    <input class="input" name="webhook_url" value="${fmt.esc(atual)}" placeholder="https://seu-servidor/webhook/evolution"></div>`;
  const r = await formModal({
    titulo: 'Editar Webhook', corpo, acao: 'Salvar',
    onSubmit: (form) => hub.post('/channels/webhook', { canal_id: id, webhook_url: new FormData(form).get('webhook_url') }),
  });
  if (r) {
    showToast(r.aplicado_na_evolution ? 'Webhook salvo e aplicado' : `Webhook salvo (não aplicado na Evolution: ${r.detalhe || '?'})`, r.aplicado_na_evolution ? 'ok' : 'error');
    loadChannels();
  }
}

$('btn-add-channel').onclick = async () => {
  const corpo = document.createElement('div');
  corpo.innerHTML = `
    <div class="field"><label class="field__label">Nome</label><input class="input" name="nome" placeholder="ex: Atendimento UEMA 02" required></div>
    <div class="field"><label class="field__label">URL base da Evolution</label><input class="input" name="base_url" placeholder="https://evolution.seu-servidor" required></div>
    <div class="field"><label class="field__label">Nome da instância</label><input class="input" name="instance" placeholder="ex: uema-02" required></div>
    <div class="field"><label class="field__label">Variável de ambiente da chave</label><input class="input" name="api_key_env" value="EVOLUTION_API_KEY" required>
      <span class="field__hint">A chave fica no .env do servidor.</span></div>
    <div class="field"><label class="field__label">Webhook (opcional)</label><input class="input" name="webhook_url" placeholder="https://seu-servidor/webhook/evolution"></div>`;
  const r = await formModal({
    titulo: 'Adicionar Canal', corpo, acao: 'Conectar',
    onSubmit: (form) => {
      const d = Object.fromEntries(new FormData(form));
      if (!d.nome || !d.base_url || !d.instance) throw new Error('Preencha nome, URL e instância');
      return hub.post('/channels', d);
    },
  });
  if (r) { showToast(`Canal "${r.nome}" adicionado`); loadChannels(); }
};

// ─── Wire ──────────────────────────────────────────────────────────────────
$('hist-x').onclick = () => $('hist').hidden = true;
$('hist').onclick = (e) => { if (e.target === $('hist')) $('hist').hidden = true; };
$('save-keys').onclick = saveKeys;
$('sys-prompt').addEventListener('input', atualizarContador);
$('save-prompt').onclick = async () => { try { await api.post('/system/prompt', { prompt: $('sys-prompt').value }); showToast('Prompt salvo'); } catch (e) { showToast(e.message, 'error'); } };
$('reset-prompt').onclick = async () => {
  if (!await confirmar({ titulo: 'Restaurar prompt padrão', corpo: 'O texto atual é apagado e volta o padrão interno do sistema.', acao: 'Restaurar', perigo: true })) return;
  try { await api.post('/system/prompt', { prompt: '' }); $('sys-prompt').value = ''; atualizarContador(); showToast('Prompt restaurado'); } catch (e) { showToast(e.message, 'error'); }
};
$('maint-on').onclick = async () => { try { const d = await api.post('/system/maintenance', { ativo: true }); showToast(d.msg || 'Manutenção ativada'); loadSystem(); } catch (e) { showToast(e.message, 'error'); } };
$('maint-off').onclick = async () => { try { const d = await api.post('/system/maintenance', { ativo: false }); showToast(d.msg || 'Manutenção desativada'); loadSystem(); } catch (e) { showToast(e.message, 'error'); } };
$('clear-cache').onclick = async () => {
  if (!await confirmar({ titulo: 'Limpar cache', corpo: 'Todas as respostas guardadas são apagadas.', acao: 'Limpar', perigo: true })) return;
  try { const r = await fetch('/api/admin/cache', { method: 'DELETE', credentials: 'same-origin' }); const d = await r.json(); $('cache-status').textContent = `${d.deleted ?? '?'} entradas removidas.`; showToast('Cache limpo'); }
  catch (e) { showToast(e.message, 'error'); }
};
$('check-workers').onclick = async () => {
  $('workers').textContent = 'Verificando…';
  try {
    const d = await api.get('/celery/health');
    $('workers').innerHTML = d.ok && d.workers?.length
      ? d.workers.map((w) => `<span class="badge badge--ok" style="margin-right:var(--space-1)">${fmt.esc(w)}</span>`).join('')
      : `<span style="color:var(--danger)">Nenhuma fila respondendo. ${fmt.esc(d.error || '')}</span>`;
  } catch (e) { $('workers').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`; }
};

bar = SaveBar.mount({ onSave: salvarPendentes, onDiscard: () => loadDyn() });

loadSystem();
loadDyn();
loadProviders();
loadChannels();
