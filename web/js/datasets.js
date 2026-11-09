// Datasets (DESIGN §12, §16): the list, import of a JSONL/CSV file, a run over engines with its estimate, and the
// calibration report — threshold curve, reliability diagram, disagreements. The report itself holds no call text;
// this screen shows the texts of the disagreeing items from the dataset, which stays on this Mac.

import * as api from './api.js';
import { reliabilityChart, thresholdChart } from './charts.js';
import { getLang, t } from './i18n.js';
import { el } from './questions.js';
import { fmtUsd } from './results.js';

export const pct = (x) => (x == null ? '—' : `${(x * 100).toFixed(1)} %`);
const num = (x) => (x == null ? '—' : String(x));
const when = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

/** A file's format by its name: `.csv` is CSV, anything else is read as JSONL. Pure. */
export function fileFormat(name) {
  return /\.csv$/i.test(name || '') ? 'csv' : 'jsonl';
}

/** A dataset name proposed from a file name: lowercase letters, digits, `_ . -`, up to 64. Pure. */
export function datasetName(fileName) {
  const base = String(fileName || '').replace(/\.[^.]*$/, '').toLowerCase();
  const slug = base.replace(/[^a-z0-9_.-]+/g, '_').replace(/^[^a-z0-9]+/, '').slice(0, 64);
  return slug || 'dataset';
}

/** Label counts as text: "true 31 · false 28". Pure. */
export function labelSummary(labels) {
  return Object.entries(labels || {}).map(([q, counts]) => {
    const parts = Object.entries(counts).map(([v, n]) => `${v} ${n}`).join(' · ');
    return Object.keys(labels).length > 1 ? `${q}: ${parts}` : parts;
  }).join('; ');
}

/** Confusion-matrix rows for the screen: a row per label that occurred, counts per option, the diagonal's index.
 * Pure. */
export function confusionRows(metrics) {
  const opts = metrics?.options || [];
  const conf = metrics?.confusion || {};
  return opts.filter((o) => conf[o]).map((o) => ({ label: o, counts: opts.map((x) => conf[o][x] || 0), diag: opts.indexOf(o) }));
}

/** Progress as a share, never above 1 and never NaN. Pure. */
export function progressShare(done, total) {
  return total > 0 ? Math.min(1, Math.max(0, done / total)) : 0;
}

export class DatasetsView {
  /** `client` and `confirm` are the API module and window.confirm; node tests pass fakes (review P4: the money and
   * consent paths of this screen were unguarded). */
  constructor(root, { getEngines, onBudget, client = api, confirm = (text) => window.confirm(text) }) {
    this.root = root;
    this.getEngines = getEngines;
    this.onBudget = onBudget;
    this.api = client;
    this.confirm = confirm;
    this.list = [];
    this.presets = [];
    this.selected = null;
    this.detail = null;
    this.runs = [];
    this.picked = new Set();
    this.limit = '';
    this.allowNoZdr = false;
    this.est = null;  // the estimate of the current selection; null while it is computed or out of date
    this.estError = null;
    this.running = false;
    this.progress = null;
    this.report = null;
    this.target = 0.9;
    this.texts = {};  // item id → its state, for the disagreements of `this.report`
    this.error = null;
    this.message = null;
    this.importNode = null;  // built once: a re-render must not drop the picked file and the typed fields
    this.importKey = null;
    this.importError = null;
    this.opened = new Set();  // <details> left open, by key, across re-renders
    this.estSeq = 0;
    this.reportSeq = 0;
  }

  available() {
    return this.getEngines().filter((e) => e.available);
  }

  async load() {
    try {
      const [d, p] = await Promise.all([this.api.listDatasets(), this.api.getPresets()]);
      this.list = d.datasets;
      this.presets = p.presets.filter((x) => !x.error);
      this.error = null;
    } catch (e) {
      this.error = e.message;
    }
    // an engine that went away (a key, a config change) cannot stay picked without a box to untick (review P4)
    const live = new Set(this.available().map((e) => e.id));
    this.picked = new Set([...this.picked].filter((id) => live.has(id)));
    if (!this.picked.size) this.available().filter((e) => e.paid).forEach((e) => this.picked.add(e.id));
    this.render();
  }

