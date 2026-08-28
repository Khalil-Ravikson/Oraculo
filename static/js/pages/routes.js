/* routes.js — Registro de rotas (Plano A / Fase 2). Mapa rota→execução,
   editável sem restart. Concorrência otimista por `versao` (409 → recarrega). */
import { api, ApiError } from '/static/js/core/api-client.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';
import { fmt } from '/static/js/core/format.js';

const $ = (id) => document.getElementById(id);
const VER = {};

const OWNER_BADGE = {
  langgraph: 'badge--ok',
  langgraph_conditional: 'badge--warn',
  legacy: 'badge--neutral',
};

function sel(id, val, opts) {
  return `<select class="select select--cell" id="${id}">` +
    opts.map((o) => `<option ${o === val ? 'selected' : ''}>${fmt.esc(o)}</option>`).join('') +
    '</select>';
}

async function load() {
  const box = $('tbl');
  try {
    const d = await api.get('/routes');
    const rows = d.rotas.map((r) => {
      VER[r.rota] = r.versao;
      const p = (campo) => `f-${r.rota}-${campo}`;
      return `<tr>
        <td>
          <span class="rota-nome">${fmt.esc(r.rota)}</span>
          <span class="badge ${OWNER_BADGE[r.owner] || 'badge--neutral'}" style="margin-left:var(--space-2)">v${r.versao}</span>
        </td>
        <td>${sel(p('entrypoint_node'), r.entrypoint_node, d.nodes_validos)}</td>
        <td>${sel(p('owner'), r.owner, d.owners_validos)}</td>
        <td><input class="input input--cell" id="${p('agente')}" value="${fmt.esc(r.agente || '')}" placeholder="—"></td>
        <td class="col-center"><input type="checkbox" class="chk" id="${p('cacheavel')}" ${r.cacheavel ? 'checked' : ''}></td>
        <td class="col-center"><input type="checkbox" class="chk" id="${p('permite_detour')}" ${r.permite_detour ? 'checked' : ''}></td>
        <td><input class="input input--cell" id="${p('doc_type')}" value="${fmt.esc(r.doc_type || '')}" placeholder="—"></td>
        <td><input class="input input--cell input--num" id="${p('k')}" type="number" value="${r.k ?? ''}"></td>
        <td><input class="input input--cell" id="${p('planner_steps')}" value="${fmt.esc((r.planner_steps || []).join(', '))}" placeholder="—"></td>
        <td class="col-actions">
          <button class="btn btn--primary btn--sm" data-save="${fmt.esc(r.rota)}">Salvar</button>
          <button class="btn btn--sm" data-hist="${fmt.esc(r.rota)}">Histórico</button>
        </td>
      </tr>`;
    }).join('');

    box.innerHTML = `<div class="table-wrap"><table class="table table--routes">
      <thead><tr>
        <th>rota</th><th>entrypoint_node</th><th>owner</th><th>agente</th>
        <th>cache</th><th>detour</th><th>doc_type</th><th>k</th><th>planner_steps</th><th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;

    box.querySelectorAll('[data-save]').forEach((b) => (b.onclick = () => salvar(b.dataset.save)));
    box.querySelectorAll('[data-hist]').forEach((b) => (b.onclick = () => abrirHistorico(b.dataset.hist)));
  } catch (e) {
    box.innerHTML = `<div class="table__empty" style="color:var(--danger)">Erro ao carregar: ${fmt.esc(e.message)}</div>`;
  }
}

function coletar(rota) {
  const g = (campo) => $(`f-${rota}-${campo}`);
  const steps = g('planner_steps').value.trim();
  const k = g('k').value.trim();
  return {
    entrypoint_node: g('entrypoint_node').value,
    owner: g('owner').value,
    agente: g('agente').value.trim() || null,
    cacheavel: g('cacheavel').checked,
    permite_detour: g('permite_detour').checked,
    doc_type: g('doc_type').value.trim() || null,
    k: k === '' ? null : Number(k),
    planner_steps: steps === '' ? null : steps.split(',').map((s) => s.trim()).filter(Boolean),
  };
}

async function salvar(rota) {
  try {
    const d = await api.post(`/routes/${rota}`, { campos: coletar(rota), versao: VER[rota] });
    showToast(`${rota} salvo (v${d.versao})`);
    load();
  } catch (e) {
    if (e instanceof ApiError && e.isConflict) {
      showToast(`${rota} mudou noutro lugar — recarregado`, 'error');
    } else {
      showToast(`${rota}: ${e.message}`, 'error');
    }
    load();
  }
}

async function abrirHistorico(rota) {
  $('hist-title').textContent = `Histórico — ${rota}`;
  $('hist-body').innerHTML = '<span class="caption">Carregando…</span>';
  $('hist').hidden = false;
  try {
    const d = await api.get(`/routes/${rota}/historico`);
    $('hist-body').innerHTML = d.historico.map((h) => `
      <div class="hist-item">
        <div class="hist-item__head">
          <span class="badge badge--neutral">v${h.versao}</span>
          <span class="caption">${fmt.esc(h.atualizado_por || '?')} · ${fmt.dateTime(h.atualizado_em)}</span>
          <button class="btn btn--sm" data-rev="${h.versao}">Reverter para v${h.versao}</button>
        </div>
        <pre class="hist-item__snap">${fmt.esc(JSON.stringify(h.snapshot, null, 2))}</pre>
      </div>`).join('') || '<span class="caption">Sem histórico.</span>';
    $('hist-body').querySelectorAll('[data-rev]').forEach((b) => (b.onclick = () => reverter(rota, Number(b.dataset.rev))));
  } catch (e) {
    $('hist-body').innerHTML = `<span style="color:var(--danger)">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

async function reverter(rota, para) {
  if (!(await confirmar({
    titulo: `Reverter ${rota}`,
    corpo: `A rota volta ao estado da versão ${para}. Isso cria uma nova versão no histórico.`,
    acao: `Reverter para v${para}`,
  }))) return;
  try {
    const d = await api.post(`/routes/${rota}/reverter`, { para_versao: para, versao: VER[rota] });
    showToast(`${rota} revertido (v${d.versao})`);
    $('hist').hidden = true;
    load();
  } catch (e) {
    showToast(`Erro ao reverter: ${e.message}`, 'error');
    load();
  }
}

$('hist-x').onclick = () => ($('hist').hidden = true);
$('hist').onclick = (e) => { if (e.target === $('hist')) $('hist').hidden = true; };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('hist').hidden = true; });

load();
