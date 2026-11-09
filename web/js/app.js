// Wiring: tabs, the draft, engines, estimate, run over SSE, budget, language (DESIGN §16).

import * as api from './api.js';
import { DatasetsView } from './datasets.js';
import { History, isWorkbenchRun, notWorkbenchMessage } from './history.js';
import { applyStatic, getLang, setLang, t } from './i18n.js';
import { LocalModelsView } from './local.js';
import { MatrixView } from './matrix.js';
import { backupOnOpen } from './draft.js';
import { makeHandler } from './runview.js';
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

const editor = new QuestionEditor($('#questions'), () => userEdited());
const history = new History($('#history'), { onOpen: openRun, onRerun: rerunRun, onOpenReport: (id) => openReport(id) });
const localView = new LocalModelsView($('#local'));
const matrixView = new MatrixView($('#matrix'), { onBudget: () => loadBudget() });
const datasetsView = new DatasetsView($('#datasets'), { getEngines: () => ui.engines, onBudget: () => loadBudget() });

// --- storage (a per-viewer convenience: the page works without it) -------------------------------------------------
function readDraft() {
  try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch { return null; }
}
function saveDraft() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({
      state: $('#state').value, stateMode: ui.stateMode, questions: editor.list, qJson: ui.qJson,
      qJsonText: $('#questions-json').value, selected: [...ui.selected], title: $('#title').value, tags: $('#tags').value,
      fromRun: ui.draftFromRun || null,
    }));
  } catch { /* storage blocked */ }
}

function applyDraft(draft) {
  setState(draft.state);
  setStateMode(draft.stateMode || 'text');
  if (ui.qJson) setQJson(false);
  editor.set(draft.questions || []);
  $('#title').value = draft.title || '';
  $('#tags').value = draft.tags || '';
  if (draft.qJson) {
    setQJson(true);
    if (draft.qJsonText) $('#questions-json').value = draft.qJsonText;
  }
  ui.draftFromRun = draft.fromRun || null;
  onDraftChange();
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
    badges.push(el('span', { class: 'chip' }, e.local ? t('local') : t('free')));
    if (e.local && e.state) badges.push(el('span', { class: `chip lm-${e.state}` }, t(`state_${e.state}`)));
  }
  const box = el('input', { type: 'checkbox', checked: ui.selected.has(e.id), disabled: !e.available,
    onchange: (ev) => { if (ev.target.checked) ui.selected.add(e.id); else ui.selected.delete(e.id); syncZdrBox(); userEdited(); } });
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
const byIdLocal = (id) => ui.engines.find((e) => e.id === id)?.local;

/** The event handler of one stream, bound to the view it fills: opening another run while this one streams must
 * not mix the two tables (review P1). Running state and budget are global; cells and meta belong to the view. */
function handlerFor(view) {
  return makeHandler(view, {
    getCurrent: () => ui.view,
    render: () => show(),
    setMeta: (kind, p) => {
      $('#run-meta').textContent = kind === 'started'
        ? `${p.run_id} · ${t('estimate')} ${fmtUsd(p.estimate_usd)}`
        : `${p.run_id} · ${t('cost')} ${fmtUsd(p.cost_usd)}`;
    },
    finish: (name, p, v) => {
      setRunning(false);
      loadBudget();
      if (name === 'run_done') {
        updateEstimate();
        if (v.engines.some((e) => byIdLocal(e.id))) loadEngines();  // a local model's state changed
      } else if (name === 'budget_stop') {
        $('#run-error').textContent = t('budget_stop', { est: fmtUsd(p.estimate_usd), rem: fmtUsd(p.remaining_usd) });
      } else {
        $('#run-error').textContent = p.message;
      }
    },
  });
}

function startView(request, engineIds) {
  const byId = Object.fromEntries(ui.engines.map((e) => [e.id, e]));
  ui.view = { request, engines: engineIds.map((id) => ({ id, model: byId[id]?.model })), results: {} };
  show();
  return ui.view;
}

/** Consent to skip ZDR is per run (§18): read it for this run, then clear the box. */
function takeNoZdrConsent() {
  const box = $('#allow-no-zdr');
  const given = box.checked;
  box.checked = false;
  return given;
}

function run() {
  if (ui.running) return;
  let body;
  try { body = runBody(); } catch (e) { $('#run-error').textContent = e.message; return; }
  body.options.allow_no_zdr = takeNoZdrConsent();
  $('#run-error').textContent = '';
  setRunning(true);
  api.runCompare(body, handlerFor(startView(body.request, body.engines)));
}

/** Rerun from History: the same inputs and engines, after the user sees the estimate. Consent to skip ZDR is asked
 * here, for this rerun only — never read from the Workbench box, which belongs to another selection (review round 2). */
async function rerunRun(id) {
  if (ui.running) return;
  let record;
  let est;
  try {
    record = await api.getRun(id);
    if (!isWorkbenchRun(record)) throw new Error(notWorkbenchMessage(record.kind));
    est = await api.estimate({ request: record.request, engines: record.engines.map((e) => e.engine_id) });
  } catch (e) {
    $('#run-error').textContent = e.message;
    return;
  }
  if (!window.confirm(t('rerun_confirm', { est: fmtUsd(est.total_usd), rem: fmtUsd(est.budget.remaining_usd) }))) return;
  const noZdr = record.engines.filter((e) => e.zdr === false).map((e) => e.engine_id);
  const allow = noZdr.length ? window.confirm(t('rerun_no_zdr', { engines: noZdr.join(', ') })) : false;
  if (ui.running) return;  // a Workbench run started while the dialogs were open (review round 3)
  if (!openRun(record)) return;
  $('#run-error').textContent = '';
  setRunning(true);
  const view = startView(record.request, record.engines.map((e) => e.engine_id));
  api.rerun(id, { allow_no_zdr: allow }, handlerFor(view));
}

