/* agents.js — catálogo do Agent Registry (Plano B). */
import { showToast } from '/static/js/core/toast.js';
import { fmt } from '/static/js/core/format.js';

const box = document.getElementById('agents');

async function post(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const d = await r.json();
  if (d.error) throw new Error(d.error);
  return d;
}

function card(a) {
  const tools = (a.tools || []);
  return `<div class="card" data-name="${a.name}">
    <div style="display:flex;justify-content:space-between;gap:var(--space-4);align-items:flex-start">
      <div>
        <div class="card__title">${fmt.esc(a.name)}</div>
        <div class="card__desc" id="d-${a.name}">${fmt.esc(a.description)}</div>
        ${tools.length ? `<div class="caption mono" style="margin-top:var(--space-2)">capabilities: ${tools.map(fmt.esc).join(', ')}</div>` : ''}
        ${a.atualizado_por ? `<div class="caption" style="margin-top:var(--space-1)">editado por ${fmt.esc(a.atualizado_por)} · ${fmt.dateTime(a.atualizado_em)}</div>` : ''}
      </div>
      <div style="display:flex;align-items:center;gap:var(--space-3);flex:none">
        <span class="badge ${a.enabled ? 'badge--ok' : 'badge--neutral'}">${a.enabled ? 'ativo' : 'desligado'}</span>
        <label class="toggle"><input type="checkbox" ${a.enabled ? 'checked' : ''} data-toggle="${a.name}"><span class="toggle__track"></span></label>
      </div>
    </div>
    <div style="display:flex;gap:var(--space-2);margin-top:var(--space-3);flex-wrap:wrap;align-items:center">
      <button class="btn btn--sm" data-edit-desc="${a.name}">Editar descrição</button>
      <a class="btn btn--sm" href="/hub/agents/${a.name}/prompt">Prompt</a>
      <button class="btn btn--sm" data-edit-llm="${a.name}">Modelo LLM</button>
      <span class="caption mono">${a.llm_provider ? fmt.esc(a.llm_provider + (a.llm_model ? ':' + a.llm_model : '')) : 'herda global'}</span>
    </div>
    <div class="field" hidden id="ed-${a.name}" style="margin-top:var(--space-3)">
      <input class="input" id="di-${a.name}" value="${fmt.esc(a.description)}">
      <button class="btn btn--primary btn--sm" data-save-desc="${a.name}" style="align-self:flex-start">Salvar descrição</button>
    </div>
    <div hidden id="el-${a.name}" style="margin-top:var(--space-3);display:flex;gap:var(--space-2);flex-wrap:wrap">
      <select class="select" id="lp-${a.name}" style="max-width:160px">
        <option value="">herda global</option>
        <option ${a.llm_provider === 'gemini' ? 'selected' : ''}>gemini</option>
        <option ${a.llm_provider === 'deepseek' ? 'selected' : ''}>deepseek</option>
        <option ${a.llm_provider === 'groq' ? 'selected' : ''}>groq</option>
      </select>
      <input class="input" id="lm-${a.name}" placeholder="modelo (opcional)" value="${fmt.esc(a.llm_model || '')}" style="max-width:240px">
      <button class="btn btn--primary btn--sm" data-save-llm="${a.name}">Salvar</button>
    </div>
  </div>`;
}

async function load() {
  try {
    const r = await fetch('/hub/agents/data');
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    box.innerHTML = d.agentes.map(card).join('');
    wire();
  } catch (e) {
    box.innerHTML = `<span style="color:var(--danger)">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

function wire() {
  box.querySelectorAll('[data-toggle]').forEach(el => el.onchange = async () => {
    const n = el.dataset.toggle;
    try { await post(`/hub/agents/${n}/toggle`, { enabled: el.checked }); showToast(`${n} ${el.checked ? 'ativado' : 'desligado'}`); load(); }
    catch (e) { el.checked = !el.checked; showToast(e.message, 'error'); }
  });
  box.querySelectorAll('[data-edit-desc]').forEach(el => el.onclick = () => {
    const b = document.getElementById('ed-' + el.dataset.editDesc); b.hidden = !b.hidden;
  });
  box.querySelectorAll('[data-edit-llm]').forEach(el => el.onclick = () => {
    const b = document.getElementById('el-' + el.dataset.editLlm); b.hidden = !b.hidden;
  });
  box.querySelectorAll('[data-save-desc]').forEach(el => el.onclick = async () => {
    const n = el.dataset.saveDesc;
    try { await post(`/hub/agents/${n}/descricao`, { descricao: document.getElementById('di-' + n).value }); showToast(`Descrição de ${n} salva`); load(); }
    catch (e) { showToast(e.message, 'error'); }
  });
  box.querySelectorAll('[data-save-llm]').forEach(el => el.onclick = async () => {
    const n = el.dataset.saveLlm;
    try {
      await post(`/hub/agents/${n}/llm`, {
        llm_provider: document.getElementById('lp-' + n).value || null,
        llm_model: document.getElementById('lm-' + n).value || null,
      });
      showToast(`Modelo de ${n} salvo`); load();
    } catch (e) { showToast(e.message, 'error'); }
  });
}

load();
