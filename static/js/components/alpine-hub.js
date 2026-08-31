/* alpine-hub.js — componentes Alpine compartilhados do Hub (v2, Sprint 0).
   Registrados no evento `alpine:init` (roda antes de Alpine inicializar o DOM).
   Carregado no _shell.html antes do alpine.min.js (defer preserva a ordem).

   Componentes:
     x-data="tabs('geral')"        abas: $data.tab, tabBtn(id), tabPanel(id)
     x-data="infoBanner('routes')" aviso retrátil, estado persistido por chave
     x-data="reveal()"             mostrar/esconder (accordion simples)
*/

document.addEventListener('alpine:init', () => {
  const Alpine = window.Alpine;

  Alpine.data('tabs', (inicial = '') => ({
    tab: inicial,
    init() {
      const h = (location.hash || '').replace('#', '');
      if (h && this.$root.querySelector(`[data-tab="${CSS.escape(h)}"]`)) this.tab = h;
    },
    select(id) {
      this.tab = id;
      history.replaceState(null, '', '#' + id);
    },
    isTab(id) { return this.tab === id; },
  }));

  Alpine.data('infoBanner', (chave = 'default', abertoPadrao = false) => ({
    aberto: abertoPadrao,
    init() {
      try {
        const v = localStorage.getItem('hub:banner:' + chave);
        if (v !== null) this.aberto = v === '1';
      } catch { /* storage indisponível — usa o padrão */ }
    },
    toggle() {
      this.aberto = !this.aberto;
      try { localStorage.setItem('hub:banner:' + chave, this.aberto ? '1' : '0'); } catch { /* noop */ }
    },
  }));

  Alpine.data('reveal', (abertoPadrao = false) => ({
    aberto: abertoPadrao,
    toggle() { this.aberto = !this.aberto; },
  }));
});
