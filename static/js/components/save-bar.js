/* save-bar.js — barra de salvar flutuante (Hub v2, Sprint 0).
   Aparece só quando há alteração pendente; some ao salvar ou descartar.
   Uma instância por página.

   Uso:
     import { SaveBar } from '/static/js/components/save-bar.js';
     const bar = SaveBar.mount({ onSave: async () => {...}, onDiscard: () => {...} });
     bar.markDirty('rota CALENDARIO');   // registra 1 alteração pendente
     bar.clearDirty('rota CALENDARIO');  // desfez essa alteração
     bar.reset();                        // após salvar/descartar

   Sem framework: manipula um nó fixo no fim do <body>. */

const ICON_SAVE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>';

function build() {
  const el = document.createElement('div');
  el.className = 'save-bar';
  el.setAttribute('role', 'region');
  el.setAttribute('aria-label', 'Alterações não salvas');
  el.dataset.open = 'false';
  el.innerHTML = `
    <span class="save-bar__msg"><span class="save-bar__count">0</span> <span class="save-bar__label">alteração pendente</span></span>
    <div class="save-bar__actions">
      <button class="btn btn--ghost btn--sm" data-discard>Descartar</button>
      <button class="btn btn--primary btn--sm" data-save>${ICON_SAVE} Salvar</button>
    </div>`;
  document.body.appendChild(el);
  return el;
}

export const SaveBar = {
  mount({ onSave, onDiscard } = {}) {
    const el = document.querySelector('.save-bar') || build();
    const dirty = new Set();
    const countEl = el.querySelector('.save-bar__count');
    const labelEl = el.querySelector('.save-bar__label');
    const saveBtn = el.querySelector('[data-save]');
    const discardBtn = el.querySelector('[data-discard]');

    function render() {
      const n = dirty.size;
      el.dataset.open = n > 0 ? 'true' : 'false';
      countEl.textContent = String(n);
      labelEl.textContent = n === 1 ? 'alteração pendente' : 'alterações pendentes';
    }

    const bar = {
      markDirty(id = '_') { dirty.add(id); render(); },
      clearDirty(id = '_') { dirty.delete(id); render(); },
      reset() { dirty.clear(); render(); },
      get count() { return dirty.size; },
      get ids() { return [...dirty]; },
    };

    saveBtn.addEventListener('click', async () => {
      if (!onSave) return;
      saveBtn.disabled = discardBtn.disabled = true;
      try { await onSave(bar); }
      finally { saveBtn.disabled = discardBtn.disabled = false; }
    });
    discardBtn.addEventListener('click', () => {
      onDiscard?.(bar);
      bar.reset();
    });

    window.addEventListener('beforeunload', (e) => {
      if (dirty.size > 0) { e.preventDefault(); e.returnValue = ''; }
    });

    render();
    return bar;
  },
};
