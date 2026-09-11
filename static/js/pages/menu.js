/* menu.js — edição do menu do bot (item C2.5).

   Três invariantes que esta tela precisa respeitar, e que existem porque o
   erro correspondente é invisível aqui e visível no WhatsApp de milhares de
   pessoas:

   1. A pré-visualização é reconstruída aqui com o MESMO formato de
      `menu/spec.py::render()` — abertura, linha em branco, opções, rodapé —
      para responder a cada tecla digitada. O servidor manda a sua própria
      versão em `/menu/data`, e é ela que vale: qualquer divergência aparece
      no primeiro recarregamento depois de salvar. Se as duas se separarem,
      o certo é corrigir esta função, nunca a do servidor.
   2. Concorrência otimista: guardamos a versão que a tela carregou. Se
      alguém salvou no meio, o backend recusa e a tela recarrega em vez de
      sobrescrever o trabalho do outro.
   3. Nada é validado só aqui. O backend revalida o menu inteiro; esta tela
      apenas antecipa os erros mais comuns para não fazer o usuário salvar
      para descobrir. */
import { hub } from '/static/js/core/api-client.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';
import { fmt } from '/static/js/core/format.js';

const $ = (id) => document.getElementById(id);

let CONFIG = null;      // o menu inteiro, como objeto
let VERSAO = 0;         // versão carregada — o optimistic lock
let ROTAS = [];         // rotas de conteúdo disponíveis
let TELA = null;        // id da tela sendo editada
let SUJO = false;

const ACOES = [
  ['submenu',    'Abrir outra tela'],
  ['texto_fixo', 'Mostrar uma resposta pronta'],
  ['pergunta',   'Pedir uma pergunta (consulta a base)'],
  ['handoff',    'Chamar um atendente'],
  ['voltar',     'Voltar'],
];

/* Quantas consultas à inteligência artificial cada tipo de opção custa.
   Mostrado na tela porque é o argumento central do desenho do produto, e
   quem edita o menu precisa enxergar a consequência do que está montando. */
const CUSTO_PERGUNTA = 'uma consulta, ou nenhuma se a pergunta já foi feita antes';

function marcarSujo() {
  SUJO = true;
  $('btn-salvar').disabled = false;
}

/* ── Carga ──────────────────────────────────────────────────────────────── */

async function carregar() {
  const d = await hub.get('/menu/data');
  CONFIG = d.config;
  VERSAO = d.versao || 0;
  ROTAS = d.rotas_de_conteudo || ['WIKI'];

  const badge = $('menu-versao');
  badge.textContent = d.usando_default ? 'texto original' : 'versão ' + d.versao;
  badge.className = 'badge ' + (d.usando_default ? 'badge--neutral' : 'badge--ok');

  $('menu-autoria').textContent = d.atualizado_por
    ? 'última alteração por ' + d.atualizado_por
    : 'nunca editado por aqui';

  SUJO = false;
  $('btn-salvar').disabled = true;

  renderTelas();
  if (TELA && !CONFIG.nos[TELA]) TELA = null;
  if (!TELA) TELA = CONFIG.raiz;
  abrirTela(TELA);
}

/* ── Lista de telas ─────────────────────────────────────────────────────── */

function renderTelas() {
  const ul = $('lista-telas');
  ul.innerHTML = Object.entries(CONFIG.nos).map(([id, no]) => {
    const nome = no.titulo || id;
    const marca = id === CONFIG.raiz ? '<span class="badge badge--neutral">inicial</span>' : '';
    return `<li><button class="menu-telas__item ${id === TELA ? 'is-ativo' : ''}"
      data-tela="${fmt.esc(id)}" data-tech="${fmt.esc(id)}">
      <span>${fmt.esc(nome)}</span>${marca}</button></li>`;
  }).join('');

  ul.querySelectorAll('[data-tela]').forEach((b) => {
    b.addEventListener('click', () => abrirTela(b.dataset.tela));
  });
}

function abrirTela(id) {
  TELA = id;
  const no = CONFIG.nos[id];
  if (!no) return;

  $('editor-vazio').hidden = true;
  $('editor').hidden = false;
  $('tela-abertura').value = no.abertura || '';
  renderOpcoes();
  renderTelas();
  renderPreview();
}

/* ── Opções ─────────────────────────────────────────────────────────────── */

