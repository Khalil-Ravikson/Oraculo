/* capabilities.js — manifestos + vínculo agente↔capability (Plano B / Fase 5). */
import { showToast } from '/static/js/core/toast.js';
import { fmt } from '/static/js/core/format.js';

async function load() {
  const capsEl = document.getElementById('caps');
  const bindEl = document.getElementById('bindings');
  try {
    const r = await fetch('/hub/capabilities/data');
    const d = await r.json();
    if (d.error) throw new Error(d.error);

    capsEl.innerHTML = d.capabilities.map(c => `
      <div class="card">
        <div class="card__title mono">${fmt.esc(c.nome)}
          ${c.confirmacao ? '<span class="badge badge--warn">exige confirmação</span>' : ''}
        </div>
        <div class="card__desc">${fmt.esc(c.descricao)}</div>
        <div class="caption mono" style="margin-top:var(--space-2)">${fmt.esc(c.interface)} · permissões: ${(c.permissoes || []).join(', ') || '—'}</div>
      </div>`).join('');

    bindEl.innerHTML = `<table class="table"><thead><tr><th>agente</th><th>capability</th><th>estado</th><th></th></tr></thead><tbody>${
      d.bindings.map(b => `<tr>
        <td>${fmt.esc(b.agente)}</td>
        <td class="mono">${fmt.esc(b.tool)}</td>
        <td><span class="badge ${b.habilitado ? 'badge--ok' : 'badge--neutral'}">${b.habilitado ? 'ligado' : 'desligado'}</span></td>
        <td><button class="btn btn--sm" data-a="${b.agente}" data-t="${b.tool}" data-h="${b.habilitado ? 0 : 1}">${b.habilitado ? 'desligar' : 'ligar'}</button></td>
      </tr>`).join('') || '<tr><td colspan="4" class="table__empty">Sem vínculos.</td></tr>'
    }</tbody></table>`;

    bindEl.querySelectorAll('button[data-a]').forEach(el => el.onclick = async () => {
      try {
        const res = await fetch('/hub/capabilities/toggle', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agente: el.dataset.a, tool: el.dataset.t, habilitado: el.dataset.h === '1' }),
        });
        const j = await res.json();
        if (j.error) throw new Error(j.error);
        showToast(`${el.dataset.a} ↔ ${el.dataset.t}: ${j.habilitado ? 'ligado' : 'desligado'}`);
        load();
      } catch (e) { showToast(e.message, 'error'); }
    });
  } catch (e) {
    capsEl.innerHTML = `<span style="color:var(--danger)">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

load();
