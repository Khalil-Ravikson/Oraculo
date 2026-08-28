/* users.js — CRUD de pessoas (Plano B). */
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';
import { fmt } from '/static/js/core/format.js';

const $ = (id) => document.getElementById(id);
let pagina = 1, totalPaginas = 1, timer;

async function u(path, opts = {}) {
  opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (opts.body && typeof opts.body === 'object') opts.body = JSON.stringify(opts.body);
  const r = await fetch('/api/admin/users' + path, opts);
  if (r.status === 204) return null;
  const d = await r.json().catch(() => ({ detail: r.statusText }));
  if (!r.ok) throw new Error(d.detail || d.error || `HTTP ${r.status}`);
  return d;
}

const BADGE = { admin: 'badge--danger', coordenador: 'badge--warn', professor: 'badge--active', servidor: 'badge--active' };

async function load() {
  const qs = new URLSearchParams({ pagina, por_pag: 15 });
  if ($('q').value.trim()) qs.set('busca', $('q').value.trim());
  if ($('f-role').value) qs.set('role', $('f-role').value);
  if ($('f-status').value) qs.set('ativo', $('f-status').value);
  try {
    const d = await u('/?' + qs);
    totalPaginas = Math.max(1, Math.ceil((d.total || 0) / (d.por_pag || 15)));
    $('rows').innerHTML = (d.itens || d.pessoas || []).map(p => `
      <tr>
        <td class="num">${p.id}</td>
        <td>${fmt.esc(p.nome)}</td>
        <td class="mono">${fmt.esc(p.telefone || '—')}</td>
        <td class="caption">${fmt.esc(p.email || '—')}</td>
        <td><span class="badge ${BADGE[p.role] || 'badge--neutral'}">${fmt.esc(p.role)}</span></td>
        <td><span class="badge ${p.ativo ? 'badge--ok' : 'badge--neutral'}">${p.ativo ? 'ativo' : 'inativo'}</span></td>
        <td style="white-space:nowrap">
          <button class="btn btn--sm" data-edit='${fmt.esc(JSON.stringify(p))}'>Editar</button>
          <button class="btn btn--sm" data-toggle="${p.id}" data-a="${p.ativo ? 1 : 0}">${p.ativo ? 'Desativar' : 'Ativar'}</button>
          <button class="btn btn--sm btn--danger" data-del="${p.id}" data-n="${fmt.esc(p.nome)}">Excluir</button>
        </td>
      </tr>`).join('') || '<tr><td colspan="7" class="table__empty">Nenhuma pessoa encontrada.</td></tr>';
    $('pag-info').textContent = `${d.total ?? 0} pessoas · página ${pagina}/${totalPaginas}`;
    $('prev').disabled = pagina <= 1;
    $('next').disabled = pagina >= totalPaginas;
    wire();
  } catch (e) { $('rows').innerHTML = `<tr><td colspan="7" style="color:var(--danger)">${fmt.esc(e.message)}</td></tr>`; }
}

async function stats() {
  try {
    const [tot, ativos, est, prof, adm] = await Promise.all([
      u('/?por_pag=1'), u('/?ativo=true&por_pag=1'), u('/?role=estudante&por_pag=1'),
      u('/?role=professor&por_pag=1'), u('/?role=admin&por_pag=1'),
    ]);
    $('stats').innerHTML = [
      ['total', tot.total], ['ativos', ativos.total], ['estudantes', est.total],
      ['professores', prof.total], ['admins', adm.total],
    ].map(([l, v]) => `<div class="col-3"><div class="card card--stat"><div class="stat__value tabular">${fmt.num(v ?? 0)}</div><div class="stat__label">${l}</div></div></div>`).join('');
  } catch { /* silencioso */ }
}

function wire() {
  $('rows').querySelectorAll('[data-edit]').forEach(b => b.onclick = () => abrir(JSON.parse(b.dataset.edit)));
  $('rows').querySelectorAll('[data-toggle]').forEach(b => b.onclick = async () => {
    if (!await confirmar({ titulo: b.dataset.a === '1' ? 'Desativar' : 'Ativar', corpo: `Pessoa #${b.dataset.toggle}.`, acao: 'Confirmar' })) return;
    try { await u(`/${b.dataset.toggle}/toggle`, { method: 'PATCH' }); showToast('Estado alterado'); load(); stats(); }
    catch (e) { showToast(e.message, 'error'); }
  });
  $('rows').querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (!await confirmar({ titulo: 'Excluir pessoa', corpo: `${b.dataset.n} (#${b.dataset.del}) — permanente.`, acao: 'Excluir', perigo: true })) return;
    try { await u(`/${b.dataset.del}`, { method: 'DELETE' }); showToast('Pessoa excluída'); load(); stats(); }
    catch (e) { showToast(e.message, 'error'); }
  });
}

function abrir(p = null) {
  $('edit-id').value = p?.id || '';
  $('m-title').textContent = p ? `Editar #${p.id}` : 'Nova pessoa';
  $('f-nome').value = p?.nome || '';
  $('f-telefone').value = p?.telefone || '';
  $('f-telefone').disabled = !!p;
  $('f-email').value = p?.email || '';
  $('f-role2').value = p?.role || 'estudante';
  $('f-turno').value = p?.turno || '';
  $('f-curso').value = p?.curso || '';
  $('f-matricula').value = p?.matricula || '';
  $('f-ativo').checked = p ? !!p.ativo : true;
  $('modal').hidden = false;
}

$('m-x').onclick = $('m-cancel').onclick = () => $('modal').hidden = true;
$('modal').onclick = (e) => { if (e.target === $('modal')) $('modal').hidden = true; };
$('novo').onclick = () => abrir();
$('m-save').onclick = async () => {
  const id = $('edit-id').value;
  const body = {
    nome: $('f-nome').value.trim(), email: $('f-email').value.trim() || null,
    role: $('f-role2').value, turno: $('f-turno').value || null,
    curso: $('f-curso').value.trim() || null, matricula: $('f-matricula').value.trim() || null,
    ativo: $('f-ativo').checked,
  };
  if (!id) body.telefone = $('f-telefone').value.trim();
  if (!body.nome || (!id && !body.telefone)) return showToast('Nome e telefone são obrigatórios', 'error');
  try {
    if (id) await u(`/${id}`, { method: 'PUT', body }); else await u('/', { method: 'POST', body });
    showToast(id ? 'Atualizado' : 'Criado'); $('modal').hidden = true; load(); stats();
  } catch (e) { showToast(e.message, 'error'); }
};
$('prev').onclick = () => { pagina = Math.max(1, pagina - 1); load(); };
$('next').onclick = () => { pagina = Math.min(totalPaginas, pagina + 1); load(); };
[$('q'), $('f-role'), $('f-status')].forEach(el => el.oninput = el.onchange = () => { clearTimeout(timer); timer = setTimeout(() => { pagina = 1; load(); }, 300); });

load();
stats();
