/* styleguide.js — demos interativas dos componentes do Hub v2 (Sprint 0). */
import { showToast } from '/static/js/core/toast.js';
import { confirmarComToken, formModal } from '/static/js/core/modal.js';
import { SaveBar } from '/static/js/components/save-bar.js';
import { SecretField } from '/static/js/components/secret-field.js';
import { renderLayerRule, CAMADAS } from '/static/js/components/layer-indicator.js';

// Hero: régua de camadas (assinatura)
const box = document.getElementById('sg-layers');
if (box) {
  CAMADAS.forEach((_, i) => {
    const el = document.createElement('div');
    renderLayerRule(el, { camada: i + 1, rota: ['GREETING', 'WIKI', 'CALENDARIO', 'EDITAL', 'GERAL'][i] });
    box.appendChild(el);
  });
}

// SecretField
const secret = SecretField.create({
  label: 'Chave de API',
  filled: true,
  hint: 'Deixe em branco para manter a chave atual.',
  onTest: async (value) => {
    await new Promise((r) => setTimeout(r, 600));
    return value ? { ok: true, mensagem: 'Conexão OK (demo)' } : { ok: false, error: 'Digite uma chave para testar' };
  },
});
document.getElementById('sg-secret').append(secret.el);

// SaveBar
const bar = SaveBar.mount({
  onSave: async () => { await new Promise((r) => setTimeout(r, 500)); showToast('Alterações salvas (demo)'); bar.reset(); },
  onDiscard: () => showToast('Alterações descartadas', 'error'),
});
document.getElementById('sg-dirty').onclick = () => bar.markDirty('campo-' + (bar.count + 1));
document.getElementById('sg-clean').onclick = () => bar.clearDirty(bar.ids[0]);

// FormModal
document.getElementById('sg-form').onclick = async () => {
  const corpo = document.createElement('div');
  corpo.innerHTML = `
    <div class="field"><label class="field__label">Nome</label><input class="input" name="nome" required></div>
    <div class="field"><label class="field__label">Tipo</label>
      <select class="select" name="tipo"><option value="http">HTTP / REST</option><option value="mcp">Servidor MCP</option></select></div>`;
  const r = await formModal({
    titulo: 'Nova Ferramenta',
    corpo,
    acao: 'Criar',
    onSubmit: (form) => {
      const d = Object.fromEntries(new FormData(form));
      if (!d.nome) throw new Error('Informe um nome');
      return d;
    },
  });
  showToast(r ? `Criada: ${r.nome} (${r.tipo})` : 'Cancelado', r ? 'ok' : 'error');
};

// ConfirmModal com token
document.getElementById('sg-token').onclick = async () => {
  const ok = await confirmarComToken({
    titulo: 'Excluir canal',
    corpo: 'Isto remove a instância e para de receber mensagens dela. Não dá para desfazer.',
    token: 'Instância 01',
    acao: 'Excluir canal',
  });
  showToast(ok ? 'Canal excluído (demo)' : 'Cancelado', ok ? 'ok' : 'error');
};

document.getElementById('sg-toast-ok').onclick = () => showToast('Provedor "openai-uema" salvo');
document.getElementById('sg-toast-err').onclick = () => showToast('Falha ao conectar no servidor MCP', 'error');
