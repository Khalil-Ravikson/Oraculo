/* routes.js — Mapa de rotas (o "e depois?" de cada assunto). Vale sem
   reiniciar. Concorrência otimista por versão (409 → recarrega). */
import { api, ApiError } from '/static/js/core/api-client.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar, confirmarComToken, formModal } from '/static/js/core/modal.js';
import { fmt } from '/static/js/core/format.js';
import { Glossario } from '/static/js/core/glossario.js';

const $ = (id) => document.getElementById(id);
const VER = {};
let NODES_VALIDOS = [];
let OWNERS_VALIDOS = [];

const OWNER_BADGE = {
  langgraph: 'badge--ok',
  langgraph_conditional: 'badge--warn',
  legacy: 'badge--neutral',
};
const motorLabel = (o) => Glossario.rotulo('owner:' + o, o);
const rotaLabel = (r) => Glossario.rotulo('rota:' + r, r);
const noLabel = (n) => Glossario.rotulo('no:' + n, n);
const stepLabel = (s) => Glossario.rotulo('step:' + s, s);
const agenteLabel = (a) => Glossario.rotulo('agente:' + a, a || '—');

// <select> onde o value continua sendo o termo real, mas o texto é traduzido
function sel(id, val, opts, labelFn) {
  const label = labelFn || ((o) => o);
  return `<select class="select select--cell" id="${id}">` +
    opts.map((o) => `<option value="${fmt.esc(o)}" ${o === val ? 'selected' : ''}>${fmt.esc(label(o))}</option>`).join('') +
    '</select>';
}

// rótulos legíveis dos campos, para o histórico (planner_steps some da UI —
// Planner em aposentadoria, ADR 0008 — mas fica aqui p/ histórico antigo)
const CAMPO_LABEL = {
  entrypoint_node: 'Ponto de entrada', owner: 'Motor', agente: 'Agente',
  cacheavel: 'Cache', permite_detour: 'Desvio', doc_type: 'Tipo de documento',
  k: 'Trechos', planner_steps: 'Passos do planejador (legado)',
};
function valorLegivel(campo, v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'sim' : 'não';
  if (campo === 'entrypoint_node') return noLabel(v);
  if (campo === 'owner') return motorLabel(v);
  if (campo === 'agente') return agenteLabel(v);
  if (campo === 'planner_steps') return (Array.isArray(v) ? v : [v]).map(stepLabel).join(', ');
  return String(v);
}

