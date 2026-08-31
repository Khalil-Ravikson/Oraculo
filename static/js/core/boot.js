/* boot.js — rede de segurança do Hub (Hub v2).
   Se um módulo de página quebrar (ex.: JS antigo em cache referenciando um id
   que o HTML novo não tem mais), o navegador para de executar aquele módulo e
   a página fica "Carregando…" pra sempre, sem pista. Aqui capturamos o erro e
   mostramos o que fazer. Carregado antes dos módulos de página no _shell. */

(function () {
  let avisou = false;

  function avisar(msg) {
    if (avisou) return;
    avisou = true;
    const el = document.createElement('div');
    el.setAttribute('role', 'alert');
    el.style.cssText =
      'position:fixed;left:50%;top:16px;transform:translateX(-50%);z-index:9999;' +
      'background:#1b1f26;border:1px solid #d9483f;border-radius:8px;padding:10px 16px;' +
      'color:#e8eaed;font:500 13px system-ui,sans-serif;max-width:560px;box-shadow:0 8px 24px #0009';
    el.textContent = msg;
    (document.body || document.documentElement).appendChild(el);
  }

  window.addEventListener('error', function (e) {
    if (e && (e.filename || '').includes('/static/js/')) {
      avisar('Um script da página falhou ao carregar. Isso costuma ser versão antiga em cache — recarregue com Ctrl+Shift+R.');
    }
  });

  window.addEventListener('unhandledrejection', function () {
    // silencioso: rejeições de fetch já são tratadas nos módulos; não poluir
  });
})();