  async select(name) {
    this.selected = name;
    this.report = null;
    this.texts = {};
    this.message = null;
    this.changed();
    try {
      const [detail, runs] = await Promise.all([this.api.getDataset(name), this.api.listRuns({ kind: 'dataset', limit: 200 })]);
      if (this.selected !== name) return;
      this.detail = detail;
      this.runs = runs.runs.filter((r) => r.dataset === name);
      this.error = null;
    } catch (e) {
      this.detail = null;
      this.error = e.message;
    }
    this.render();
  }

  /** The request for the current selection; consent to skip ZDR is passed in, never read from the box here. */
  body(allowNoZdr = false) {
    const limit = parseInt(this.limit, 10);
    return { engines: [...this.picked], limit: limit > 0 ? limit : null, options: { allow_no_zdr: allowNoZdr } };
  }

  /** The no-ZDR consent belongs to one run (DESIGN §18): read and cleared at the start, as the Workbench does. */
  takeConsent() {
    const allow = this.allowNoZdr;
    this.allowNoZdr = false;
    return allow;
  }

  /** The selection changed: the shown estimate is out of date (Run waits for the new one). */
  changed() {
    this.est = null;
    this.estimate();
  }

  async estimate() {
    const seq = ++this.estSeq;
    if (!this.selected || !this.picked.size) {
      this.render();
      return;
    }
    this.render();
    try {
      const est = await this.api.estimateDataset(this.selected, this.body());
      if (seq !== this.estSeq) return;  // a later selection's estimate is on its way
      this.est = est;
      this.estError = null;
    } catch (e) {
      if (seq !== this.estSeq) return;
      this.est = null;
      this.estError = e.message;
    }
    this.render();
  }

  /** A run starts on an estimate fetched for exactly this request, confirmed when it costs or skips ZDR. */
  async run() {
    if (this.running || !this.selected || !this.picked.size) return false;
    this.estSeq += 1;  // an estimate still in flight must not land over what the run reports
    const name = this.selected;
    const body = this.body();
    let est;
    try {
      est = await this.api.estimateDataset(name, body);
    } catch (e) {
      this.estError = e.message;
      this.render();
      return false;
    }
    if (this.running || this.selected !== name || JSON.stringify(this.body()) !== JSON.stringify(body)) return false;
    this.est = est;
    const noZdr = this.available().filter((e) => this.picked.has(e.id) && e.zdr === false).map((e) => e.id);
    const skipsZdr = this.allowNoZdr && noZdr.length > 0;
    if (est.total_usd > 0 || skipsZdr) {
      let text = t('ds_confirm', { calls: est.calls, est: fmtUsd(est.total_usd), rem: fmtUsd(est.budget.remaining_usd) });
      if (skipsZdr) text += `\n${t('ds_confirm_no_zdr', { engines: noZdr.join(', ') })}`;
      if (!this.confirm(text)) return false;
    }
    if (this.running) return false;  // a run started while the dialog was open
    const consent = this.takeConsent();
    this.running = true;
    this.progress = { done: 0, total: est.calls || 0 };
    this.message = null;
    this.report = null;
    this.render();
    this.api.runDataset(name, this.body(consent), (ev, p) => {
      if (ev === 'run_started') {
        this.progress = { done: 0, total: p.calls };
      } else if (ev === 'progress') {
        this.progress = { done: p.done, total: p.total };
      } else if (['run_done', 'budget_stop', 'run_error', 'fatal'].includes(ev)) {
        this.running = false;
        this.progress = null;
        this.onBudget();
        if (p.run_id) {
          this.message = ev === 'budget_stop' ? t('ds_budget_partial') : (p.report_error ? t('ds_report_error', { e: p.report_error }) : null);
          this.openReport(p.run_id);
          this.select(name).catch(() => {});
          return;
        }
        this.message = p.message || t('budget_stop', { est: fmtUsd(p.estimate_usd), rem: fmtUsd(p.remaining_usd) });
      }
      this.render();
    });
    return true;
  }

