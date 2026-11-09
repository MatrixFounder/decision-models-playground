// Wiring: tabs, the draft, engines, estimate, run over SSE, budget, language (DESIGN §16).

import * as api from './api.js';
import { History } from './history.js';
import { applyStatic, getLang, setLang, t } from './i18n.js';
import { el, fromRequest, QuestionEditor, toRequest } from './questions.js';
import { fmtUsd, renderResults } from './results.js';

const $ = (s) => document.querySelector(s);
const DRAFT_KEY = 'draft.v1';
const DEFAULT_PRESET = 'demo_bank_call';

const ui = {
  engines: [],
  selected: new Set(),
  stateMode: 'text',
  qJson: false,
  view: null,
  running: false,
  tab: 'workbench',
};

const editor = new QuestionEditor($('#questions'), () => onDraftChange());
const history = new History($('#history'), { onOpen: openRun, onRerun: rerunRun });

// --- storage (a per-viewer convenience: the page works without it) -------------------------------------------------
function readDraft() {
  try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch { return null; }
}
function saveDraft() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({
      state: $('#state').value, stateMode: ui.stateMode, questions: editor.list, qJson: ui.qJson,
      qJsonText: $('#questions-json').value, selected: [...ui.selected], title: $('#title').value, tags: $('#tags').value,
    }));
  } catch { /* storage blocked */ }
}

// --- the draft → POST body -----------------------------------------------------------------------------------------
function currentQuestions() {
  if (!ui.qJson) return toRequest(editor.list);
  try { return JSON.parse($('#questions-json').value || '{}'); } catch (e) { throw new Error(t('json_invalid', { msg: e.message })); }
}
function currentState() {
  const text = $('#state').value;
  if (ui.stateMode !== 'json') return text;
  try { return JSON.parse(text); } catch (e) { throw new Error(t('json_invalid', { msg: e.message })); }
}
function runBody() {
  return {
    request: { state: currentState(), questions: currentQuestions() },
    engines: [...ui.selected],
    tags: $('#tags').value.split(',').map((s) => s.trim()).filter(Boolean),
    title: $('#title').value.trim() || null,
    options: { allow_no_zdr: $('#allow-no-zdr').checked },
  };
}

// --- estimate ------------------------------------------------------------------------------------------------------
let estimateTimer = null;
let estimateSeq = 0;
function onDraftChange() {
  $('#state-info').textContent = t('state_info', { chars: $('#state').value.length });
  saveDraft();
  clearTimeout(estimateTimer);
  estimateTimer = setTimeout(updateEstimate, 350);
}
async function updateEstimate() {
  const seq = ++estimateSeq;
  const out = $('#estimate');
  const err = $('#run-error');
  if (!ui.selected.size) { out.textContent = ''; err.textContent = ''; return; }
  let body;
  try { body = runBody(); } catch (e) { out.textContent = ''; err.textContent = e.message; return; }
  try {
    const est = await api.estimate(body);
    if (seq !== estimateSeq) return;
    err.textContent = '';
    out.textContent = `${t('estimate')} ${fmtUsd(est.total_usd)} · ${t('remaining')} ${fmtUsd(est.budget.remaining_usd)}`;
    out.classList.toggle('warn', est.total_usd > est.budget.remaining_usd);
  } catch (e) {
    if (seq !== estimateSeq) return;
    out.textContent = '';
    err.textContent = e.message;
  }
}

// --- engines -------------------------------------------------------------------------------------------------------
function engineRow(e) {
  const badges = [];
  if (e.paid) {
    badges.push(el('span', { class: 'chip' }, e.price_per_mtok == null ? '$?' : `$${e.price_per_mtok}${t('per_mtok')}`));
    const z = e.zdr_endpoint;
    badges.push(el('span', { class: `chip ${z === false ? 'warn' : ''}` }, t(z === true ? 'zdr_yes' : z === false ? 'zdr_no' : 'zdr_unknown')));
    if (e.expiration_date) badges.push(el('span', { class: 'chip warn' }, `⌛ ${e.expiration_date}`));
  } else if (e.available) {
    badges.push(el('span', { class: 'chip' }, t('free')));
  }
  const box = el('input', { type: 'checkbox', checked: ui.selected.has(e.id), disabled: !e.available,
    onchange: (ev) => { if (ev.target.checked) ui.selected.add(e.id); else ui.selected.delete(e.id); syncZdrBox(); onDraftChange(); } });
  return el('label', { class: `engine${e.available ? '' : ' off'}` }, box,
    el('div', { class: 'engine-main' },
      el('div', {}, el('b', {}, e.id), ' ', el('span', { class: 'dim small mono' }, e.model || '')),
      el('div', { class: 'small' }, badges, e.available ? '' : el('span', { class: 'dim' }, e.reason))));
}
function renderEngines() {
  const on = ui.engines.filter((e) => e.available);
  const off = ui.engines.filter((e) => !e.available);
  const rows = on.map(engineRow);
  if (off.length) {
    const box = el('details', { class: 'off-list', open: ui.offOpen },
      el('summary', { class: 'dim small' }, t('unavailable', { n: off.length })), off.map(engineRow));
    box.addEventListener('toggle', () => { ui.offOpen = box.open; });
    rows.push(box);
  }
  $('#engines').replaceChildren(...rows);
  syncZdrBox();
}
function syncZdrBox() {
  const needs = ui.engines.some((e) => ui.selected.has(e.id) && e.zdr === false);
  $('#allow-no-zdr-row').hidden = !needs;
}
async function loadEngines() {
  const data = await api.getEngines();
  ui.engines = data.engines;
  const known = new Set(ui.engines.filter((e) => e.available).map((e) => e.id));
  ui.selected = new Set([...ui.selected].filter((id) => known.has(id)));
  if (!ui.selected.size) ui.engines.filter((e) => e.available && e.paid).forEach((e) => ui.selected.add(e.id));
  history.setEngines(ui.engines.map((e) => e.id));
  renderEngines();
}

