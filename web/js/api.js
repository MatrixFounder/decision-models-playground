// JSON endpoints plus an SSE reader over fetch streams (POST bodies cannot ride EventSource).

import { t } from './i18n.js';

export class ApiError extends Error {}

function detailOf(body, fallback) {
  const d = body?.detail;
  if (Array.isArray(d) && d.length) {
    // FastAPI 422: [{loc, msg}] → "questions.q1: msg"
    const e = d[0];
    const loc = (e.loc || []).filter((x) => x !== 'body').join('.');
    return loc ? `${loc}: ${e.msg}` : e.msg;
  }
  return typeof d === 'string' ? d : fallback;
}

async function json(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let body = null;
    try { body = await response.json(); } catch { /* not JSON */ }
    throw new ApiError(detailOf(body, `${response.status} ${response.statusText}`));
  }
  return response.json();
}

const post = (url, body) =>
  json(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) });

export const getEngines = () => json('/api/engines');
export const getBudget = () => json('/api/budget');
export const getPresets = () => json('/api/presets');
export const getPreset = (id) => json(`/api/presets/${encodeURIComponent(id)}`);
export const estimate = (body) => post('/api/estimate', body);
export const refreshCatalog = () => post('/api/catalog/refresh');
export const listRuns = (params = {}) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v));
  return json(`/api/runs?${q}`);
};
export const getRun = (id) => json(`/api/runs/${encodeURIComponent(id)}`);
export const getLocalModels = () => json('/api/local/models');
export const getMatrix = () => json('/api/matrix');
export const unloadLocal = (id) => post(`/api/local/models/${encodeURIComponent(id)}/unload`);

export const TERMINAL = ['run_done', 'budget_stop', 'run_error', 'probe_done', 'probe_error'];

/** POST and dispatch SSE frames to onEvent(name, payload). Every stream ends with exactly one terminal event for
 * the caller: the server's, or `fatal` when the stream breaks or ends without one (review P1). */
export function streamPost(url, body, onEvent) {
  const controller = new AbortController();
  let ended = false;
  const emit = (name, payload) => {
    if (ended) return;
    if (TERMINAL.includes(name) || name === 'fatal') ended = true;
    onEvent(name, payload);
  };
  (async () => {
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
        signal: controller.signal,
      });
      if (!response.ok) {
        let b = null;
        try { b = await response.json(); } catch { /* ignore */ }
        emit('fatal', { message: detailOf(b, `${response.status} ${response.statusText}`) });
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          emit('fatal', { message: t('stream_cut') });  // a no-op when a terminal event already came
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          let name = 'message';
          const data = [];
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) name = line.slice(6).trim();
            else if (line.startsWith('data:')) data.push(line.slice(5).trim());
          }
          if (!data.length) continue;
          let payload;
          try { payload = JSON.parse(data.join('\n')); } catch { continue; /* malformed frame */ }
          emit(name, payload);
        }
      }
    } catch (error) {
      if (error.name !== 'AbortError') emit('fatal', { message: String(error) });
    }
  })();
  return controller;
}

export const runCompare = (body, onEvent) => streamPost('/api/runs', body, onEvent);
/** Rerun: the same inputs and engines; consent to skip ZDR is given again for this run, never inherited (§18). */
export const rerun = (id, options, onEvent) => streamPost(`/api/runs/${encodeURIComponent(id)}/rerun`, options, onEvent);
export const probe = (body, onEvent) => streamPost('/api/probe', body, onEvent);
export const probeEstimate = (body) => post('/api/probe/estimate', body);
export const listDatasets = () => json('/api/datasets');
export const getDataset = (name, params = {}) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== ''));
  return json(`/api/datasets/${encodeURIComponent(name)}${q.size ? `?${q}` : ''}`);
};
export const uploadDataset = (body) => post('/api/datasets', body);
export const estimateDataset = (name, body) => post(`/api/datasets/${encodeURIComponent(name)}/estimate`, body);
export const runDataset = (name, body, onEvent) => streamPost(`/api/datasets/${encodeURIComponent(name)}/run`, body, onEvent);
export const reportUrl = (id, format = 'json', lang = 'ru', target = 0.9) =>
  `/api/reports/${encodeURIComponent(id)}?${new URLSearchParams({ format, lang, target: String(target) })}`;
export const getReport = (id, target = 0.9) => json(reportUrl(id, 'json', 'ru', target));
export const estimateExperiment = (body) => post('/api/experiments/estimate', body);
export const reportKind = (id) => json(`/api/reports/${encodeURIComponent(id)}?format=kind`);
export const runExperiment = (body, onEvent) => streamPost('/api/experiments', body, onEvent);
/** A picture or a recording; the file is the body (DESIGN §15). Answers {type, path, sha256, seconds, …}. */
export const uploadMedia = (file) =>
  json('/api/media', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
export const mediaUrl = (path) => `/api/media/${encodeURIComponent(path)}`;
