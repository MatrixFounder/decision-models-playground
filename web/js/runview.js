// One stream's event handler, bound to the view it fills (review P1/round 2: pure, so node --test can check it).
// Cells and the meta line belong to the view; the running state, budget and errors are global (`finish`).

export const TERMINAL = ['run_done', 'budget_stop', 'run_error', 'fatal'];

/**
 * @param view {{results: object, runId?: string}}
 * @param deps {{isCurrent: (view) => boolean, render: (view) => void, setMeta: (kind, payload) => void,
 *               finish: (name, payload, view) => void}}
 */
export function makeHandler(view, { isCurrent, render, setMeta, finish }) {
  return (name, p) => {
    if (name === 'run_started') {
      view.runId = p.run_id;
      if (isCurrent(view)) setMeta('started', p);
    } else if (name === 'engine_done' || name === 'engine_failed') {
      view.results[p.engine_id] = p.result;
      if (isCurrent(view)) render(view);
    } else if (TERMINAL.includes(name)) {
      if (name === 'run_done' && isCurrent(view)) setMeta('done', p);
      finish(name, p, view);
    }
  };
}
