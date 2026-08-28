/* graph-nodes.js — Registry de nós (Camada 1 / Fase 6-8). Leitura + toggle habilitado. */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';

function portsBlock(label, ports) {
  if (!ports || !ports.length) return '';
  return `
    <div class="node-ports__group">
      <div class="node-ports__group-label">${label}</div>
      ${ports.map(p => `
        <div class="node-port${p.required === false ? ' node-port--optional' : ''}">
          <span>${fmt.esc(p.name)}</span>
          <span class="node-port__type">${fmt.esc(p.type)}</span>
        </div>`).join('')}
    </div>`;
}

function healthBadge(health) {
  if (!health) return '<span class="badge badge--neutral">saúde não monitorada</span>';
  return health.is_healthy
    ? '<span class="badge badge--ok">saudável</span>'
    : `<span class="badge badge--danger" title="${fmt.esc(health.error || '')}">indisponível</span>`;
}

async function load() {
  const el = document.getElementById('nodes');
  try {
    const r = await fetch('/hub/graph-nodes/data');
    const d = await r.json();
    if (d.error) throw new Error(d.error);

    if (!d.nodes.length) {
      el.innerHTML = '<span class="caption">Nenhum nó registrado.</span>';
      return;
    }

    el.innerHTML = d.nodes.map(n => `
      <div class="card">
        <div class="node-card__head">
          <span class="card__title mono">${fmt.esc(n.id)}</span>
          <span class="badge ${n.habilitado ? 'badge--ok' : 'badge--neutral'}">${n.habilitado ? 'habilitado' : 'desabilitado'}</span>
          ${healthBadge(n.health)}
        </div>
        <div class="node-card__type">${fmt.esc(n.type)}</div>
        <div class="card__desc">${fmt.esc(n.metadata.description || '')}</div>
        <div class="node-ports">
          ${portsBlock('entrada', n.input_ports)}
          ${portsBlock('saída', n.output_ports)}
        </div>
        <button class="btn btn--sm btn--ghost" style="margin-top:var(--space-3)" data-id="${fmt.esc(n.id)}" data-h="${n.habilitado ? 0 : 1}">
          ${n.habilitado ? 'Desabilitar' : 'Habilitar'}
        </button>
      </div>`).join('');

    el.querySelectorAll('button[data-id]').forEach(btn => btn.onclick = async () => {
      try {
        const res = await fetch('/hub/graph-nodes/toggle', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ node_id: btn.dataset.id, habilitado: btn.dataset.h === '1' }),
        });
        const j = await res.json();
        if (j.error) throw new Error(j.error);
        showToast(`${j.node_id}: ${j.habilitado ? 'habilitado' : 'desabilitado'}`);
        load();
      } catch (e) { showToast(e.message, 'error'); }
    });
  } catch (e) {
    el.innerHTML = `<span class="badge badge--danger">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

load();
