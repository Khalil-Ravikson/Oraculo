/* config.js — Configuração do Hub (Plano B). */
import { api, ApiError } from '/static/js/core/api-client.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';
import { fmt } from '/static/js/core/format.js';

const $ = (id) => document.getElementById(id);

// ─── Chaves .env ───────────────────────────────────────────────────────────
const KEYS = [
  ['GEMINI_API_KEY', 'Gemini API key', 'password'],
  ['EMBEDDING_PROVIDER', 'Embedding provider', 'select', ['google', 'local']],
  ['EVOLUTION_API_KEY', 'Evolution API key', 'password'],
  ['EVOLUTION_BASE_URL', 'Evolution base URL', 'text'],
  ['EVOLUTION_INSTANCE_NAME', 'Evolution instance', 'text'],
  ['DEEPSEEK_API_KEY', 'DeepSeek API key', 'password'],
  ['DEEPSEEK_MODEL', 'DeepSeek model', 'text'],
  ['GROQ_API_KEY', 'Groq API key', 'password'],
  ['GROQ_MODEL', 'Groq model', 'text'],
  ['DATABASE_URL', 'DATABASE_URL', 'password'],
  ['REDIS_URL', 'REDIS_URL', 'text'],
  ['ADMIN_JWT_SECRET', 'ADMIN_JWT_SECRET', 'password'],
  ['ADMIN_API_KEY', 'ADMIN_API_KEY', 'password'],
  ['ADMIN_NUMBERS', 'ADMIN_NUMBERS', 'text'],
  ['LLAMA_CLOUD_API_KEY', 'LlamaCloud key', 'password'],
  ['HF_TOKEN', 'HF token', 'password'],
];

function renderKeys(sys) {
  $('keys').innerHTML = KEYS.map(([env, label, type, opts]) => {
    const val = fmt.esc(sys[env.toLowerCase()] ?? '');
    const ctl = type === 'select'
      ? `<select class="select" data-env="${env}">${opts.map(o => `<option ${o === sys[env.toLowerCase()] ? 'selected' : ''}>${o}</option>`).join('')}</select>`
      : `<input class="input" type="${type}" data-env="${env}" value="${type === 'password' ? '' : val}" placeholder="${type === 'password' ? '•••• (deixe vazio p/ manter)' : ''}">`;
    return `<div class="col-6"><div class="field"><label class="field__label">${label}</label>${ctl}</div></div>`;
  }).join('');
}

async function saveKeys() {
  const env = {};
  document.querySelectorAll('#keys [data-env]').forEach(el => { if (el.value.trim()) env[el.dataset.env] = el.value.trim(); });
  if (!Object.keys(env).length) return showToast('Nada para salvar', 'error');
  try { await api.post('/system/env', { env }); showToast('Credenciais salvas — reinicie os serviços'); }
  catch (e) { showToast(e.message, 'error'); }
}

// ─── System / manutenção / cache / workers ─────────────────────────────────
async function loadSystem() {
  try {
    const sys = await api.get('/system');
    renderKeys(sys);
    if (sys.prompt_custom) $('sys-prompt').value = sys.prompt_custom;
    $('maint-status').textContent = sys.manutencao ? 'Manutenção ativa — usuários bloqueados.' : 'Operando normalmente.';
  } catch (e) { showToast(e.message, 'error'); }
}

// ─── Configuração dinâmica ─────────────────────────────────────────────────
const VER = {};

function widget(c) {
  const off = c.reconectada ? '' : ' disabled';
  if (c.tipo === 'bool') {
    return `<select class="select" id="dv-${c.chave}" style="max-width:120px"${off}>
      <option ${c.valor === 'true' ? 'selected' : ''}>true</option>
      <option ${c.valor === 'false' ? 'selected' : ''}>false</option></select>`;
  }
  return `<input class="input" id="dv-${c.chave}" type="${c.tipo === 'int' ? 'number' : 'text'}" value="${fmt.esc(c.valor)}" style="max-width:240px"${off}>`;
}

