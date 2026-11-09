// Local models (ARCHITECTURE §3.6): state, size, snapshot, memory, unload; the compat server's address and token.

import * as api from './api.js';
import { t } from './i18n.js';
import { el } from './questions.js';

const when = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString() : '—');

export class LocalModelsView {
  constructor(root) {
    this.root = root;
    this.data = null;
    this.error = null;
  }

  async load() {
    try {
      this.data = await api.getLocalModels();
      this.error = null;
    } catch (e) {
      this.error = e.message;
    }
    this.render();
  }

  async unload(id) {
    try {
      await api.unloadLocal(id);
    } catch (e) {
      this.error = e.message;
    }
    await this.load();
  }

  render() {
    const out = [el('div', { class: 'filters' }, el('h2', { class: 'section' }, t('local_models')),
      el('div', { class: 'spacer' }), el('button', { class: 'ghost', onclick: () => this.load() }, '↻'))];
    if (this.error) out.push(el('p', { class: 'error' }, this.error));
    const models = this.data?.models || [];
    if (!models.length) {
      out.push(el('p', { class: 'dim empty' }, t('no_local')));
    } else {
      const head = el('tr', {}, ['id', 'repo', 'state', 'disk', 'snapshot', 'memory', 'last_call', 'calls', ''].map((k) =>
        el('th', {}, k ? t(`lm_${k}`) : '')));
      const rows = models.map((m) => el('tr', {},
        el('td', {}, el('b', {}, m.id), m.audio ? el('span', { class: 'chip' }, 'audio') : ''),
        el('td', { class: 'mono small' }, m.repo),
        el('td', {}, el('span', { class: `badge lm-${m.state || 'off'}` }, m.available ? t(`state_${m.state}`) : t('lm_unavailable')),
          m.available ? '' : el('div', { class: 'dim small' }, m.reason),
          m.last_error ? el('div', { class: 'error small' }, m.last_error) : ''),
        el('td', { class: 'mono' }, m.disk_gb == null ? '—' : `${m.disk_gb} GB`),
        el('td', { class: 'mono small' }, m.snapshot || '—'),
        el('td', { class: 'mono' }, m.memory_gb == null ? '—' : `${m.memory_gb} GB`),
        el('td', { class: 'mono small' }, when(m.last_used)),
        el('td', { class: 'mono' }, String(m.calls ?? 0)),
        el('td', {}, el('button', { class: 'ghost', disabled: m.state !== 'ready', onclick: () => this.unload(m.id) }, t('unload')))));
      out.push(el('div', { class: 'table-wrap' }, el('table', {}, el('thead', {}, head), el('tbody', {}, rows))));
    }
    const c = this.data?.compat;
    if (c) {
      const auth = c.token_set ? ' -H "Authorization: Bearer $SYSTEMONE_TOKEN"' : '';
      const curl = `curl -s ${c.url}${auth} -H 'Content-Type: application/json' \\\n  -d @tests/fixtures/requests/whip_insight_systemone.json`;
      out.push(el('h3', {}, t('compat_server')),
        el('div', { class: 'kv' },
          el('span', { class: 'dim' }, t('compat_url')), el('span', { class: 'mono' }, c.url),
          el('span', { class: 'dim' }, t('compat_access')), el('span', {}, c.lan ? t('compat_lan') : t('compat_loopback')),
          el('span', { class: 'dim' }, t('compat_token')), el('span', { class: c.token_set ? '' : 'warn' }, t(c.token_set ? 'yes' : 'no')),
          el('span', { class: 'dim' }, t('compat_default')), el('span', { class: 'mono' }, c.default_model || '—'),
          el('span', { class: 'dim' }, t('compat_ext')), el('span', { class: 'mono' }, `${c.extensions_header}: 1`)),
        el('pre', { class: 'mono small code' }, curl));
    }
    this.root.replaceChildren(...out);
  }
}
