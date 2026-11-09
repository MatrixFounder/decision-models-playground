// Small SVG charts of the calibration report (TASK §6.5: own SVG, no CDN): the threshold curve and the reliability
// diagram. The geometry is pure and tested with node --test; only `svg()` touches the DOM.

const NS = 'http://www.w3.org/2000/svg';

/** A linear map from a domain to a range. */
export function scale([d0, d1], [r0, r1]) {
  return (v) => r0 + ((v - d0) * (r1 - r0)) / (d1 - d0);
}

/** An SVG path through [x, y] points; a null y breaks the line (no accuracy where nothing was decided). */
export function pathOf(points, sx, sy) {
  let d = '';
  let pen = false;
  for (const [x, y] of points) {
    if (y == null || Number.isNaN(y)) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`;
    pen = true;
  }
  return d;
}

export const SERIES = [
  { key: 'agreement', cls: 'ln-agree' },  // whip-insight's verdict p ≥ t (noul only)
  { key: 'coverage', cls: 'ln-cover' },  // selective: decided / all
  { key: 'accuracy', cls: 'ln-acc' },  // selective: correct among decided
];

const W = 300;
const H = 170;
const PAD = { l: 30, r: 8, t: 8, b: 22 };

/** The threshold chart's paths and marks for one group's metrics. Pure. */
export function thresholdGeometry(metrics, width = W, height = H) {
  const sx = scale([0.5, 1.0], [PAD.l, width - PAD.r]);
  const sy = scale([0, 1], [height - PAD.b, PAD.t]);
  const curve = metrics?.curve || [];
  const lines = SERIES.filter((s) => curve.some((c) => c[s.key] !== undefined))
    .map((s) => ({ ...s, d: pathOf(curve.map((c) => [c.t, c[s.key]]), sx, sy) }));
  const marks = [];
  if (metrics?.whip?.t != null) marks.push({ cls: 'mk-agree', x: sx(metrics.whip.t), t: metrics.whip.t });
  if (metrics?.selective?.t != null) marks.push({ cls: 'mk-acc', x: sx(metrics.selective.t), t: metrics.selective.t });
  return { lines, marks, sx, sy, width, height };
}

/** The reliability diagram's points: bin mean probability against observed frequency, sized by count. Pure. */
export function reliabilityGeometry(bins, width = 170, height = H) {
  const sx = scale([0, 1], [PAD.l, width - PAD.r]);
  const sy = scale([0, 1], [height - PAD.b, PAD.t]);
  const most = Math.max(1, ...(bins || []).map((b) => b.n));
  const points = (bins || []).map((b) => ({ cx: sx(b.mean_p), cy: sy(b.freq), r: 2 + 5 * Math.sqrt(b.n / most), n: b.n }));
  return { points, diagonal: `M${sx(0)},${sy(0)}L${sx(1)},${sy(1)}`, sx, sy, width, height };
}

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const c of children.flat()) if (c) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

function axes(g, ticksX) {
  const out = [];
  const y0 = g.sy(0);
  const y1 = g.sy(1);
  out.push(svg('line', { x1: g.sx(ticksX[0]), y1: y0, x2: g.sx(ticksX.at(-1)), y2: y0, class: 'axis' }));
  out.push(svg('line', { x1: g.sx(ticksX[0]), y1: y0, x2: g.sx(ticksX[0]), y2: y1, class: 'axis' }));
  for (const v of [0, 0.5, 1]) {
    out.push(svg('text', { x: g.sx(ticksX[0]) - 4, y: g.sy(v) + 3, class: 'tick', 'text-anchor': 'end' }, v.toString()));
  }
  for (const v of ticksX) {
    out.push(svg('text', { x: g.sx(v), y: y0 + 14, class: 'tick', 'text-anchor': 'middle' }, v.toString()));
  }
  return out;
}

export function thresholdChart(metrics, title) {
  const g = thresholdGeometry(metrics);
  return svg('svg', { viewBox: `0 0 ${g.width} ${g.height}`, class: 'chart', role: 'img', 'aria-label': title || '' },
    svg('title', {}, title || ''),
    axes(g, [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]),
    g.marks.map((m) => svg('line', { x1: m.x, y1: g.sy(0), x2: m.x, y2: g.sy(1), class: `mark ${m.cls}` },
      svg('title', {}, `t = ${m.t}`))),
    g.lines.map((l) => svg('path', { d: l.d, class: `line ${l.cls}` })));
}

export function reliabilityChart(bins, title) {
  const g = reliabilityGeometry(bins);
  return svg('svg', { viewBox: `0 0 ${g.width} ${g.height}`, class: 'chart', role: 'img', 'aria-label': title || '' },
    svg('title', {}, title || ''),
    axes(g, [0, 0.5, 1]),
    svg('path', { d: g.diagonal, class: 'diag' }),
    g.points.map((p) => svg('circle', { cx: p.cx, cy: p.cy, r: p.r, class: 'dot' }, svg('title', {}, `n = ${p.n}`))));
}