function renderOpcoes() {
  const no = CONFIG.nos[TELA];
  const alvo = $('lista-opcoes');

  alvo.innerHTML = no.opcoes.map((op, i) => {
    const selAcao = ACOES.map(([v, t]) =>
      `<option value="${v}" ${op.acao === v ? 'selected' : ''}>${fmt.esc(t)}</option>`).join('');

    const telas = Object.entries(CONFIG.nos).map(([tid, t]) =>
      `<option value="${fmt.esc(tid)}" ${op.destino === tid ? 'selected' : ''}>${fmt.esc(t.titulo || tid)}</option>`).join('');

    const rotas = ROTAS.map((r) =>
      `<option value="${r}" ${op.rota === r ? 'selected' : ''}>${fmt.esc(assuntoLabel(r))}</option>`).join('');

    return `
    <div class="opcao" data-i="${i}">
      <div class="opcao__linha">
        <input class="input input--tecla" data-campo="tecla" value="${fmt.esc(op.tecla)}" maxlength="2" aria-label="Número">
        <input class="input opcao__label" data-campo="label" value="${fmt.esc(op.label)}" placeholder="O que essa opção diz">
        <select class="select" data-campo="acao">${selAcao}</select>
        <button class="btn btn--ghost btn--sm opcao__remover" data-remover="${i}" aria-label="Remover">✕</button>
      </div>

      <div class="opcao__detalhe" data-quando="submenu" ${op.acao === 'submenu' ? '' : 'hidden'}>
        <label class="campo"><span class="campo__label">Abre a tela</span>
          <select class="select" data-campo="destino">${telas}</select></label>
      </div>

      <div class="opcao__detalhe" data-quando="texto_fixo" ${op.acao === 'texto_fixo' ? '' : 'hidden'}>
        <label class="campo"><span class="campo__label">Resposta pronta</span>
          <textarea class="input" data-campo="texto" rows="3">${fmt.esc(op.texto || '')}</textarea></label>
        <label class="campo"><span class="campo__label">Pergunta de fechamento (opcional)</span>
          <textarea class="input" data-campo="fecho" rows="2">${fmt.esc(op.fecho || '')}</textarea></label>
      </div>

      <div class="opcao__detalhe" data-quando="pergunta" ${op.acao === 'pergunta' ? '' : 'hidden'}>
        <label class="campo"><span class="campo__label">Convite para escrever</span>
          <textarea class="input" data-campo="prompt" rows="2">${fmt.esc(op.prompt || '')}</textarea></label>
        <label class="campo"><span class="campo__label">Onde procurar a resposta</span>
          <select class="select" data-campo="rota">${rotas}</select></label>
        <span class="caption">Esta é a única opção que consulta a inteligência
          artificial. As outras respondem de graça.</span>
      </div>
    </div>`;
  }).join('') || '<p class="caption">Nenhuma opção. Uma tela sem opções deixa a pessoa presa.</p>';

  alvo.querySelectorAll('.opcao').forEach((div) => {
    const i = Number(div.dataset.i);
    div.querySelectorAll('[data-campo]').forEach((el) => {
      el.addEventListener('input', () => aplicarCampo(i, el));
      el.addEventListener('change', () => aplicarCampo(i, el));
    });
    const rm = div.querySelector('[data-remover]');
    if (rm) rm.addEventListener('click', () => removerOpcao(i));
  });
}

/* Rótulo humano para o assunto da busca — a tela nunca mostra o identificador
   interno da rota, que fica só no data-tech. */
function assuntoLabel(rota) {
  const mapa = {
    WIKI:     'Sistemas e acesso (wiki da CTIC)',
    CONTATOS: 'Telefones e setores',
    GERAL:    'Toda a base de conhecimento',
  };
  return mapa[rota] || rota;
}

function aplicarCampo(i, el) {
  const op = CONFIG.nos[TELA].opcoes[i];
  const campo = el.dataset.campo;
  op[campo] = el.value;

  if (campo === 'acao') {
    // Limpa o que não pertence à ação nova — senão um campo de uma ação
    // antiga viaja escondido no documento e reaparece se alguém trocar de
    // volta, com um valor que já não faz sentido.
    if (el.value !== 'submenu' && el.value !== 'voltar') op.destino = '';
    if (el.value !== 'texto_fixo') { op.texto = ''; op.fecho = ''; }
    if (el.value !== 'pergunta') { op.rota = ''; op.doc_type = ''; op.prompt = ''; op.filtros = {}; }
    if (el.value === 'pergunta' && !op.rota) op.rota = ROTAS[0] || 'WIKI';
    if (el.value === 'submenu' && !op.destino) op.destino = CONFIG.raiz;
    renderOpcoes();
  }

  // O assunto define onde buscar; o identificador técnico do acervo anda
  // junto para o backend, sem aparecer na tela.
  if (campo === 'rota') op.doc_type = op.rota === 'CONTATOS' ? 'contatos' : 'wiki_ctic';

  marcarSujo();
  renderPreview();
}

function removerOpcao(i) {
  CONFIG.nos[TELA].opcoes.splice(i, 1);
  renderOpcoes();
  marcarSujo();
  renderPreview();
}

function novaOpcao() {
  const opcoes = CONFIG.nos[TELA].opcoes;
  const usadas = new Set(opcoes.map((o) => o.tecla));
  let tecla = '1';
  for (let n = 1; n <= 8; n++) { if (!usadas.has(String(n))) { tecla = String(n); break; } }

  opcoes.push({
    tecla, label: '', acao: 'texto_fixo', destino: '',
    texto: '', fecho: '', rota: '', doc_type: '', prompt: '', filtros: {},
  });
  renderOpcoes();
  marcarSujo();
  renderPreview();
}

/* ── Pré-visualização ───────────────────────────────────────────────────── */

