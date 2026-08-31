/* capabilities.js — ferramentas (código + painel) + vínculo agente↔ferramenta.
   Hub v2 Sprint 2: grid de cards, switch de vínculo, "+ Nova Ferramenta". */
import { showToast } from '/static/js/core/toast.js';
import { confirmar, formModal } from '/static/js/core/modal.js';
import { hub } from '/static/js/core/api-client.js';
import { fmt } from '/static/js/core/format.js';

const capsEl = document.getElementById('caps');
const bindEl = document.getElementById('bindings');
const filtroEl = document.getElementById('filtro-agente');

let STATE = { capabilities: [], bindings: [], mcp_servers: [] };

const ICON_SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>';
const ICON_TOOL = {
  http: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>',
  mcp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="8" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/></svg>',
  codigo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m8 6-6 6 6 6M16 6l6 6-6 6"/></svg>',
};

function capCard(c) {
  const scopes = (c.permissoes || []).map((p) => `<span class="badge badge--neutral">${fmt.esc(p)}</span>`).join(' ');
  const podeRemover = c.origem === 'painel';
  return `<div class="card card--resource" data-cap="${fmt.esc(c.nome)}">
    <div class="card__head">
      <span class="card__title">${ICON_TOOL[c.tipo] || ICON_TOOL.codigo} ${fmt.esc(c.nome)}</span>
      <span class="badge ${c.habilitado ? 'badge--ok' : 'badge--neutral'} status-pill">${c.origem === 'codigo' ? 'de código' : (c.habilitado ? 'ativa' : 'desligada')}</span>
    </div>
    <div class="card__desc">${fmt.esc(c.descricao || '')}</div>
    <div class="u-flex u-wrap u-gap-2">
      ${scopes}
      ${c.confirmacao ? `<span class="badge badge--warn">${ICON_SHIELD} exige confirmação</span>` : ''}
    </div>
    ${podeRemover ? `<div class="res__actions">
      <button class="btn btn--sm" data-test="${c.id}">Testar</button>
      <button class="btn btn--sm" data-toggle="${c.id}" data-h="${c.habilitado ? 0 : 1}">${c.habilitado ? 'Desligar' : 'Ligar'}</button>
      <button class="btn btn--sm btn--danger" data-del="${c.id}" data-nome="${fmt.esc(c.nome)}">Excluir</button>
    </div>` : ''}
  </div>`;
}

function renderCaps() {
  capsEl.innerHTML = STATE.capabilities.map(capCard).join('') ||
    '<div class="empty empty--inline"><div class="empty__title">Nenhuma ferramenta</div><div class="empty__desc">Crie a primeira em "Nova Ferramenta".</div></div>';

  capsEl.querySelectorAll('[data-toggle]').forEach((b) => b.onclick = () => togglar(Number(b.dataset.toggle), b.dataset.h === '1'));
  capsEl.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => excluir(Number(b.dataset.del), b.dataset.nome));
  capsEl.querySelectorAll('[data-test]').forEach((b) => b.onclick = () => testar(Number(b.dataset.test)));
}

function renderBindings() {
  const agentes = [...new Set(STATE.bindings.map((b) => b.agente))].sort();
  const atual = filtroEl.value;
  filtroEl.innerHTML = '<option value="">Todos os agentes</option>' +
    agentes.map((a) => `<option ${a === atual ? 'selected' : ''}>${fmt.esc(a)}</option>`).join('');

  const rows = STATE.bindings.filter((b) => !atual || b.agente === atual);
  bindEl.innerHTML = `<table class="table"><thead><tr><th>Agente</th><th>Ferramenta</th><th>Usa?</th></tr></thead><tbody>${
    rows.map((b) => `<tr>
      <td>${fmt.esc(b.agente)}</td>
      <td class="mono">${fmt.esc(b.tool)}</td>
      <td><label class="toggle"><input type="checkbox" ${b.habilitado ? 'checked' : ''} data-bind data-a="${fmt.esc(b.agente)}" data-t="${fmt.esc(b.tool)}"><span class="toggle__track"></span></label></td>
    </tr>`).join('') || '<tr><td colspan="3" class="table__empty">Sem vínculos.</td></tr>'
  }</tbody></table>`;

  bindEl.querySelectorAll('[data-bind]').forEach((el) => el.onchange = async () => {
    try {
      await hub.post('/capabilities/toggle', { agente: el.dataset.a, tool: el.dataset.t, habilitado: el.checked });
      showToast(`${el.dataset.a} ↔ ${el.dataset.t}: ${el.checked ? 'ligado' : 'desligado'}`);
    } catch (e) { el.checked = !el.checked; showToast(e.message, 'error'); }
  });
}

