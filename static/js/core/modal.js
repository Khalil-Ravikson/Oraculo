/* modal.js — confirmação genérica (Plano B §D). Substitui window.confirm().
   confirmar({titulo, corpo, acao, perigo}) -> Promise<boolean> */

function build({ titulo, corpo, acao = 'Confirmar', perigo = false }) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="${titulo}">
      <div class="modal__head">
        <span class="modal__title"></span>
        <button class="btn btn--ghost btn--sm" data-x aria-label="Fechar">✕</button>
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
