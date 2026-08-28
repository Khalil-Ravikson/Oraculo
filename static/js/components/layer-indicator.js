/* layer-indicator.js — o componente de assinatura (Plano B §B/§D).
   Renderiza a régua de 5 segmentos + o nome da camada resolvida.

   As 5 camadas do Supervisor (regras_negocio §8), da mais barata pra mais cara:
     1 regex        — padrão literal, custo ~0
     2 heurística   — palavra-chave + contexto
     3 regex-config — regex vinda de intents_router (Redis)
     4 semântica    — KNN de embeddings
     5 LLM          — Gemini Flash, último recurso

   Uso:  renderLayerRule(el, { camada: 4, rota: "CALENDARIO" })
*/

export const CAMADAS = ['regex', 'heurística', 'regex-config', 'semântica', 'LLM'];

export function renderLayerRule(el, { camada = 0, rota = '' } = {}) {
  const n = Math.max(0, Math.min(5, Number(camada) || 0));
  el.innerHTML = `
    <span class="layer-chip">
      <span class="layer-rule" data-layer="${n}" role="img"
            aria-label="Rota ${rota || '—'} resolvida na camada ${n} (${CAMADAS[n - 1] || 'não resolvida'})">
        ${'<span class="layer-rule__seg"></span>'.repeat(5)}
      </span>
      <span class="layer-chip__name">${CAMADAS[n - 1] || '—'}</span>
    </span>`;
}