// --- budget --------------------------------------------------------------------------------------------------------
async function loadBudget() {
  try {
    const b = await api.getBudget();
    const pill = $('#budget');
    pill.textContent = `${t('budget_today')}: ${fmtUsd(b.spent_usd)} / $${b.limit_usd.toFixed(2)}`;
    pill.classList.toggle('warn', b.spent_usd > 0.8 * b.limit_usd);
  } catch { /* the header stays as it was */ }
}

// --- runs ----------------------------------------------------------------------------------------------------------
function setRunning(on) {
  ui.running = on;
  const b = $('#run');
  b.disabled = on;
  b.textContent = t(on ? 'running' : 'run');
}
function show() { renderResults($('#results'), ui.view); }

function onEvent(name, p) {
  if (name === 'run_started') {
    ui.view.runId = p.run_id;
    $('#run-meta').textContent = `${p.run_id} · ${t('estimate')} ${fmtUsd(p.estimate_usd)}`;
  } else if (name === 'engine_done' || name === 'engine_failed') {
    ui.view.results[p.engine_id] = p.result;
    show();
  } else if (name === 'run_done') {
    $('#run-meta').textContent = `${p.run_id} · ${t('cost')} ${fmtUsd(p.cost_usd)}`;
    setRunning(false);
    loadBudget();
    updateEstimate();
  } else if (name === 'budget_stop') {
    $('#run-error').textContent = t('budget_stop', { est: fmtUsd(p.estimate_usd), rem: fmtUsd(p.remaining_usd) });
    setRunning(false);
  } else if (name === 'run_error' || name === 'fatal') {
    $('#run-error').textContent = p.message;
    setRunning(false);
  }
}

function startView(request, engineIds) {
  const byId = Object.fromEntries(ui.engines.map((e) => [e.id, e]));
  ui.view = { request, engines: engineIds.map((id) => ({ id, model: byId[id]?.model })), results: {} };
  show();
}

function run() {
  if (ui.running) return;
  let body;
  try { body = runBody(); } catch (e) { $('#run-error').textContent = e.message; return; }
  $('#run-error').textContent = '';
  setRunning(true);
  startView(body.request, body.engines);
  api.runCompare(body, onEvent);
}

async function rerunRun(id) {
  if (ui.running) return;
  const record = await api.getRun(id);
  openRun(record);
  setRunning(true);
  startView(record.request, record.engines.map((e) => e.engine_id));
  api.rerun(id, onEvent);
}

function openRun(record) {
  setState(record.request.state);
  setQuestions(record.request.questions);
  $('#title').value = record.title || '';
  $('#tags').value = (record.tags || []).join(', ');
  ui.view = {
    request: record.request,
    engines: record.engines.map((e) => ({ id: e.engine_id, model: e.model })),
    results: Object.fromEntries(record.results.map((r) => [r.engine_id, r])),
  };
  show();
  $('#run-meta').textContent = `${t('loaded_run', { id: record.id })} · ${t('cost')} ${fmtUsd(record.cost_usd)}`;
  switchTab('workbench');
  onDraftChange();
}

