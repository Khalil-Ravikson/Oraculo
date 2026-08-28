const CV = (() => {

const S = {
  source:'file', text:'', fileId:null, fileName:'',
  pageCount:0, curPage:0, pages:[], fullText:'',
  selectedPages: new Set(), // FEATURE 1: Guarda páginas selecionadas
  parser:'auto', strategy:'recursive', size:400,
  overlap:60, docType:'geral',
  chunks:[], view:'text',
  simTimer:null, taskId:null,
  ingestMode: 'all',// Pode ser 'all', 'page', 'selected'
  eixo:'Institucional', setor:'Geral', campus:'Todos', ano:'2026', 
};

// Paleta funcional (chunks adjacentes = cor distinta). Alinhada com as classes
// .ck0-9 de chunkviz.css — dessaturada do neon original, legível no grafite.
const COLORS = [
  { hex: '#3ba55c' }, { hex: '#4a90c7' }, { hex: '#c9982e' }, { hex: '#d97a3f' },
  { hex: '#9a6ec7' }, { hex: '#d9483f' }, { hex: '#5a8dee' }, { hex: '#b98a30' },
  { hex: '#4bb88a' }, { hex: '#c1554e' },
].map((c) => ({ ...c, bg: c.hex + '33', bdr: c.hex + 'b0' }));

const PARSER_HINTS = {
  auto:         'Detecta automaticamente pelo formato do arquivo',
  pymupdf:      'Rápido — ideal para PDFs com texto nativo',
  llamaparse:   'Nuvem — Tabelas e IA (Requer internet e API Key)',
  marker:       'ML — para PDFs com tabelas ou layout complexo (mais lento)',
  docling:      'IBM Docling — layout-aware, ótimo para DOCX e editais',
  csv:          'CSV Semântico — transforma linhas em frases',
  txt:          'Texto puro — sem processamento especial',
};

/* ─── INIT ──────────────────────────────────────────── */
function init() {
  _loadPrefs();
  _setupDnD();
  _wireEvents();
}

/* Liga todos os controles do template (sem handlers inline). */
function _wireEvents() {
  document.querySelectorAll('.cv-tab').forEach((t) => (t.onclick = () => setSource(t.dataset.src)));
  document.querySelectorAll('.cv-setting').forEach((s) => (s.onchange = settingChanged));
  document.querySelectorAll('[data-view]').forEach((b) => (b.onclick = () => setView(b.dataset.view)));

  $('cv-parser').onchange = parserChanged;
  $('cv-warn-close').onclick = () => ($('cv-parser-warning').hidden = true);
  $('cv-fi').onchange = fileSelected;
  $('cv-reset').onclick = resetDocument;
  $('cv-prev').onclick = prevPage;
  $('cv-next').onclick = nextPage;
  $('cv-load-selected').onclick = loadSelectedPages;
  $('cv-load-full').onclick = loadFullDoc;
  $('cv-textarea').oninput = textChanged;
  $('cv-urlinput').onkeydown = (e) => { if (e.key === 'Enter') fetchUrl(); };
  $('cv-url-extract').onclick = fetchUrl;
  $('cv-size').oninput = (e) => sizeChanged(e.target.value);
  $('cv-overlap').oninput = (e) => overlapChanged(e.target.value);
  $('cv-bsim').onclick = simulate;
  $('cv-bingest').onclick = ingest;

  // delegação: miniaturas de página + spans/legenda de chunk (conteúdo dinâmico)
  $('cv-pgstrip').addEventListener('click', (e) => {
    const chk = e.target.closest('[data-page-check]');
    if (chk) { togglePageSelect(e, Number(chk.dataset.pageCheck)); return; }
    const thumb = e.target.closest('[data-page]');
    if (thumb) gotoPage(Number(thumb.dataset.page));
  });
  const hl = (e) => { const el = e.target.closest('[data-ci]'); if (el) hlChunk(Number(el.dataset.ci)); };
  $('cv-textcontent').addEventListener('click', hl);
  $('cv-legend').addEventListener('click', hl);
}

function _loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem('cv_prefs') || '{}');
    if (p.size)     { S.size    = p.size;    $('cv-size').value    = p.size;    $v('cv-sizeval',  p.size);  }
    if (p.overlap)  { S.overlap = p.overlap; $('cv-overlap').value = p.overlap; $v('cv-ovlapval', p.overlap); }
    if (p.strategy) { S.strategy = p.strategy; $('cv-strategy').value = p.strategy; }
    if (p.docType)  { S.docType  = p.docType; }
    if (p.parser)   { S.parser = p.parser; $('cv-parser').value = p.parser; $v('cv-parserhint', PARSER_HINTS[p.parser] || ''); }
    if (p.eixo)   { S.eixo   = p.eixo;   $('cv-eixo').value   = p.eixo;   }
    if (p.setor)  { S.setor  = p.setor;  $('cv-setor').value  = p.setor;  }
    if (p.campus) { S.campus = p.campus; $('cv-campus').value = p.campus; }
    if (p.ano)    { S.ano    = p.ano;    $('cv-ano').value    = p.ano;    }
  } catch(_) {}
}