  async openReport(runId) {
    const seq = ++this.reportSeq;  // two quick opens: only the last one lands (review P4)
    try {
      const report = await this.api.getReport(runId, this.target);
      if (seq !== this.reportSeq) return;
      if (this.selected !== report.dataset.name) {
        this.selected = report.dataset.name;
        try {
          const detail = await this.api.getDataset(this.selected);
          const runs = await this.api.listRuns({ kind: 'dataset', limit: 200 });
          if (seq !== this.reportSeq) return;
          this.detail = detail;
          this.runs = runs.runs.filter((r) => r.dataset === this.selected);
          this.changed();  // opened by a link or from History: the run block needs its estimate too
        } catch { /* the dataset may be gone: the report still shows */ }
      }
      const texts = await this.textsOf(report);
      if (seq !== this.reportSeq) return;
      this.report = report;
      this.texts = texts;
      this.error = null;
    } catch (e) {
      if (seq !== this.reportSeq) return;
      this.error = e.message;
    }
    this.render();
  }

  async textsOf(report) {
    const ids = [...new Set((report?.groups || []).flatMap((g) => g.errors.map((x) => x.id)))].slice(0, 200);
    if (!ids.length) return {};
    try {
      const d = await this.api.getDataset(report.dataset.name, { ids: ids.join(',') });
      return Object.fromEntries((d.items || []).map((i) => [i.id, i.state]));
    } catch {
      return {};
    }
  }

  async upload(form) {
    const file = form.file.files[0];
    if (!file) return;
    this.setImportError(null);
    try {
      const content = await file.text();
      const body = { name: form.dsname.value.trim() || datasetName(file.name), format: fileFormat(file.name), content,
        label_source: form.source.value, overwrite: form.overwrite.checked };
      if (form.qset.value) body.question_set = form.qset.value;
      else body.questions = JSON.parse(form.questions.value || '{}');
      const made = await this.api.uploadDataset(body);
      form.reset();
      await this.load();
      await this.select(made.name);
    } catch (e) {
      this.setImportError(e.message);
    }
  }

  setImportError(text) {
    this.importError = text;
    if (this.importNode) this.importNode.querySelector('.error').textContent = text || '';
  }

  // --- rendering ----------------------------------------------------------------------------------------------------
  render() {
    const out = [el('div', { class: 'filters' }, el('h2', { class: 'section' }, t('datasets')), el('div', { class: 'spacer' }),
      el('button', { class: 'ghost', onclick: () => this.load() }, '↻'))];
    if (this.error) out.push(el('p', { class: 'error' }, this.error));
    out.push(el('div', { class: 'ds-grid' },
      el('div', { class: 'col' }, this.listPanel(), this.importPanel()),
      el('div', { class: 'col' }, this.detailPanel(), this.reportPanel())));
    this.root.replaceChildren(...out);
  }