async function load() {
  const box = $('tbl');
  try {
    const d = await api.get('/routes');
    NODES_VALIDOS = d.nodes_validos || [];
    OWNERS_VALIDOS = d.owners_validos || [];
    const rows = d.rotas.map((r) => {
      VER[r.rota] = r.versao;
      const p = (campo) => `f-${r.rota}-${campo}`;
      const selo = r.fixa === false ? ' <span class="badge badge--neutral">personalizada</span>' : '';
      return `<tr>
        <td>
          <span class="rota-nome" data-tech="${fmt.esc(r.rota)}">${fmt.esc(rotaLabel(r.rota))}</span>
          <span class="badge ${OWNER_BADGE[r.owner] || 'badge--neutral'}" data-tech="owner:${fmt.esc(r.owner)}" title="versão ${r.versao}">${fmt.esc(motorLabel(r.owner))}</span>${selo}
        </td>
        <td>${sel(p('entrypoint_node'), r.entrypoint_node, d.nodes_validos, noLabel)}</td>
        <td>${sel(p('owner'), r.owner, d.owners_validos, motorLabel)}</td>
        <td><input class="input input--cell" id="${p('agente')}" value="${fmt.esc(r.agente || '')}" placeholder="—" title="nome interno do agente"></td>
        <td class="col-center"><input type="checkbox" class="chk" id="${p('cacheavel')}" ${r.cacheavel ? 'checked' : ''}></td>
        <td class="col-center"><input type="checkbox" class="chk" id="${p('permite_detour')}" ${r.permite_detour ? 'checked' : ''}></td>
        <td><input class="input input--cell" id="${p('doc_type')}" value="${fmt.esc(r.doc_type || '')}" placeholder="—"></td>
        <td><input class="input input--cell input--num" id="${p('k')}" type="number" value="${r.k ?? ''}"></td>
        <td class="col-actions">
          <button class="btn btn--primary btn--sm" data-save="${fmt.esc(r.rota)}">Salvar</button>
          <button class="btn btn--sm" data-hist="${fmt.esc(r.rota)}">Histórico</button>
          ${r.fixa === false ? `<button class="btn btn--sm btn--danger" data-del="${fmt.esc(r.rota)}">Apagar</button>` : ''}
        </td>
      </tr>`;
    }).join('');

    box.innerHTML = `<div class="table-wrap"><table class="table table--routes">
      <thead><tr>
        <th>Rota</th>
        <th>Ponto de entrada</th>
        <th>Motor</th>
        <th>Agente</th>
        <th title="A resposta pode ser reaproveitada do cache?">Cache</th>
        <th title="Pode desviar para outra rota no meio?">Desvio</th>
        <th>Tipo de documento</th>
        <th title="Quantos trechos de documento buscar">Trechos</th>
        <th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;

    box.querySelectorAll('[data-save]').forEach((b) => (b.onclick = () => salvar(b.dataset.save)));
    box.querySelectorAll('[data-hist]').forEach((b) => (b.onclick = () => abrirHistorico(b.dataset.hist)));
    box.querySelectorAll('[data-del]').forEach((b) => (b.onclick = () => apagarRota(b.dataset.del)));
  } catch (e) {
    box.innerHTML = `<div class="table__empty" style="color:var(--danger)">Erro ao carregar: ${fmt.esc(e.message)}</div>`;
  }
}

function coletar(rota) {
  const g = (campo) => $(`f-${rota}-${campo}`);
  const k = g('k').value.trim();
  // `planner_steps` não é mais editável aqui (Planner em aposentadoria) —
  // o upsert do route_registry é parcial, então não mandar = manter o valor.
  return {
    entrypoint_node: g('entrypoint_node').value,
    owner: g('owner').value,
    agente: g('agente').value.trim() || null,
    cacheavel: g('cacheavel').checked,
    permite_detour: g('permite_detour').checked,
    doc_type: g('doc_type').value.trim() || null,
    k: k === '' ? null : Number(k),
  };
}

async function salvar(rota) {
  try {
    const d = await api.post(`/routes/${rota}`, { campos: coletar(rota), versao: VER[rota] });
    showToast(`${rotaLabel(rota)} salvo (v${d.versao})`);
    load();
  } catch (e) {
    if (e instanceof ApiError && e.isConflict) {
      showToast(`${rotaLabel(rota)} mudou noutro lugar — recarregado`, 'error');
    } else {
      showToast(`${rotaLabel(rota)}: ${e.message}`, 'error');
    }
    load();
  }
}

function snapshotLegivel(snap) {
  if (!snap || typeof snap !== 'object') return '<span class="caption">—</span>';
  const linhas = Object.entries(CAMPO_LABEL)
    .filter(([campo]) => campo in snap)
    .map(([campo, label]) => `<div class="hist-campo"><span>${label}</span><span class="mono">${fmt.esc(valorLegivel(campo, snap[campo]))}</span></div>`);
  return linhas.join('') || '<span class="caption">—</span>';
}

async function abrirHistorico(rota) {
  $('hist-title').textContent = `Histórico — ${rotaLabel(rota)}`;
  $('hist-body').innerHTML = '<span class="caption">Carregando…</span>';
  $('hist').hidden = false;
  try {
    const d = await api.get(`/routes/${rota}/historico`);
    $('hist-body').innerHTML = d.historico.map((h) => `
      <div class="hist-item">
        <div class="hist-item__head">
          <span class="badge badge--neutral">versão ${h.versao}</span>
          <span class="caption">${fmt.esc(h.atualizado_por || '?')} · ${fmt.dateTime(h.atualizado_em)}</span>
          <button class="btn btn--sm" data-rev="${h.versao}">Voltar para esta</button>
        </div>
        <div class="hist-item__snap">${snapshotLegivel(h.snapshot)}</div>
      </div>`).join('') || '<span class="caption">Sem histórico.</span>';
    $('hist-body').querySelectorAll('[data-rev]').forEach((b) => (b.onclick = () => reverter(rota, Number(b.dataset.rev))));
  } catch (e) {
    $('hist-body').innerHTML = `<span style="color:var(--danger)">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

async function reverter(rota, para) {
  if (!(await confirmar({
    titulo: `Reverter ${rotaLabel(rota)}`,
    corpo: `Volta ao estado da versão ${para}. Isso cria uma nova versão no histórico.`,
    acao: `Voltar para a versão ${para}`,
  }))) return;
  try {
    const d = await api.post(`/routes/${rota}/reverter`, { para_versao: para, versao: VER[rota] });
    showToast(`${rotaLabel(rota)} revertido (v${d.versao})`);
    $('hist').hidden = true;
    load();
  } catch (e) {
    showToast(`Erro ao reverter: ${e.message}`, 'error');
    load();
  }
}

// ── Criar / apagar rota personalizada ─────────────────────────────────────
async function apagarRota(rota) {
  const ok = await confirmarComToken({
    titulo: `Apagar ${rota}`,
    corpo: 'A rota e o gatilho de classificação são removidos. Mensagens que iam para ela voltam a ser classificadas normalmente.',
    token: rota, acao: 'Apagar',
  });
  if (!ok) return;
  try {
    await api.del(`/routes/${rota}`);
    showToast(`${rota} apagada`);
    load();
  } catch (e) { showToast(`Erro ao apagar: ${e.message}`, 'error'); }
}

function opt(lista, sel) {
  return lista.map((o) => `<option value="${fmt.esc(o)}"${o === sel ? ' selected' : ''}>${fmt.esc(o)}</option>`).join('');
}

$('btn-nova-rota').onclick = async () => {
  const corpo = document.createElement('div');
  corpo.innerHTML = `
    <div class="field"><label class="field__label">Nome da rota</label>
      <input class="input" name="nome" placeholder="TESTE_GUI" required>
      <span class="field__hint">MAIÚSCULAS, dígitos ou "_", 3–24 caracteres.</span></div>
    <div class="field"><label class="field__label">Gatilho — expressão que ativa a rota</label>
      <input class="input" name="regex" placeholder="teste de gui">
      <span class="field__hint">Deixe em branco para uma rota sem classificação automática.</span></div>
    <div class="field"><label class="field__label">Frases de exemplo (uma por linha)</label>
      <textarea class="input" name="exemplos" rows="2" placeholder="quero fazer um teste de GUI"></textarea></div>
    <div class="field"><label class="field__label">Ponto de entrada</label>
      <select class="select" name="entrypoint_node">${opt(NODES_VALIDOS, 'rag')}</select></div>
    <div class="field"><label class="field__label">Motor</label>
      <select class="select" name="owner">${opt(OWNERS_VALIDOS, 'legacy')}</select></div>
    <div class="field"><label class="field__label">Agente (opcional)</label>
      <input class="input" name="agente" placeholder="academic_knowledge"></div>
    <div class="field"><label class="field__label">Trechos de documento (k)</label>
      <input class="input input--num" name="k" type="number" value="0"></div>`;
  const r = await formModal({
    titulo: 'Nova rota', corpo, acao: 'Criar',
    onSubmit: (form) => {
      const f = Object.fromEntries(new FormData(form));
      if (!f.nome) throw new Error('Informe o nome');
      const campos = {
        entrypoint_node: f.entrypoint_node,
        owner: f.owner,
        agente: f.agente.trim() || null,
        k: f.k === '' ? null : Number(f.k),
      };
      const body = { nome: f.nome, campos };
      if (f.regex.trim()) {
        body.gatilho = {
          regex: f.regex.trim(),
          exemplos: f.exemplos.split('\n').map((s) => s.trim()).filter(Boolean),
        };
      }
      return api.post('/routes', body);
    },
  });
  if (r) { showToast(`Rota '${r.rota}' criada`); load(); }
};

$('hist-x').onclick = () => ($('hist').hidden = true);
$('hist').onclick = (e) => { if (e.target === $('hist')) $('hist').hidden = true; };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('hist').hidden = true; });

load();
