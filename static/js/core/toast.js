/* toast.js — showToast(msg, tipo) (Plano B §D). Uma pilha, reusada por toda página. */

const ICON = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/></svg>',
};

function stack() {
  let s = document.querySelector('.toast-stack');
  if (!s) {
    s = document.createElement('div');
    s.className = 'toast-stack';
    document.body.appendChild(s);
  }
  return s;
}

export function showToast(msg, tipo = 'ok', ms = 3600) {
  const el = document.createElement('div');
  el.className = `toast toast--${tipo === 'error' ? 'error' : 'ok'}`;
  el.setAttribute('role', 'status');
  el.innerHTML = `${ICON[tipo] || ICON.ok}<span></span>`;
  el.querySelector('span').textContent = msg;
  stack().appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 200);
  }, ms);
}