async function load() {
  try {
    STATE = await hub.get('/capabilities/data');
    renderCaps();
    renderBindings();
  } catch (e) {
    capsEl.innerHTML = `<span style="color:var(--danger)">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

filtroEl.onchange = renderBindings;

async function togglar(id, habilitado) {
  try { await hub.post('/tools/toggle', { tool_id: id, habilitado }); await load(); }
  catch (e) { showToast(e.message, 'error'); }
}

async function excluir(id, nome) {
  if (!(await confirmar({ titulo: `Excluir ${nome}`, corpo: 'A ferramenta some do catálogo. Vínculos com agentes também são perdidos.', acao: 'Excluir', perigo: true }))) return;
  try { await hub.post('/tools/remove', { tool_id: id }); showToast(`${nome} excluída`); await load(); }
  catch (e) { showToast(e.message, 'error'); }
}

async function testar(id) {
  showToast('Testando…');
  try {
    const r = await hub.post('/tools/test', { tool_id: id, args: {} });
    const res = r.resultado || {};
    showToast(res.ok ? 'Ferramenta respondeu OK' : `Falhou: ${res.erro || res.status || '?'}`, res.ok ? 'ok' : 'error');
  } catch (e) { showToast(e.message, 'error'); }
}

document.getElementById('btn-nova').onclick = async () => {
  const corpo = document.createElement('div');
  corpo.innerHTML = `
    <div class="field"><label class="field__label">Nome</label><input class="input" name="nome" placeholder="ex: consulta_protocolo" required></div>
    <div class="field"><label class="field__label">O que faz</label><input class="input" name="descricao" placeholder="descrição curta"></div>
    <div class="field"><label class="field__label">Tipo</label>
      <select class="select" name="tipo">
        <option value="http">Chamada HTTP a uma API</option>
        <option value="mcp">Ferramenta de um servidor MCP</option>
      </select></div>
    <div data-when="http">
      <div class="field"><label class="field__label">Método</label>
        <select class="select" name="metodo"><option>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></div>
      <div class="field"><label class="field__label">URL</label><input class="input" name="url" placeholder="https://api.exemplo.gov.br/protocolo/\${numero}"></div>
      <div class="field"><label class="field__label">Corpo (JSON, opcional — use \${arg})</label><textarea class="textarea" name="corpo_template" rows="2"></textarea></div>
      <div class="field"><label class="field__label">Autenticação</label>
        <select class="select" name="auth_tipo"><option value="">Nenhuma</option><option value="bearer">Bearer token</option><option value="api_key">API Key (header)</option></select></div>
      <div class="field"><label class="field__label">Variável de ambiente da credencial (opcional)</label><input class="input" name="auth_env" placeholder="ex: PROTOCOLO_API_KEY"><span class="field__hint">O valor fica no .env; aqui vai só o nome.</span></div>
    </div>
    <div data-when="mcp" hidden>
      <div class="field"><label class="field__label">Servidor MCP</label>
        <select class="select" name="servidor">${STATE.mcp_servers.map((s) => `<option>${fmt.esc(s)}</option>`).join('') || '<option value="">(nenhum conectado)</option>'}</select></div>
      <div class="field"><label class="field__label">Nome da ferramenta remota</label><input class="input" name="tool_remota" placeholder="ex: search"></div>
    </div>
    <label class="toggle" style="margin-top:var(--space-2)"><input type="checkbox" name="confirmacao"><span class="toggle__track"></span><span>Exige confirmação antes de executar</span></label>`;

  const tipoSel = corpo.querySelector('[name=tipo]');
  const sync = () => {
    corpo.querySelector('[data-when="http"]').hidden = tipoSel.value !== 'http';
    corpo.querySelector('[data-when="mcp"]').hidden = tipoSel.value !== 'mcp';
  };
  tipoSel.onchange = sync; sync();

  const r = await formModal({
    titulo: 'Nova Ferramenta', corpo, acao: 'Criar',
    onSubmit: async (form) => {
      const d = Object.fromEntries(new FormData(form));
      if (!d.nome) throw new Error('Informe um nome');
      const config = d.tipo === 'http'
        ? { metodo: d.metodo, url: d.url, corpo_template: d.corpo_template,
            auth: d.auth_tipo ? { tipo: d.auth_tipo, env: d.auth_env } : {} }
        : { servidor: d.servidor, tool_remota: d.tool_remota };
      return hub.post('/tools', {
        nome: d.nome, tipo: d.tipo, descricao: d.descricao || '',
        config, confirmacao: !!d.confirmacao,
      });
    },
  });
  if (r) { showToast(`Ferramenta "${r.nome}" criada`); await load(); }
};

load();