function _savePrefs() {
  localStorage.setItem('cv_prefs', JSON.stringify({
    size:S.size, overlap:S.overlap, strategy:S.strategy,
    docType:S.docType, parser:S.parser,eixo:S.eixo, setor:S.setor, campus:S.campus, ano:S.ano,
  }));
}

/* ─── FEATURE 3: WARNINGS ───────────────────────────── */
function showWarning(msg) {
  const w = $('cv-parser-warning');
  if (!w) return;
  $('cv-parser-warning-msg').textContent = `${msg}`;
  w.hidden = false;
}

function _checkParserFallback(responseInfo) {
  if (responseInfo.parser_used && responseInfo.parser_used !== S.parser) {
    let msg = `O parser '${S.parser}' falhou ou não estava disponível. `;
    if (S.parser === 'llamaparse') msg += "Verifique sua API Key. ";
    msg += `O sistema usou '${responseInfo.parser_used}' como fallback automático.`;
    showWarning(msg);
  } else {
    $('cv-parser-warning').hidden = true;
  }
}

/* ─── FEATURE 2: RESET DOCUMENT ─────────────────────── */
function resetDocument() {
  S.text = ''; S.fileId = null; S.fileName = '';
  S.pageCount = 0; S.pages = []; S.fullText = '';
  S.selectedPages.clear();
  S.ingestMode = 'all';
  
  $('cv-dz').hidden = false;
  $('cv-fileinfo').hidden = true;
  $('cv-statsbar').hidden = true;
  $('cv-vizarea').children[0].hidden = false; // Mostra empty state
  $('cv-textview').hidden = true;
  $('cv-cardsview').hidden = true;
  $('cv-parser-warning').hidden = true;
  
  _updateSrcStats();
  badge('info', 'Documento removido');
  $('cv-fi').value = ''; // Reseta o input file
}

/* ─── DRAG & DROP ───────────────────────────────────── */
function _setupDnD() {
  const dz = $('cv-dz');
  if (!dz) return;
  dz.addEventListener('click', () => $('cv-fi').click());
  ['dragenter', 'dragover'].forEach((e) => dz.addEventListener(e, (ev) => {
    ev.preventDefault(); dz.classList.add('over');
  }));
  dz.addEventListener('dragleave', () => dz.classList.remove('over'));
  dz.addEventListener('drop', (ev) => {
    ev.preventDefault();
    dz.classList.remove('over');
    const f = ev.dataTransfer?.files?.[0];
    if (f) _upload(f);
  });
}

function fileSelected(e) { const f = e.target.files?.[0]; if (f) _upload(f); }

