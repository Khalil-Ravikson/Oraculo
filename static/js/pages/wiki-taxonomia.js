/* wiki-taxonomia.js — classificação Sistema/Módulo da wiki, editável pelo
   painel. Substitui `hierarchy.KNOWN_SYSTEM_HUBS` (dict hardcoded). */
import { showToast } from '/static/js/core/toast.js';
import { confirmar, formModal } from '/static/js/core/modal.js';
import { hub } from '/static/js/core/api-client.js';
import { fmt } from '/static/js/core/format.js';

const taxEl = document.getElementById('tax');

let STATE = { classificadas: [] };

function renderTax() {
  const linhas = STATE.classificadas;
  taxEl.innerHTML = `<table class="table"><thead><tr>
      <th>page_id</th><th>Sistema</th><th>Módulo</th><th>Atualizado</th><th></th>
    </tr></thead><tbody>${
    linhas.map((t) => `<tr>
      <td class="mono">${fmt.esc(t.page_id)}</td>
      <td>${fmt.esc(t.sistema)}</td>
      <td>${fmt.esc(t.modulo)}</td>
      <td class="caption">${t.atualizado_por ? fmt.esc(t.atualizado_por) : '—'}</td>
      <td><button class="btn btn--sm btn--danger" data-del="${t.id}" data-page="${fmt.esc(t.page_id)}">Excluir</button></td>
    </tr>`).join('') ||
    '<tr><td colspan="5" class="table__empty">Nenhuma página classificada ainda.</td></tr>'
  }</tbody></table>`;

  taxEl.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => excluir(Number(b.dataset.del), b.dataset.page));
}

async function load() {
  try {
    STATE = await hub.get('/wiki/taxonomia/data');
    renderTax();
  } catch (e) {
    taxEl.innerHTML = `<span style="color:var(--danger)">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

async function excluir(id, pageId) {
  if (!(await confirmar({ titulo: `Remover classificação de "${pageId}"`, corpo: 'A página volta a cair em "Geral" até ser classificada de novo.', acao: 'Remover', perigo: true }))) return;
  try {
    await hub.post(`/wiki/taxonomia/${id}/remover`, {});
    showToast(`"${pageId}" removida da taxonomia`);
    await load();
  } catch (e) { showToast(e.message, 'error'); }
}

document.getElementById('btn-nova').onclick = async () => {
  const corpo = document.createElement('div');
  corpo.innerHTML = `
    <div class="field"><label class="field__label">page_id</label><input class="input" name="page_id" placeholder="ex: almoxarifado" required></div>
    <div class="field"><label class="field__label">Sistema</label><input class="input" name="sistema" placeholder="ex: SIPAC" required></div>
    <div class="field"><label class="field__label">Módulo</label><input class="input" name="modulo" placeholder="ex: Almoxarifado" required></div>`;

  const r = await formModal({
    titulo: 'Classificar página', corpo, acao: 'Salvar',
    onSubmit: async (form) => {
      const d = Object.fromEntries(new FormData(form));
      if (!d.page_id || !d.sistema || !d.modulo) throw new Error('Preencha os três campos');
      return hub.post('/wiki/taxonomia', { page_id: d.page_id, sistema: d.sistema, modulo: d.modulo });
    },
  });
  if (r && !r.error) { showToast(`"${r.page_id}" classificada`); await load(); }
  else if (r && r.error) { showToast(r.error, 'error'); }
};

load();
