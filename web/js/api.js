// JSON endpoints plus an SSE reader over fetch streams (POST bodies cannot ride EventSource).

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

/** POST and dispatch SSE frames to onEvent(name, payload). Returns an AbortController. */
export function streamPost(url, body, onEvent) {
  const controller = new AbortController();
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
        onEvent('fatal', { message: detailOf(b, `${response.status} ${response.statusText}`) });
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
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
          try { onEvent(name, JSON.parse(data.join('\n'))); } catch { /* malformed frame */ }
        }
      }
    } catch (error) {
      if (error.name !== 'AbortError') onEvent('fatal', { message: String(error) });
    }
  })();
  return controller;
}

export const runCompare = (body, onEvent) => streamPost('/api/runs', body, onEvent);
export const rerun = (id, onEvent) => streamPost(`/api/runs/${encodeURIComponent(id)}/rerun`, {}, onEvent);
