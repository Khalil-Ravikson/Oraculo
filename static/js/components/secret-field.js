/* secret-field.js — campo de credencial (Hub v2, Sprint 0).
   Input de senha + botão olho (ver/esconder) + botão "Testar Conexão".
   A chave nunca volta preenchida do servidor: o placeholder indica
   "•••• (guardada)" e deixar em branco mantém a atual.

   Uso (programático):
     import { SecretField } from '/static/js/components/secret-field.js';
     const f = SecretField.create({
       label: 'Chave de API',
       filled: true,                       // já existe uma chave salva
       onTest: async (value) => api.post('/providers/x/test-connection', { api_key: value }),
     });
     container.append(f.el);
     f.value;         // string digitada (vazio = manter atual)

   Uso (enhance de markup existente):
     <div class="field" data-secret data-filled="true"><label class="field__label">Chave</label></div>
     SecretField.enhanceAll(document);
*/

const EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9.9 4.2A10.9 10.9 0 0 1 12 4c6.5 0 10 7 10 7a19 19 0 0 1-3.2 4M6.6 6.6A19 19 0 0 0 2 11s3.5 7 10 7a10.9 10.9 0 0 0 4-.8"/><path d="M3 3l18 18"/></svg>';

function create({ label = 'Credencial', name = '', filled = false, hint = '', onTest = null } = {}) {
  const el = document.createElement('div');
  el.className = 'field field--secret';
  el.innerHTML = `
    <label class="field__label"></label>
    <div class="secret-row">
      <input class="input" type="password" autocomplete="off" spellcheck="false"${name ? ` name="${name}"` : ''}>
      <button class="btn btn--ghost btn--icon" type="button" data-eye aria-label="Mostrar credencial">${EYE}</button>
      ${onTest ? '<button class="btn btn--sm" type="button" data-test>Testar Conexão</button>' : ''}
    </div>
    <span class="field__hint"></span>
    <span class="field__status" hidden></span>`;

  el.querySelector('.field__label').textContent = label;
  const input = el.querySelector('input');
  input.placeholder = filled ? '•••• (guardada — deixe em branco para manter)' : 'cole a credencial aqui';
  el.querySelector('.field__hint').textContent = hint;

  const eye = el.querySelector('[data-eye]');
  eye.addEventListener('click', () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    eye.innerHTML = show ? EYE_OFF : EYE;
    eye.setAttribute('aria-label', show ? 'Esconder credencial' : 'Mostrar credencial');
  });

  const statusEl = el.querySelector('.field__status');
  const testBtn = el.querySelector('[data-test]');
  if (testBtn && onTest) {
    testBtn.addEventListener('click', async () => {
      testBtn.disabled = true;
      statusEl.hidden = false;
      statusEl.className = 'field__status field__status--pending';
      statusEl.textContent = 'Testando…';
      try {
        const r = await onTest(input.value);
        const ok = r === true || (r && r.ok !== false && !r.error);
        statusEl.className = `field__status field__status--${ok ? 'ok' : 'err'}`;
        statusEl.textContent = ok ? (r && r.mensagem || 'Conexão OK') : (r && (r.error || r.mensagem) || 'Falhou');
      } catch (e) {
        statusEl.className = 'field__status field__status--err';
        statusEl.textContent = e && e.message ? e.message : 'Falha ao testar';
      } finally {
        testBtn.disabled = false;
      }
    });
  }

  return {
    el,
    get value() { return input.value; },
    set value(v) { input.value = v; },
    focus() { input.focus(); },
  };
}

function enhanceAll(root = document) {
  root.querySelectorAll('[data-secret]:not([data-secret-done])').forEach((holder) => {
    const labelEl = holder.querySelector('.field__label');
    const f = create({
      label: labelEl ? labelEl.textContent : 'Credencial',
      name: holder.dataset.name || '',
      filled: holder.dataset.filled === 'true',
      hint: holder.dataset.hint || '',
    });
    holder.replaceWith(f.el);
    f.el.dataset.secretDone = 'true';
  });
}

export const SecretField = { create, enhanceAll };