  listPanel() {
    if (!this.list.length) return el('div', { class: 'panel' }, el('p', { class: 'dim empty' }, t('ds_none')));
    const rows = this.list.map((d) => el('tr', { class: `pick${d.name === this.selected ? ' on' : ''}`, onclick: () => this.select(d.name) },
      el('td', {}, el('b', {}, d.name), d.title ? el('div', { class: 'dim small' }, d.title) : '',
        d.error ? el('div', { class: 'error small' }, d.error) : ''),
      el('td', { class: 'mono' }, num(d.items)),
      el('td', { class: 'mono small' }, labelSummary(d.labels))));
    return el('div', { class: 'panel ds-list' }, el('div', { class: 'table-wrap' }, el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, t('ds_name')), el('th', {}, t('ds_items')), el('th', {}, t('ds_labels')))),
      el('tbody', {}, rows))));
  }

  /** Built once and reused: the file input and typed fields survive every re-render (progress events included);
   * rebuilt only when the preset list changes. */
  importPanel() {
    const key = this.presets.map((p) => p.id).join(',');
    if (this.importNode && this.importKey === key) return this.importNode;
    this.importKey = key;
    // the labels carry data-i18n: a language switch relabels them in place (applyStatic), the typed values stay
    const tr = (key) => el('span', { 'data-i18n': key }, t(key));
    const qset = el('select', { name: 'qset' }, el('option', { value: '', 'data-i18n': 'ds_inline_questions' }, t('ds_inline_questions')),
      this.presets.map((p) => el('option', { value: p.id }, `${p.id} — ${p.title}`)));
    const form = el('form', { class: 'ds-import', onsubmit: (e) => { e.preventDefault(); this.upload(form); } },
      el('label', {}, tr('ds_file'), el('input', { type: 'file', name: 'file', accept: '.jsonl,.json,.csv,text/csv', required: true })),
      el('label', {}, tr('ds_name'), el('input', { name: 'dsname', placeholder: t('ds_name_ph'), 'data-i18n-ph': 'ds_name_ph' })),
      el('label', {}, tr('ds_question_set'), qset),
      el('textarea', { name: 'questions', class: 'mono', rows: 4, placeholder: '{"is_tag": {"type": "noul", "instructions": "…"}}' }),
      el('label', {}, tr('ds_label_source'), el('select', { name: 'source' },
        el('option', { value: 'manual', 'data-i18n': 'ds_src_manual' }, t('ds_src_manual')),
        el('option', { value: 'test_phrases', 'data-i18n': 'ds_src_test_phrases' }, t('ds_src_test_phrases')))),
      el('label', { class: 'small' }, el('input', { type: 'checkbox', name: 'overwrite' }), ' ', tr('ds_overwrite')),
      el('div', { class: 'dim small', 'data-i18n': 'ds_format_hint' }, t('ds_format_hint')),
      el('div', { class: 'error small' }, this.importError || ''),
      el('button', { type: 'submit', 'data-i18n': 'ds_upload' }, t('ds_upload')));
    this.importNode = el('details', { class: 'panel', open: !!this.importNode?.open },
      el('summary', {}, el('b', { 'data-i18n': 'ds_import' }, t('ds_import'))), form);
    return this.importNode;
  }

  /** A <details> that stays open across re-renders. */
  details(key, summary, ...body) {
    return el('details', { class: 'off-list', open: this.opened.has(key),
      ontoggle: (e) => { if (e.target.open) this.opened.add(key); else this.opened.delete(key); } }, summary, ...body);
  }

  detailPanel() {
    const d = this.detail;
    if (!d) return el('div', { class: 'panel' }, el('p', { class: 'dim empty' }, t('ds_pick')));
    const qs = Object.entries(d.questions || {}).map(([k, q]) => el('div', { class: 'small' },
      el('span', { class: 'mono' }, k), ` (${q.type}) `, typeof q.instructions === 'string' ? q.instructions : ''));
    const engines = this.available();
    const picks = engines.map((e) => el('label', { class: 'chip-check' },
      el('input', { type: 'checkbox', checked: this.picked.has(e.id), disabled: this.running, onchange: (ev) => {
        if (ev.target.checked) this.picked.add(e.id); else this.picked.delete(e.id);
        this.changed();
      } }), ` ${e.id}`));
    const noZdr = engines.some((e) => this.picked.has(e.id) && e.zdr === false);
    const est = this.est;
    const share = this.progress ? progressShare(this.progress.done, this.progress.total) : 0;
    const bar = el('span', { class: 'fill' });
    bar.style.width = `${share * 100}%`;
    return el('div', { class: 'panel' },
      el('div', { class: 'panel-head' }, el('h2', {}, d.title || d.name), el('span', { class: 'dim small mono' }, d.name)),
      el('div', { class: 'small' }, `${d.items} ${t('ds_items_l')} · ${labelSummary(d.labels)} · ${t('ds_label_source')}: ${d.label_source}`),
      d.source ? el('div', { class: 'dim small' }, d.source) : '',
      ...qs,
      this.details(`preview:${d.name}`, el('summary', { class: 'dim small' }, t('ds_preview', { n: (d.preview || []).length })),
        el('div', { class: 'table-wrap' }, el('table', { class: 'small' }, el('tbody', {}, (d.preview || []).map((i) => el('tr', {},
          el('td', { class: 'mono' }, i.id), el('td', { class: 'mono' }, Object.values(i.labels).join(', ')), el('td', {}, i.state))))))),
      el('div', { class: 'ds-engines' }, picks),
      el('div', { class: 'run-row' },
        el('label', { class: 'small' }, t('ds_limit'), ' ', el('input', { type: 'number', min: 1, value: this.limit, class: 'ds-limit',
          onchange: (e) => { this.limit = e.target.value; this.changed(); } })),
        noZdr ? el('label', { class: 'small' }, el('input', { type: 'checkbox', checked: this.allowNoZdr,
          onchange: (e) => { this.allowNoZdr = e.target.checked; } }), ` ${t('allow_no_zdr')}`) : '',
        el('span', { class: `mono small${est && est.total_usd > est.budget.remaining_usd ? ' warn' : ''}` },
          est ? `${est.calls} ${t('ds_calls')} · ${t('estimate')} ${fmtUsd(est.total_usd)} · ${t('remaining')} ${fmtUsd(est.budget.remaining_usd)}` : ''),
        el('div', { class: 'spacer' }),
        el('button', { class: 'primary', disabled: this.running || !this.picked.size || !est, onclick: () => this.run() },
          t(this.running ? 'running' : 'run'))),
      this.estError ? el('div', { class: 'error small' }, this.estError) : '',
      this.progress ? el('div', { class: 'progress' }, el('span', { class: 'track' }, bar),
        el('span', { class: 'mono small' }, `${this.progress.done} / ${this.progress.total}`)) : '',
      this.message ? el('div', { class: 'warn small' }, this.message) : '',
      this.runs.length ? el('details', { class: 'off-list', open: true }, el('summary', { class: 'dim small' }, t('ds_runs', { n: this.runs.length })),
        el('div', {}, this.runs.slice(0, 20).map((r) => el('div', { class: 'small ds-run' },
          el('button', { class: 'link', onclick: () => this.openReport(r.id) }, when(r.created_at)),
          ` · ${r.engines.join(', ')} · ${fmtUsd(r.cost_usd)}`,
          Object.entries(r.status || {}).map(([id, s]) => el('span', { class: `chip st-${s}` }, id)))))) : '');
  }

  reportPanel() {
    const r = this.report;
    if (!r) return '';
    const lang = getLang() === 'en' ? 'en' : 'ru';
    const head = el('div', { class: 'panel-head' }, el('h2', {}, t('ds_report')),
      el('span', { class: 'dim small mono' }, `${r.run_id} · ${when(r.created_at)} · ${fmtUsd(r.cost_usd)}`),
      el('div', { class: 'spacer' }),
      el('label', { class: 'small' }, t('ds_target'), ' ', el('input', { type: 'number', min: 0.5, max: 0.99, step: 0.01,
        value: this.target, class: 'ds-limit', onchange: (e) => {
          const v = parseFloat(e.target.value);
          if (v >= 0.5 && v <= 0.99) { this.target = v; this.openReport(r.run_id); }
        } })),
      el('a', { class: 'button ghost', href: this.api.reportUrl(r.run_id, 'md', lang, this.target) }, 'Markdown'),
      el('a', { class: 'button ghost', href: this.api.reportUrl(r.run_id, 'json', lang, this.target), download: `${r.run_id}.json` }, 'JSON'));
    const parts = [head, el('p', { class: 'dim small' }, t('ds_report_note'))];
    if (r.budget_stopped) parts.push(el('p', { class: 'warn small' }, t('ds_budget_partial')));
    for (const [key, q] of Object.entries(r.questions)) {
      const groups = r.groups.filter((g) => g.question === key);
      if (!groups.length) continue;
      parts.push(el('h3', {}, el('span', { class: 'mono' }, key), ` (${q.type}) `, typeof q.instructions === 'string' ? q.instructions : ''));
      parts.push(this.groupTable(q.type, groups));
      parts.push(el('div', { class: 'chart-grid' }, groups.map((g) => el('div', { class: 'chart-card' },
        el('div', { class: 'small' }, el('b', {}, g.engine_id), ' ', el('span', { class: 'dim mono' }, g.snapshot || '')),
        el('div', { class: 'charts' }, thresholdChart(g.metrics, t('ds_curve')), reliabilityChart(g.metrics.reliability, t('ds_reliability'))),
        q.type === 'noul' ? '' : this.confusion(g),
        this.errorList(g)))));
    }
    parts.push(el('div', { class: 'legend small' },
      el('span', { class: 'lg lg-agree' }, t('ds_agreement')), el('span', { class: 'lg lg-cover' }, t('ds_coverage')),
      el('span', { class: 'lg lg-acc' }, t('ds_accuracy')), el('span', { class: 'dim' }, t('ds_legend_marks'))));
    return el('div', { class: 'panel' }, parts);
  }

  groupTable(type, groups) {
    const noul = type === 'noul';
    const cols = noul
      ? ['ds_engine', 'snapshot', 'ds_answered', 'Brier', 'ECE', 'ds_whip_t', 'ds_agreement', 'ds_precision', 'ds_recall', 'ds_sel_t', 'ds_coverage']
      : ['ds_engine', 'snapshot', 'ds_answered', 'ds_top1', 'Brier', 'ECE', 'ds_sel_t', 'ds_coverage'];
    const head = el('tr', {}, cols.map((c) => el('th', {}, /^[A-Z]/.test(c) ? c : t(c))));
    const rows = groups.map((g) => {
      const m = g.metrics;
      const w = m.whip || {};
      const s = m.selective || {};
      const base = [el('td', {}, el('b', {}, g.engine_id)), el('td', { class: 'mono small' }, g.snapshot || '—'),
        el('td', { class: 'mono' }, `${m.answered}/${m.items}${g.not_asked ? ` (+${g.not_asked} ${t('ds_not_asked')})` : ''}`)];
      const tail = noul
        ? [num(m.brier), num(m.ece), num(w.t), el('span', { class: w.meets_target ? 'ok' : 'warn' }, pct(w.agreement)),
          pct(w.precision), pct(w.recall), num(s.t), pct(s.coverage)]
        : [pct(m.accuracy), num(m.brier), num(m.ece), num(s.t), pct(s.coverage)];
      return el('tr', {}, base, tail.map((v) => el('td', { class: 'mono' }, v)));
    });
    return el('div', { class: 'table-wrap' }, el('table', { class: 'small' }, el('thead', {}, head), el('tbody', {}, rows)));
  }

  /** Choice and score: label rows, answer columns (DESIGN §16). */
  confusion(g) {
    const rows = confusionRows(g.metrics);
    if (!rows.length) return '';
    return el('div', { class: 'table-wrap' }, el('table', { class: 'small confusion', title: t('ds_confusion') },
      el('thead', {}, el('tr', {}, el('th', {}, '↓ / →'), g.metrics.options.map((o) => el('th', { class: 'mono' }, o)))),
      el('tbody', {}, rows.map((r) => el('tr', {}, el('th', { class: 'mono' }, r.label),
        r.counts.map((n, i) => el('td', { class: `mono${i === r.diag ? ' ok' : n ? ' warn' : ' dim'}` }, String(n))))))));
  }

  errorList(g) {
    if (!g.errors.length) return el('div', { class: 'dim small' }, t('ds_no_errors'));
    const more = g.errors_total > g.errors.length ? el('div', { class: 'dim small' }, `+${g.errors_total - g.errors.length}`) : '';
    return this.details(`errors:${this.report?.run_id}:${g.engine_id}:${g.question}:${g.snapshot}`,
      el('summary', { class: 'small' }, t('ds_errors', { n: g.errors_total })),
      el('table', { class: 'small' }, el('tbody', {}, g.errors.map((x) => el('tr', {},
        el('td', { class: 'mono' }, x.id), el('td', { class: 'mono' }, `${x.label}${x.top ? ` → ${x.top}` : ''}`),
        el('td', { class: 'mono' }, x.p.toFixed(3)), el('td', {}, this.texts[x.id] || ''))))), more);
  }
}