/* ─── FILE UPLOAD ───────────────────────────────────── */
async function _upload(file) {
  badge('busy', `Enviando ${file.name}…`);
  const fd = new FormData();
  fd.append('file', file);
  fd.append('parser', S.parser);
  try {
    const d = await _post('/hub/chunkviz/upload', fd, true);
    S.fileId    = d.file_id;
    S.fileName  = d.name;
    S.pageCount = d.page_count;
    S.pages     = d.pages;
    S.curPage   = 0;
    S.fullText  = '';
    S.selectedPages.clear(); // Limpa seleção anterior

    // Verifica fallback do backend
    _checkParserFallback(d);

    $('cv-dz').hidden = true;
    $('cv-fileinfo').hidden = false;
    $v('cv-fname', d.name);
    $v('cv-fmeta', `${d.size_kb} KB · ${d.page_count} págs · ${d.total_chars.toLocaleString()} chars`);

    if (d.page_count > 1) {
      $('cv-pagenav').hidden = false;
      $v('cv-pgcur', '1'); $v('cv-pgtot', d.page_count);
      _buildStrip(d.pages);
    } else {
      $('cv-pagenav').hidden = true;
    }

    S.text = d.first_text;
    S.ingestMode = 'page';
    _updateIngestButton();
    _updateSrcStats();
    badge('ok', `${d.name}`);
    _schedSim();
  } catch(e) { badge('err', `${e.message}`); }
}
/* ─── FEATURE 1: MULTIPLE PAGE SELECTION ────────────── */
function _buildStrip(pages) {
  const strip = $('cv-pgstrip');
  // Alteração aqui: Removemos o limite de 60 páginas!
  strip.innerHTML = pages.map((p, i) =>
    `<div class="cv-pgthumb${i === 0 ? ' active' : ''}" data-page="${i}" title="${esc(p.preview)}…">
      <input type="checkbox" class="cv-pgthumb-check" data-page-check="${i}" aria-label="Selecionar página ${i + 1} para ingestão">
      ${i + 1}
    </div>`
  ).join('');
}
function togglePageSelect(event, idx) {
  event.stopPropagation(); // Evita navegar para a página ao clicar no checkbox
  if (event.target.checked) {
    S.selectedPages.add(idx);
  } else {
    S.selectedPages.delete(idx);
  }
}

async function loadSelectedPages() {
  if (S.selectedPages.size === 0) {
    alert("Selecione pelo menos uma página nos checkboxes das miniaturas.");
    return;
  }
  
  badge('busy', `Carregando ${S.selectedPages.size} páginas selecionadas…`);
  try {
    const fd = new FormData();
    fd.append('file_id', S.fileId); 
    // Envia a lista de páginas. Como FormData não suporta array direto de forma simples no Python rápido,
    // enviamos como CSV. (Nota: você precisará ajustar o backend para ler se quiser extrair no Python, 
    // mas aqui vamos concatenar no JS puxando da memória se já temos).
    
    // Para ser eficiente e não mexer na API, vamos buscar apenas a página -1 (TUDO) 
    // e o JS recorta o que precisa se não tivermos na RAM, ou buscamos 1 por 1.
    // Como a API atual só suporta page=X ou page=-1, vamos puxar o texto local S.pages
    
    let combinedText = "";
    const sortedIdx = Array.from(S.selectedPages).sort((a,b)=>a-b);
    
    for (let idx of sortedIdx) {
      // Se não temos o texto na memória completa, buscamos da API
      const fdPage = new FormData();
      fdPage.append('file_id', S.fileId); fdPage.append('page', idx);
      const d = await _post('/hub/chunkviz/page', fdPage, true);
      combinedText += `\n\n--- Página ${idx+1} ---\n\n` + d.text;
    }
    
    S.text = combinedText;
    S.ingestMode = 'selected';
    _updateIngestButton();
    _updateSrcStats();
    badge('ok', `${S.selectedPages.size} páginas prontas para chunk`);
    _schedSim();
  } catch(e) { badge('err', `${e.message}`); }
}

async function gotoPage(idx) {
  if (idx < 0 || idx >= S.pageCount) return;
  S.curPage = idx;
  $v('cv-pgcur', idx + 1);
  document.querySelectorAll('.cv-pgthumb').forEach((t, i) =>
    t.classList.toggle('active', i === idx));
  badge('busy', `Carregando página ${idx + 1}…`);
  try {
    const fd = new FormData();
    fd.append('file_id', S.fileId); fd.append('page', idx);
    const d = await _post('/hub/chunkviz/page', fd, true);
    S.text = d.text;
    S.ingestMode = 'page';
    _updateIngestButton();
    _updateSrcStats();
    badge('ok', `Pág. ${idx+1}/${S.pageCount}`);
    _schedSim();
  } catch(e) { badge('err', `${e.message}`); }
}

async function loadFullDoc() {
  if (!S.fileId) return;
  badge('busy', 'Carregando documento inteiro…');
  try {
    const fd = new FormData();
    fd.append('file_id', S.fileId); fd.append('page', -1);
    const d = await _post('/hub/chunkviz/page', fd, true);
    S.text = d.text;
    S.ingestMode = 'all';
    _updateIngestButton();
    _updateSrcStats();
    badge('ok', `Doc completo — ${S.text.length.toLocaleString()} chars`);
    _schedSim();
  } catch(e) { badge('err', `${e.message}`); }
}

