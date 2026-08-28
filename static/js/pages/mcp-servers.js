/* mcp-servers.js — cadastro/toggle/remoção de servidores MCP (Fase 8). */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';

async function load() {
  const el = document.getElementById('servers');
  try {
    const r = await fetch('/hub/mcp-servers/data');
    const d = await r.json();
    if (d.error) throw new Error(d.error);

    if (!d.servers.length) {
      el.innerHTML = '<span class="caption">Nenhum servidor cadastrado.</span>';
      return;
    }

    el.innerHTML = `<table class="table"><thead><tr>
        <th>nome</th><th>url</th><th>descrição</th><th>estado</th><th></th>
      </tr></thead><tbody>${
      d.servers.map(s => `<tr>
        <td class="mono">${fmt.esc(s.name)}</td>
        <td class="url-cell">${fmt.esc(s.url)}</td>
        <td>${fmt.esc(s.description || '—')}</td>
        <td><span class="badge ${s.habilitado ? 'badge--ok' : 'badge--neutral'}">${s.habilitado ? 'habilitado' : 'desabilitado'}</span></td>
        <td class="col-actions">
          <button class="btn btn--sm btn--ghost" data-a="toggle" data-name="${fmt.esc(s.name)}" data-h="${s.habilitado ? 0 : 1}">${s.habilitado ? 'desabilitar' : 'habilitar'}</button>
          <button class="btn btn--sm btn--danger" data-a="remove" data-name="${fmt.esc(s.name)}">remover</button>
        </td>
      </tr>`).join('')
    }</tbody></table>`;

    el.querySelectorAll('button[data-a="toggle"]').forEach(btn => btn.onclick = async () => {
      try {
        const res = await fetch('/hub/mcp-servers/toggle', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: btn.dataset.name, habilitado: btn.dataset.h === '1' }),
        });
        const j = await res.json();
        if (j.error) throw new Error(j.error);
        showToast(`${j.name}: ${j.habilitado ? 'habilitado' : 'desabilitado'}`);
        load();
      } catch (e) { showToast(e.message, 'error'); }
    });

    el.querySelectorAll('button[data-a="remove"]').forEach(btn => btn.onclick = async () => {
      const ok = await confirmar({
        titulo: 'Remover servidor MCP',
        corpo: `Remover o servidor MCP "${btn.dataset.name}"? Esta ação não pode ser desfeita.`,
        acao: 'Remover',
        perigo: true,
      });
      if (!ok) return;
      try {
        const res = await fetch('/hub/mcp-servers/remove', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: btn.dataset.name }),
        });
        const j = await res.json();
        if (j.error) throw new Error(j.error);
        showToast(`${j.name} removido`);
        load();
      } catch (e) { showToast(e.message, 'error'); }
    });
  } catch (e) {
    el.innerHTML = `<span class="badge badge--danger">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

document.getElementById('register-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const body = {
    name: form.name.value.trim(),
    url: form.url.value.trim(),
    description: form.description.value.trim(),
  };
  try {
    const res = await fetch('/hub/mcp-servers/register', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await res.json();
    if (j.error) throw new Error(j.error);
    showToast(`Servidor '${j.name}' cadastrado`);
    form.reset();
    load();
  } catch (e) { showToast(e.message, 'error'); }
});

load();