/** Show a saved run in the Workbench. The user's draft is kept aside first and can be restored (review P1). */
/** A dataset run's report on the Datasets screen. */
function openReport(id) {
  switchTab('datasets', { load: false });
  datasetsView.load().then(() => datasetsView.openReport(id));
}

function openRun(record) {
  if (record.kind === 'dataset') {  // /?run=<id> of a dataset run: its report
    openReport(record.id);
    return false;
  }
  if (!isWorkbenchRun(record)) {  // a probe is not a Workbench run (its results are on the matrix)
    $('#run-error').textContent = notWorkbenchMessage(record.kind);
    return false;
  }
  stashDraft();
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
  ui.draftFromRun = record.id;
  onDraftChange();
  return true;
}

/** The user changed the draft: from now on it is theirs, not an opened run's. */
function userEdited() {
  ui.draftFromRun = null;
  onDraftChange();
}

const BACKUP_KEY = 'draft.backup.v1';
function stashDraft() {
  try {
    const backup = backupOnOpen(localStorage.getItem(DRAFT_KEY), localStorage.getItem(BACKUP_KEY));
    if (backup) localStorage.setItem(BACKUP_KEY, backup);
  } catch { /* storage blocked: nothing to restore later */ }
  syncRestore();
}
function syncRestore() {
  let has = false;
  try { has = !!localStorage.getItem(BACKUP_KEY); } catch { /* storage blocked */ }
  $('#restore-draft').hidden = !has;
}
function restoreDraft() {
  let backup = null;
  try { backup = JSON.parse(localStorage.getItem(BACKUP_KEY) || 'null'); localStorage.removeItem(BACKUP_KEY); } catch { /* ignore */ }
  if (backup) applyDraft(backup);
  syncRestore();
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
    try { $('#questions-json').value = JSON.stringify(toRequest(editor.list), null, 2); } catch (e) {
      $('#run-error').textContent = e.message;
      return;
    }
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
  const option = (p) => el('option', { value: p.id, disabled: !!p.error }, p.title);
  const groups = [...new Set(presets.map((p) => p.group).filter(Boolean))];  // std_tags/…: the imported tags
  sel.replaceChildren(el('option', { value: '' }, t('preset')), ...presets.filter((p) => !p.group).map(option),
    ...groups.map((g) => el('optgroup', { label: g }, presets.filter((p) => p.group === g).map(option))));
  return presets;
}
async function applyPreset(id) {
  const p = await api.getPreset(id);
  if (p.state != null) setState(p.state);
  setQuestions(p.questions || {});
  userEdited();
}

// --- tabs and language ---------------------------------------------------------------------------------------------
function switchTab(tab, { load = true } = {}) {
  ui.tab = tab;
  document.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  $('#tab-workbench').hidden = tab !== 'workbench';
  $('#tab-history').hidden = tab !== 'history';
  $('#tab-local').hidden = tab !== 'local';
  $('#tab-matrix').hidden = tab !== 'matrix';
  $('#tab-datasets').hidden = tab !== 'datasets';
  if (tab === 'datasets' && load) loadEngines().catch(() => {}).then(() => datasetsView.load());
  if (tab === 'history') history.load();
  if (tab === 'local') localView.load();
  if (tab === 'matrix') matrixView.load();
  if (tab === 'workbench') loadEngines().catch(() => {});
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
  if (ui.tab === 'local') localView.render();
  if (ui.tab === 'matrix') matrixView.render();
  if (ui.tab === 'datasets') datasetsView.render();
  onDraftChange();
}

// --- start ---------------------------------------------------------------------------------------------------------
async function start() {
  setLang(getLang());
  $('#lang').addEventListener('click', () => { setLang(getLang() === 'ru' ? 'en' : 'ru'); relabel(); });
  document.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  document.querySelectorAll('[data-state-mode]').forEach((b) => b.addEventListener('click', () => { setStateMode(b.dataset.stateMode); userEdited(); }));
  $('#state').addEventListener('input', userEdited);
  $('#title').addEventListener('input', userEdited);
  $('#tags').addEventListener('input', userEdited);
  $('#allow-no-zdr').addEventListener('change', onDraftChange);
  $('#questions-json').addEventListener('input', userEdited);
  $('#q-add').addEventListener('click', () => editor.add());
  $('#q-json').addEventListener('click', () => setQJson(!ui.qJson));
  $('#run').addEventListener('click', run);
  $('#restore-draft').addEventListener('click', restoreDraft);
  syncRestore();
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
    applyDraft(draft);
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
  } else if (params.get('report')) {  // /?report=<run id> opens a dataset run's report
    switchTab('datasets', { load: false });
    await datasetsView.load();
    await datasetsView.openReport(params.get('report'));
  } else if (['history', 'local', 'matrix', 'datasets'].includes(params.get('tab'))) switchTab(params.get('tab'));
}

start();