function prevPage() { gotoPage(S.curPage - 1); }
function nextPage() { gotoPage(S.curPage + 1); }

function _updateIngestButton() {
  const btn = $('cv-bingest');
  if (!btn) return;
  if (S.source !== 'file') {
    btn.innerHTML = 'Ingerir ao Redis';
    return;
  }
  if (S.ingestMode === 'all') {
    btn.innerHTML = 'Ingerir DOC INTEIRO';
  } else if (S.ingestMode === 'selected') {
    btn.innerHTML = `Ingerir ${S.selectedPages.size} PÁGS`;
  } else {
    btn.innerHTML = `Ingerir SÓ PÁG ${S.curPage+1}`;
  }
}

/* ─── PARSER ────────────────────────────────────────── */
function parserChanged() {
  S.parser = $('cv-parser').value;
  $v('cv-parserhint', PARSER_HINTS[S.parser] || '');
  _savePrefs();
  if (S.fileId) _schedSim(400);
}

/* ─── TEXT & URL ────────────────────────────────────── */
function textChanged() {
  S.text = $('cv-textarea').value;
  S.ingestMode = 'all'; // Text is always "all"
  _updateIngestButton();
  _updateSrcStats();
  _schedSim();
}

function _updateSrcStats() {
  const ss = $('cv-srcstats');
  if (!S.text) { ss.hidden = true; return; }
  ss.hidden = false;
  const w = S.text.trim() ? S.text.trim().split(/\s+/).length : 0;
  $v('cv-src-chars', S.text.length.toLocaleString());
  $v('cv-src-words', w.toLocaleString());
}

async function fetchUrl() {
  const url = $('cv-urlinput').value.trim();
  if (!url) return;
  badge('busy', 'Fazendo scraping…');
  $v('cv-urlmeta', 'Conectando…');
  try {
    const fd = new FormData();
    fd.append('url', url);
    const d = await _post('/hub/chunkviz/extract-url', fd, true);
    S.fileId = d.file_id; S.fileName = url;
    S.text = d.text;
    S.ingestMode = 'all';
    _updateIngestButton();
    _updateSrcStats();
    $v('cv-urlmeta', `${d.title || url} · ${d.total_chars.toLocaleString()} chars`);
    badge('ok', 'Scraping OK');
    _schedSim();
  } catch(e) {
    $v('cv-urlmeta', `${e.message}`);
    badge('err', `${e.message}`);
  }
}

/* ─── SETTINGS ──────────────────────────────────────── */
function sizeChanged(v)    { S.size    = +v; $v('cv-sizeval',  v); _savePrefs(); _schedSim(); }
function overlapChanged(v) { S.overlap = +v; $v('cv-ovlapval', v); _savePrefs(); _schedSim(); }
function settingChanged()  {
  S.strategy = $('cv-strategy').value;
  S.eixo     = $('cv-eixo').value;
  S.setor    = $('cv-setor').value;
  S.campus   = $('cv-campus').value;
  S.ano      = $('cv-ano').value;
  _savePrefs();
  _schedSim();
}

function setSource(src) {
  S.source = src;
  document.querySelectorAll('.cv-tab').forEach(t => t.classList.toggle('active', t.dataset.src===src));
  ['file','text','url'].forEach(s => {
    const p = $(`cv-panel-${s}`);
    if (p) p.hidden = s !== src;
  });
  _updateIngestButton();
}

/* ─── SIMULATE ──────────────────────────────────────── */
function _schedSim(delay = 700) {
  clearTimeout(S.simTimer);
  if (!S.text) return;
  S.simTimer = setTimeout(simulate, delay);
}