async function loadDyn() {
  try {
    const d = await api.get('/config');
    $('dyn-list').innerHTML = `<table class="table"><thead><tr><th>chave</th><th>valor</th><th></th><th></th></tr></thead><tbody>${
      d.chaves.map(c => {
        VER[c.chave] = c.versao;
        return `<tr${c.reconectada ? '' : ' style="opacity:.55"'}>
          <td>${c.chave}<div class="caption">${c.tipo}${c.reconectada ? ` · v${c.versao}` : ' · aguarda Fase 2/3'}</div></td>
          <td>${widget(c)}</td>
          <td>${c.reconectada ? `<button class="btn btn--primary btn--sm" data-save="${c.chave}">Salvar</button>` : ''}</td>
          <td><button class="btn btn--sm" data-hist="${c.chave}">Histórico</button></td>
        </tr>`;
      }).join('')
    }</tbody></table>`;
    $('dyn-list').querySelectorAll('[data-save]').forEach(b => b.onclick = () => saveDyn(b.dataset.save));
    $('dyn-list').querySelectorAll('[data-hist]').forEach(b => b.onclick = () => histDyn(b.dataset.hist));
  } catch (e) { $('dyn-list').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`; }
}

async function saveDyn(chave) {
  try {
    const d = await api.post('/config', { chave, valor: String($('dv-' + chave).value), versao: VER[chave] });
    showToast(`${chave} = ${d.valor} (v${d.versao})`); loadDyn();
  } catch (e) {
    showToast(e instanceof ApiError && e.isConflict ? `${chave} mudou — recarregado` : `${chave}: ${e.message}`, 'error');
    loadDyn();
  }
}

async function histDyn(chave) {
  $('hist-title').textContent = 'Histórico — ' + chave;
  $('hist').hidden = false;
  $('hist-body').textContent = 'Carregando…';
  try {
    const d = await api.get(`/config/${chave}/historico`);
    $('hist-body').innerHTML = d.historico.map(h => `
      <div style="border-bottom:1px solid var(--line);padding:var(--space-2) 0;display:flex;justify-content:space-between;gap:var(--space-3)">
        <span class="mono">v${h.versao} · <b>${fmt.esc(h.valor_novo)}</b> <span class="caption">(era ${h.valor_antigo == null ? '—' : fmt.esc(h.valor_antigo)})</span></span>
        <span class="caption">${fmt.esc(h.atualizado_por || '?')} · ${fmt.dateTime(h.atualizado_em)}
          <button class="btn btn--sm" data-rev="${h.versao}">Reverter</button></span>
      </div>`).join('') || '<span class="caption">Sem histórico.</span>';
    $('hist-body').querySelectorAll('[data-rev]').forEach(b => b.onclick = async () => {
      if (!await confirmar({ titulo: 'Reverter', corpo: `${chave} volta ao valor da versão ${b.dataset.rev}.`, acao: 'Reverter' })) return;
      try {
        const d = await api.post(`/config/${chave}/reverter`, { para_versao: Number(b.dataset.rev), versao: VER[chave] });
        showToast(`${chave} revertido (v${d.versao})`); $('hist').hidden = true; loadDyn();
      } catch (e) { showToast(e.message, 'error'); }
    });
  } catch (e) { $('hist-body').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`; }
}

// ─── Wire ──────────────────────────────────────────────────────────────────
$('hist-x').onclick = () => $('hist').hidden = true;
$('save-keys').onclick = saveKeys;
$('save-prompt').onclick = async () => { try { await api.post('/system/prompt', { prompt: $('sys-prompt').value }); showToast('Prompt salvo'); } catch (e) { showToast(e.message, 'error'); } };
$('reset-prompt').onclick = async () => { try { await api.post('/system/prompt', { prompt: '' }); $('sys-prompt').value = ''; showToast('Prompt restaurado'); } catch (e) { showToast(e.message, 'error'); } };
$('maint-on').onclick = async () => { try { const d = await api.post('/system/maintenance', { ativo: true }); showToast(d.msg || 'Manutenção ativada'); loadSystem(); } catch (e) { showToast(e.message, 'error'); } };
$('maint-off').onclick = async () => { try { const d = await api.post('/system/maintenance', { ativo: false }); showToast(d.msg || 'Manutenção desativada'); loadSystem(); } catch (e) { showToast(e.message, 'error'); } };
$('clear-cache').onclick = async () => {
  if (!await confirmar({ titulo: 'Limpar cache', corpo: 'Todo o cache semântico é apagado do Redis.', acao: 'Limpar', perigo: true })) return;
  try { const r = await fetch('/api/admin/cache', { method: 'DELETE' }); const d = await r.json(); $('cache-status').textContent = `${d.deleted ?? '?'} entradas removidas.`; showToast('Cache limpo'); }
  catch (e) { showToast(e.message, 'error'); }
};
$('check-workers').onclick = async () => {
  $('workers').textContent = 'Verificando…';
  try {
    const d = await api.get('/celery/health');
    $('workers').innerHTML = d.ok && d.workers?.length
      ? d.workers.map(w => `<div style="padding:var(--space-1) 0"><span class="badge badge--ok">${fmt.esc(w)}</span></div>`).join('')
      : `<span style="color:var(--danger)">Nenhum worker respondendo. ${fmt.esc(d.error || '')}</span>`;
  } catch (e) { $('workers').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`; }
};

loadSystem();
loadDyn();
