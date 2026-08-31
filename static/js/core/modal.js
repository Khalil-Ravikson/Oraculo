/* modal.js — diálogos do Hub (Plano B §D + Hub v2 Sprint 0).
   - confirmar({titulo, corpo, acao, perigo})            -> Promise<boolean>
   - confirmarComToken({titulo, corpo, token, acao})     -> Promise<boolean>
       exige digitar `token` (ex.: nome do recurso) antes de liberar a ação
       destrutiva. Para FLUSHDB, excluir canal, dropar índice, etc.
   - formModal({titulo, corpo, acao, onSubmit})          -> Promise<any|null>
       `corpo` é HTMLElement ou string com os campos; `onSubmit(formEl)` roda
       ao confirmar — se lançar, o modal fica aberto e mostra o erro. */

function build({ titulo, corpo, acao = 'Confirmar', perigo = false }) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="${titulo}">
      <div class="modal__head">
        <span class="modal__title"></span>
        <button class="btn btn--ghost btn--sm" data-x aria-label="Fechar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
      </div>
      <div class="modal__body"></div>
      <div class="modal__foot">
        <button class="btn" data-no>Cancelar</button>
        <button class="btn ${perigo ? 'btn--danger' : 'btn--primary'}" data-yes></button>
      </div>
    </div>`;
  overlay.querySelector('.modal__title').textContent = titulo;
  overlay.querySelector('.modal__body').textContent = corpo;
  overlay.querySelector('[data-yes]').textContent = acao;
  return overlay;
}

export function confirmar(opts) {
  return new Promise((resolve) => {
    const overlay = build(opts);
    document.body.appendChild(overlay);
    const done = (v) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') done(false); };
    overlay.querySelector('[data-yes]').addEventListener('click', () => done(true));
    overlay.querySelector('[data-no]').addEventListener('click', () => done(false));
    overlay.querySelector('[data-x]').addEventListener('click', () => done(false));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(false); });
    document.addEventListener('keydown', onKey);
    overlay.querySelector('[data-yes]').focus();
  });
}

export function confirmarComToken({ titulo, corpo, token, acao = 'Confirmar' }) {
  return new Promise((resolve) => {
    const overlay = build({ titulo, corpo, acao, perigo: true });
    const body = overlay.querySelector('.modal__body');
    const field = document.createElement('div');
    field.className = 'field';
    field.style.marginTop = 'var(--space-3)';
    field.innerHTML = `<label class="field__label">Digite <code>${token}</code> para confirmar</label><input class="input" data-token autocomplete="off">`;
    body.appendChild(field);

    const yes = overlay.querySelector('[data-yes]');
    yes.disabled = true;
    const inp = overlay.querySelector('[data-token]');
    inp.addEventListener('input', () => { yes.disabled = inp.value.trim() !== token; });

    document.body.appendChild(overlay);
    const done = (v) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') done(false); };
    yes.addEventListener('click', () => { if (!yes.disabled) done(true); });
    overlay.querySelector('[data-no]').addEventListener('click', () => done(false));
    overlay.querySelector('[data-x]').addEventListener('click', () => done(false));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(false); });
    document.addEventListener('keydown', onKey);
    inp.focus();
  });
}

export function formModal({ titulo, corpo, acao = 'Salvar', onSubmit }) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <form class="modal" role="dialog" aria-modal="true" aria-label="${titulo}">
        <div class="modal__head">
          <span class="modal__title">${titulo}</span>
          <button class="btn btn--ghost btn--sm" type="button" data-x aria-label="Fechar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
        </div>
        <div class="modal__body"></div>
        <div class="modal__foot">
          <span class="field__status" data-err hidden></span>
          <button class="btn" type="button" data-no>Cancelar</button>
          <button class="btn btn--primary" type="submit" data-yes>${acao}</button>
        </div>
      </form>`;
    const bodyEl = overlay.querySelector('.modal__body');
    if (corpo instanceof Node) bodyEl.appendChild(corpo);
    else bodyEl.innerHTML = corpo || '';

    const formEl = overlay.querySelector('form');
    const errEl = overlay.querySelector('[data-err]');
    const yes = overlay.querySelector('[data-yes]');

    document.body.appendChild(overlay);
    const done = (v) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') done(null); };

    formEl.addEventListener('submit', async (e) => {
      e.preventDefault();
      errEl.hidden = true;
      yes.disabled = true;
      try {
        const result = onSubmit ? await onSubmit(formEl) : Object.fromEntries(new FormData(formEl));
        done(result ?? Object.fromEntries(new FormData(formEl)));
      } catch (err) {
        errEl.hidden = false;
        errEl.className = 'field__status field__status--err';
        errEl.textContent = err && err.message ? err.message : 'Falha ao salvar';
        yes.disabled = false;
      }
    });
    overlay.querySelector('[data-no]').addEventListener('click', () => done(null));
    overlay.querySelector('[data-x]').addEventListener('click', () => done(null));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(null); });
    document.addEventListener('keydown', onKey);
    const first = bodyEl.querySelector('input, select, textarea');
    if (first) first.focus();
  });
}