async function simulate() {
  if (!S.text.trim()) { badge('', 'Aguardando texto…'); return; }
  const btn = $('cv-bsim');
  btn.disabled = true;
  btn.innerHTML = '<span class="cv-spin"></span>Calculando…';
  badge('busy', 'Simulando…');
  try {
    const d = await _post('/hub/chunkviz/simulate', {
      text:S.text, size:S.size, overlap:S.overlap,
      strategy:S.strategy, doc_type:S.docType, file_id:S.fileId,
    });
    S.chunks = d.chunks;
    $('cv-statsbar').hidden = false;
    $v('sv-total', d.total);
    $v('sv-avg',   d.avg_size);
    $v('sv-min',   d.min_size);
    $v('sv-max',   d.max_size);
    $v('sv-ovlp',  d.overlap_regions);
    $('cv-bingest').hidden = !(S.fileId || S.source === 'text');
    $('cv-empty').hidden = true;
    renderView();
    badge('ok', `${d.total} chunks`);
  } catch(e) {
    badge('err', `${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Simular';
  }
}

/* ─── VIEW RENDERING ────────────────────────────────── */
function setView(v) {
  S.view = v;
  $('cv-vtext').classList.toggle('active',  v==='text');
  $('cv-vcards').classList.toggle('active', v==='cards');
  renderView();
}

function renderView() {
  $('cv-textview').hidden  = S.view !== 'text';
  $('cv-cardsview').hidden = S.view !== 'cards';
  S.view === 'text' ? _renderText() : _renderCards();
}

function _renderText() {
  if (!S.chunks.length) return;
  const txt = S.text;
  const N   = txt.length;

  const cMap  = new Int32Array(N).fill(-1);
  const ovMap = new Uint8Array(N).fill(0);
  for (let ci = 0; ci < S.chunks.length; ci++) {
    const {start_char: s, end_char: e} = S.chunks[ci];
    for (let j = Math.max(0,s); j < Math.min(N,e); j++) {
      if (cMap[j] !== -1) ovMap[j] = 1;
      cMap[j] = ci;
    }
  }

  let html = '', i = 0;
  while (i < N) {
    const ci = cMap[i], ov = ovMap[i];
    let j = i + 1;
    while (j < N && cMap[j]===ci && ovMap[j]===ov) j++;
    const seg = esc(txt.slice(i, j));
    if (ci === -1) {
      html += `<span>${seg}</span>`;
    } else {
      const cn = ci % COLORS.length;
      const cls = ov ? `ck ck${cn} ckOV` : `ck ck${cn}`;
      html += `<span class="${cls}" data-ci="${ci}" title="Chunk ${ci + 1} · ${S.chunks[ci].length} chars">${seg}</span>`;
    }
    i = j;
  }
  $('cv-textcontent').innerHTML = html;

  const legItems = S.chunks.slice(0, 20).map((ck, ci) => {
    const c = COLORS[ci % COLORS.length];
    return `<div class="cv-leg" data-ci="${ci}" style="background:${c.bg};border-color:${c.bdr};color:${c.hex}" title="${esc(ck.preview)}">
              <div class="cv-legdot" style="background:${c.hex}"></div>
              C${ci + 1} <span class="cv-leg__n">${ck.length}c</span>
            </div>`;
  }).join('');
  $('cv-legend').innerHTML = legItems +
    (S.chunks.length > 20 ? `<div class="cv-leg" style="color:var(--cv-muted)">+${S.chunks.length-20} mais</div>` : '');
}

function hlChunk(ci) {
  document.querySelectorAll('.ck.hl').forEach(e => e.classList.remove('hl'));
  document.querySelectorAll(`[data-ci="${ci}"]`).forEach(e => e.classList.add('hl'));
  const ck = S.chunks[ci];
  if (ck) badge('info', `Chunk ${ci+1}: ${ck.length} chars · pos ${ck.start_char}–${ck.end_char}`);
}

function _renderCards() {
  $('cv-cardsgrid').innerHTML = S.chunks.map((ck, ci) => {
    const c = COLORS[ci % COLORS.length];
    return `<div class="cv-card" style="border-color:${c.hex}">
              <div class="cv-card-hdr" style="border-color:${c.bdr}">
                <span style="color:${c.hex};font-weight:700">Chunk ${ci+1}</span>
                <span style="color:var(--cv-muted)">${ck.length}c · pos ${ck.start_char}</span>
              </div>
              <div class="cv-card-body" style="background:${c.bg}">${esc(ck.text)}</div>
            </div>`;
  }).join('');
}

/* ─── INGEST ────────────────────────────────────────── */
async function ingest() {
  if (!S.fileId && S.source !== 'text') { alert('Carregue um arquivo antes de ingerir.'); return; }
  
  // Como agora o JS pode ter fatiado apenas uma página ou "selecionadas",
  // o modo mais seguro é mandar o TEXTO final gerado pelo JS para um novo endpoint
  // se for parcial, OU usar o endpoint normal se for arquivo inteiro.
  
  const label = S.source === 'text' ? 'TEXTO_DIRETO' : (S.fileName||'DOC').replace(/\.[^/.]+$/,'').toUpperCase().replace(/[-_]/g,' ');
  const btn = $('cv-bingest');
  btn.disabled = true;
  $('cv-ingprog').hidden = false;
  $v('cv-proglbl', 'Iniciando ingestão…');
  $('cv-progfill').style.width = '8%';
  badge('busy', 'Ingerindo…');
  
  try {
    let d;
    // Se o usuário selecionou apenas algumas páginas, o melhor é mandar o S.text gerado como se fosse txt
    if (S.ingestMode !== 'all' || S.source === 'text') {
       // Precisaríamos de um endpoint que ingere texto direto, 
       // mas para manter compatível com sua API, vamos salvar o texto como um novo arquivo temporário (.txt)
       // e mandar o ingest com esse ID.
       const fdTxt = new FormData();
       const blob = new Blob([S.text], { type: 'text/plain' });
       fdTxt.append('file', new File([blob], "selecao_parcial.txt", {type: "text/plain"}));
       fdTxt.append('parser', 'txt');
       const uploadRes = await _post('/hub/chunkviz/upload', fdTxt, true);
       
       d = await _post('/hub/chunkviz/ingest', {
        file_id: uploadRes.file_id, size:S.size, overlap:S.overlap,
        strategy:S.strategy, doc_type:S.docType,
        label: label + " (PARCIAL)", source: "Seleção Parcial", parser: 'txt',eixo:S.eixo, setor:S.setor, campus:S.campus, ano:S.ano,
      });
    } else {
       // Documento inteiro, fluxo normal
       d = await _post('/hub/chunkviz/ingest', {
        file_id:S.fileId, size:S.size, overlap:S.overlap,
        strategy:S.strategy, doc_type:S.docType,
        label:label, source:S.fileName||S.fileId, parser:S.parser,
      });
    }

    $v('cv-proglbl', `Task ${d.task_id.slice(0,8)}… executando`);
    $('cv-progfill').style.width = '25%';
    _pollTask(d.task_id, btn);
  } catch(e) {
    $('cv-ingprog').hidden = true;
    btn.disabled = false;
    badge('err', `${e.message}`);
  }
}

async function _pollTask(tid, btn, polls=0) {
  if (polls > 60) {
    $v('cv-proglbl', 'Timeout — verifique o terminal'); btn.disabled=false; return;
  }
  try {
    const d = await fetch(`/hub/chunkviz/task/${tid}`).then(r=>r.json());
    $('cv-progfill').style.width = `${Math.min(25+polls*2, 90)}%`;
    if (d.state === 'SUCCESS') {
      $('cv-progfill').style.width = '100%';
      const r = d.result || {};
      $v('cv-proglbl', `${r.chunks||'?'} chunks salvos em ${r.ms||'?'}ms`);
      badge('ok', `${r.chunks||'?'} chunks no Redis`);
      btn.disabled = false; return;
    }
    if (d.state === 'FAILURE') {
      $v('cv-proglbl', `${d.error}`); badge('err','Falhou'); btn.disabled=false; return;
    }
    setTimeout(() => _pollTask(tid, btn, polls+1), 2000);
  } catch(_) { setTimeout(() => _pollTask(tid, btn, polls+1), 3000); }
}

/* ─── HELPERS ───────────────────────────────────────── */
function $  (id)    { return document.getElementById(id); }
function $v (id, v) { const e=$(id); if(e) e.textContent=v; }
function esc(s)     { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function badge(type, msg) {
  const b = $('cv-badge');
  if (!b) return;
  b.textContent = msg;
  b.className = type ? `cv-badge ${type}` : 'cv-badge';
}

async function _post(url, body, isForm=false) {
  const opts = { method:'POST' };
  if (isForm) {
    opts.body = body instanceof FormData ? body : (() => { const f=new FormData(); Object.entries(body).forEach(([k,v])=>f.append(k,v)); return f; })();
  } else {
    opts.headers = {'Content-Type':'application/json'};
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  if (!r.ok) {
    const e = await r.json().catch(() => ({detail:`HTTP ${r.status}`}));
    throw new Error(e.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

return { init };
})();

document.addEventListener('DOMContentLoaded', CV.init);