// --- state and questions -------------------------------------------------------------------------------------------
function setStateMode(mode) {
  ui.stateMode = mode;
  document.querySelectorAll('[data-state-mode]').forEach((b) => b.classList.toggle('on', b.dataset.stateMode === mode));
}
function setState(state) {
  if (state != null && typeof state === 'object') {
    setStateMode('json');
    $('#state').value = JSON.stringify(state, null, 2);
  } else {
    setStateMode('text');
    $('#state').value = state ?? '';
  }
}
function setQJson(on) {
  if (on === ui.qJson) return;
  if (on) {
    $('#questions-json').value = JSON.stringify(toRequest(editor.list), null, 2);
  } else {
    try { editor.set(fromRequest(JSON.parse($('#questions-json').value || '{}'))); } catch (e) {
      $('#run-error').textContent = t('json_invalid', { msg: e.message });
      return;
    }
  }
  ui.qJson = on;
  $('#questions').hidden = on;
  $('#questions-json').hidden = !on;
  $('#q-add').hidden = on;
  $('#q-json').classList.toggle('on', on);
  onDraftChange();
}
function setQuestions(questions) {
  editor.set(fromRequest(questions));
  if (ui.qJson) $('#questions-json').value = JSON.stringify(questions, null, 2);
}

async function loadPresets() {
  const { presets } = await api.getPresets();
  const sel = $('#preset');
  sel.replaceChildren(el('option', { value: '' }, t('preset')),
    ...presets.map((p) => el('option', { value: p.id, disabled: !!p.error }, p.title)));
  return presets;
}
async function applyPreset(id) {
  const p = await api.getPreset(id);
  if (p.state != null) setState(p.state);
  setQuestions(p.questions || {});
  onDraftChange();
}

// --- tabs and language ---------------------------------------------------------------------------------------------
function switchTab(tab) {
  ui.tab = tab;
  document.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  $('#tab-workbench').hidden = tab !== 'workbench';
  $('#tab-history').hidden = tab !== 'history';
  if (tab === 'history') history.load();
}
function relabel() {
  applyStatic();
  $('#lang').textContent = getLang() === 'ru' ? 'EN' : 'RU';
  setRunning(ui.running);
  editor.render();
  renderEngines();
  show();
  loadBudget();
  loadPresets();
  if (ui.tab === 'history') history.render();
  onDraftChange();
}

// --- start ---------------------------------------------------------------------------------------------------------
async function start() {
  setLang(getLang());
  $('#lang').addEventListener('click', () => { setLang(getLang() === 'ru' ? 'en' : 'ru'); relabel(); });
  document.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  document.querySelectorAll('[data-state-mode]').forEach((b) => b.addEventListener('click', () => { setStateMode(b.dataset.stateMode); onDraftChange(); }));
  $('#state').addEventListener('input', onDraftChange);
  $('#title').addEventListener('input', saveDraft);
  $('#tags').addEventListener('input', saveDraft);
  $('#allow-no-zdr').addEventListener('change', onDraftChange);
  $('#questions-json').addEventListener('input', onDraftChange);
  $('#q-add').addEventListener('click', () => editor.add());
  $('#q-json').addEventListener('click', () => setQJson(!ui.qJson));
  $('#run').addEventListener('click', run);
  $('#preset').addEventListener('change', async (e) => { if (e.target.value) await applyPreset(e.target.value); e.target.value = ''; });
  $('#state-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    if (file.name.endsWith('.json')) {
      try { setState(JSON.parse(text)); } catch { setState(text); }
    } else setState(text);
    e.target.value = '';
    onDraftChange();
  });
  $('#refresh-catalog').addEventListener('click', async () => {
    try { await api.refreshCatalog(); await loadEngines(); } catch (e) { $('#run-error').textContent = e.message; }
  });

  const draft = readDraft();
  if (draft) ui.selected = new Set(draft.selected || []);
  await Promise.all([loadEngines(), loadBudget()]);
  const presets = await loadPresets();
  if (draft) {
    setState(draft.state);
    setStateMode(draft.stateMode || 'text');
    editor.set(draft.questions || []);
    $('#title').value = draft.title || '';
    $('#tags').value = draft.tags || '';
    if (draft.qJson) {
      setQJson(true);
      if (draft.qJsonText) $('#questions-json').value = draft.qJsonText;
    }
  } else if (presets.some((p) => p.id === DEFAULT_PRESET)) {
    await applyPreset(DEFAULT_PRESET);
  }
  setRunning(false);
  show();
  onDraftChange();
  const params = new URLSearchParams(location.search);
  const linked = params.get('run');  // /?run=<id> opens a saved run
  if (linked) {
    try { openRun(await api.getRun(linked)); } catch (e) { $('#run-error').textContent = e.message; }
  }
  if (params.get('compare')) {  // /?compare=<id1>,<id2>
    history.picked = new Set(params.get('compare').split(',').filter(Boolean));
    switchTab('history');
    await history.load();
    await history.compare();
  } else if (params.get('tab') === 'history') switchTab('history');
}

start();