function renderPreview() {
  const no = CONFIG.nos[TELA];
  if (!no) return;

  // Reconstrução local com o MESMO formato do `render()` do servidor:
  // abertura, linha em branco, opções, linha em branco, rodapé.
  const linhas = [];
  if (no.abertura) { linhas.push(no.abertura.trim(), ''); }
  no.opcoes.forEach((op) => linhas.push(`${op.tecla}  ${op.label}`));
  if (CONFIG.rodape) { linhas.push('', CONFIG.rodape); }

  $('preview-balao').textContent = linhas.join('\n').trim() || '—';

  const paga = no.opcoes.filter((o) => o.acao === 'pergunta').length;
  $('preview-custo').textContent = paga === 0
    ? 'Nenhuma opção desta tela consulta a inteligência artificial.'
    : `${paga} opção(ões) desta tela levam a uma consulta: ${CUSTO_PERGUNTA}.`;
}

/* ── Salvar, histórico, restaurar ───────────────────────────────────────── */

function erros(lista) {
  const box = $('menu-erros');
  if (!lista || !lista.length) { box.hidden = true; box.textContent = ''; return; }
  box.hidden = false;
  box.innerHTML = '<strong>Não deu para salvar:</strong><ul>' +
    lista.map((e) => `<li>${fmt.esc(e)}</li>`).join('') + '</ul>';
}

async function salvar() {
  CONFIG.nos[TELA].abertura = $('tela-abertura').value;
  erros(null);

  let r;
  try {
    r = await hub.post('/menu', { config: CONFIG, versao_esperada: VERSAO });
  } catch (e) {
    const p = e.payload || {};
    if (p.conflito) {
      showToast('Alguém salvou o menu enquanto você editava. Recarregando.', 'error');
      await carregar();
      return;
    }
    erros(p.detalhes || [p.error || 'Falha ao salvar.']);
    return;
  }

  showToast(r.aviso || 'Menu salvo.', 'ok');
  await carregar();
}

async function historico() {
  const painel = $('painel-historico');

  // Alterna: segundo clique fecha, em vez de recarregar a lista à toa.
  if (!painel.hidden) { painel.hidden = true; return; }

  const { historico: h } = await hub.get('/menu/historico');
  if (!h.length) {
    showToast('Ainda não há versões anteriores.', 'ok');
    return;
  }

  painel.innerHTML =
    '<p class="caption">Voltar para uma versão antiga cria uma versão nova — ' +
    'nada é apagado, e dá para desfazer de novo.</p><ul class="lista-historico">' +
    h.slice(0, 15).map((v) =>
      `<li><button class="btn btn--ghost btn--sm" data-rev="${v.versao}">Voltar para a versão ${v.versao}</button>
       <span class="caption">${fmt.esc(v.atualizado_por || '—')} · ${fmt.esc((v.atualizado_em || '').slice(0, 16).replace('T', ' '))}</span></li>`
    ).join('') + '</ul>';
  painel.hidden = false;

  // Os listeners são ligados com o painel JÁ no DOM e aberto. A versão
  // anterior disto ligava depois de um modal fechar, quando os botões já
  // tinham sido removidos — os cliques não faziam nada.
  painel.querySelectorAll('[data-rev]').forEach((b) => {
    b.addEventListener('click', async () => {
      const versao = Number(b.dataset.rev);
      const ok = await confirmar({
        titulo: 'Voltar para a versão ' + versao,
        corpo: '<p>O menu atual será substituído pelo texto daquela versão. ' +
               'Isso cria uma versão nova — dá para desfazer depois.</p>',
        acao: 'Voltar para essa versão',
      });
      if (!ok) return;

      try {
        const r = await hub.post('/menu/reverter', { versao });
        showToast('Menu restaurado como versão ' + r.versao + '.', 'ok');
        painel.hidden = true;
        await carregar();
      } catch (e) {
        const p = e.payload || {};
        erros(p.detalhes || [p.error || 'Falha ao restaurar.']);
      }
    });
  });
}


async function restaurarPadrao() {
  const ok = await confirmar({
    titulo: 'Restaurar o texto original',
    corpo: `<p>Isso substitui todas as telas pelo texto que veio com o sistema.
            Suas edições continuam no histórico e dá para voltar a elas.</p>`,
    acao: 'Restaurar',
  });
  if (!ok) return;

  try {
    await hub.post('/menu/restaurar-padrao', {});
    showToast('Texto original restaurado.', 'ok');
    await carregar();
  } catch (e) {
    showToast((e.payload && e.payload.error) || 'Falha ao restaurar.', 'error');
  }
}

/* ── Início ─────────────────────────────────────────────────────────────── */

$('tela-abertura').addEventListener('input', () => {
  CONFIG.nos[TELA].abertura = $('tela-abertura').value;
  marcarSujo();
  renderPreview();
});
$('btn-add-opcao').addEventListener('click', novaOpcao);
$('btn-salvar').addEventListener('click', salvar);
$('btn-historico').addEventListener('click', historico);
$('btn-padrao').addEventListener('click', restaurarPadrao);

window.addEventListener('beforeunload', (e) => {
  if (!SUJO) return;
  e.preventDefault();
  e.returnValue = '';
});

carregar().catch((e) => {
  console.error(e);
  showToast('Não deu para carregar o menu.', 'error');
});
