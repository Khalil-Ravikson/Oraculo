/* mcp-servers.js — servidores MCP (Hub v2 Sprint 4): conectar (modal),
   testar (latência + ferramentas), sincronizar para o catálogo, excluir. */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar, formModal } from '/static/js/core/modal.js';
import { hub } from '/static/js/core/api-client.js';

const box = document.getElementById('servers');

function serverCard(s) {
  const lat = s.latency_ms != null ? `${s.latency_ms} ms` : '—';
  const tools = (s.tools_expostas || []);
  return `<div class="card card--resource" data-name="${fmt.esc(s.name)}">
    <div class="card__head">
      <span class="card__title">${fmt.esc(s.name)}</span>
      <span class="badge ${s.habilitado ? 'badge--ok' : 'badge--neutral'} status-pill">${s.habilitado ? 'ativo' : 'desligado'}</span>
    </div>
    <div class="card__desc">${fmt.esc(s.description || '')}</div>
    <dl class="res__meta">
      <dt>endereço</dt><dd>${fmt.esc(s.url)}</dd>
      <dt>latência</dt><dd>${lat}</dd>
      <dt>autenticação</dt><dd>${s.auth_tipo === 'none' ? 'nenhuma' : fmt.esc(s.auth_tipo)}</dd>
      <dt>ferramentas</dt><dd>${tools.length ? tools.map((t) => fmt.esc(t.nome)).join(', ') : '— (sincronize)'}</dd>
    </dl>
    <div class="res__actions">
      <button class="btn btn--sm" data-a="test" data-name="${fmt.esc(s.name)}">Testar Conexão</button>
      <button class="btn btn--sm" data-a="sync" data-name="${fmt.esc(s.name)}">Sincronizar Ferramentas</button>
      <button class="btn btn--sm" data-a="toggle" data-name="${fmt.esc(s.name)}" data-h="${s.habilitado ? 0 : 1}">${s.habilitado ? 'Desligar' : 'Ligar'}</button>
      <button class="btn btn--sm btn--danger" data-a="remove" data-name="${fmt.esc(s.name)}">Excluir</button>
    </div>
  </div>`;
}

async function load() {
  try {
    const d = await hub.get('/mcp-servers/data');
    if (!d.servers.length) {
      box.innerHTML = `<div class="empty">
        <div class="empty__art"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 8h.01M7 17h.01"/></svg></div>
        <div class="empty__title">Nenhum servidor MCP conectado</div>
        <div class="empty__desc">Conecte um servidor para expor ferramentas externas aos agentes.</div>
        <div class="empty__actions"><button class="btn btn--primary btn--sm" id="empty-connect">Conectar Servidor MCP</button></div>
      </div>`;
      document.getElementById('empty-connect').onclick = abrirConectar;
      return;
    }
    box.className = 'card-grid';
    box.innerHTML = d.servers.map(serverCard).join('');
    box.querySelectorAll('[data-a]').forEach((b) => b.onclick = () => acao(b.dataset.a, b.dataset.name, b.dataset.h));
  } catch (e) {
    box.innerHTML = `<span style="color:var(--danger)">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

async function acao(a, name, h) {
  try {
    if (a === 'test') {
      showToast(`Testando ${name}…`);
      const r = await hub.post('/mcp-servers/test', { name });
      showToast(r.ok ? `${name}: ${r.latency_ms} ms · ${r.tools.length} ferramenta(s)` : `${name}: ${r.erro}`, r.ok ? 'ok' : 'error');
      load();
    } else if (a === 'sync') {
      showToast(`Sincronizando ${name}…`);
      const r = await hub.post('/mcp-servers/sync', { name });
      showToast(r.ok ? `${name}: ${r.criadas} nova(s), ${r.total} no total` : `${name}: ${r.erro}`, r.ok ? 'ok' : 'error');
      load();
    } else if (a === 'toggle') {
      await hub.post('/mcp-servers/toggle', { name, habilitado: h === '1' });
      load();
    } else if (a === 'remove') {
      if (!await confirmar({ titulo: `Excluir ${name}`, corpo: 'O servidor some do painel. As ferramentas já sincronizadas continuam no catálogo.', acao: 'Excluir', perigo: true })) return;
      await hub.post('/mcp-servers/remove', { name });
      showToast(`${name} removido`);
      load();
    }
  } catch (e) { showToast(e.message, 'error'); }
}

async function abrirConectar() {
  const corpo = document.createElement('div');
  corpo.innerHTML = `
    <div class="field"><label class="field__label">Nome</label><input class="input" name="name" placeholder="ex: stackexchange" required></div>
    <div class="field"><label class="field__label">Endereço (URL)</label><input class="input" name="url" placeholder="https://gateway.exemplo.io/pack/mcp" required></div>
    <div class="field"><label class="field__label">Descrição (opcional)</label><input class="input" name="description" placeholder="O que este servidor expõe"></div>
    <div class="field"><label class="field__label">Autenticação</label>
      <select class="select" name="auth_tipo"><option value="none">Nenhuma</option><option value="bearer">Bearer Token</option><option value="api_key">API Key</option></select></div>
    <div class="field"><label class="field__label">Variável de ambiente do segredo (se houver)</label><input class="input" name="auth_env" placeholder="ex: STACKEXCHANGE_MCP_KEY">
      <span class="field__hint">O segredo fica no .env; aqui só o nome da variável.</span></div>`;
  const r = await formModal({
    titulo: 'Conectar Servidor MCP', corpo, acao: 'Conectar',
    onSubmit: (form) => {
      const d = Object.fromEntries(new FormData(form));
      if (!d.name || !d.url) throw new Error('Informe nome e endereço');
      return hub.post('/mcp-servers/register', d);
    },
  });
  if (r) { showToast(`Servidor '${r.name}' conectado`); load(); }
}

document.getElementById('btn-connect').onclick = abrirConectar;

load();
