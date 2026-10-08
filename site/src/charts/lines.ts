/** A plain line chart drawn as SVG at the container's real pixel width.
 *
 * It is drawn at the measured width (and redrawn on resize) instead of being a scaled viewBox, because a
 * scaled chart shrinks its labels with it and an eleven-pixel label on a phone becomes six. No library, no
 * animation, no hover state: the tables next to the chart carry the exact figures.
 */
import { escapeHtml } from "../format";

export interface LinePoint {
  t: number; // epoch milliseconds
  v: number;
}

export interface LineSeries {
  id: string;
  label: string;
  color: string;
  points: LinePoint[];
  dashed?: boolean;
}

export interface LineOptions {
  ariaLabel: string;
  height?: number;
  log?: boolean;
  baseline?: number;
  fmt?: (v: number) => string;
  dateFmt?: (t: number) => string;
}

const registry = new Map<HTMLElement, () => void>();
let listening = false;
let timer = 0;

function onResize(): void {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    for (const [el, draw] of registry) {
      if (el.isConnected) draw();
      else registry.delete(el);
    }
  }, 120);
}

export function disposeLineCharts(): void {
  registry.clear();
}

/** 1-2-5 ticks covering [lo, hi]. */
function linearTicks(lo: number, hi: number, target = 4): number[] {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

function logTicks(lo: number, hi: number): number[] {
  const out: number[] = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 2, 5]) {
      const v = m * 10 ** e;
      if (v >= lo && v <= hi) out.push(v);
    }
  }
  // a range inside one 1-2-5 step would have no rule at all: fall back to its two ends
  return out.length >= 2 ? out : [lo, hi];
}

const defaultDate = (t: number): string =>
  new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "2-digit", year: "2-digit" }).format(t);

export function lineChart(el: HTMLElement, series: LineSeries[], opts: LineOptions): void {
  const usable = series.filter((s) => s.points.length >= 2);
  const draw = (): void => {
    const width = Math.max(280, Math.floor(el.clientWidth || 600));
    const height = opts.height ?? 200;
    const padL = 4;
    const padR = 64;
    const padT = 8;
    const padB = 20;
    if (!usable.length) {
      el.innerHTML = `<div class="t-empty">Storico ancora troppo corto per un grafico: servono almeno due rilevazioni.</div>`;
      return;
    }
    const all = usable.flatMap((s) => s.points);
    const t0 = Math.min(...all.map((p) => p.t));
    const t1 = Math.max(...all.map((p) => p.t));
    let lo = Math.min(...all.map((p) => p.v));
    let hi = Math.max(...all.map((p) => p.v));
    if (opts.baseline !== undefined) {
      lo = Math.min(lo, opts.baseline);
      hi = Math.max(hi, opts.baseline);
    }
    if (!(hi > lo)) {
      lo -= Math.abs(lo) * 0.01 || 1;
      hi += Math.abs(hi) * 0.01 || 1;
    }
    const log = Boolean(opts.log) && lo > 0;
    const f = (v: number): number => (log ? Math.log(v) : v);
    const headroom = (f(hi) - f(lo)) * 0.06;
    const yLo = f(lo) - headroom;
    const ySpan = f(hi) - f(lo) + 2 * headroom;
    const sx = (t: number): number => padL + ((t - t0) / Math.max(1, t1 - t0)) * (width - padL - padR);
    const sy = (v: number): number => padT + (1 - (f(v) - yLo) / ySpan) * (height - padT - padB);
    const fmt = opts.fmt ?? ((v: number) => v.toFixed(0));
    const dateFmt = opts.dateFmt ?? defaultDate;

    const ticks = (log ? logTicks(lo, hi) : linearTicks(lo, hi)).filter((v) => v >= lo && v <= hi);
    const rules = ticks
      .map((v) => {
        const y = sy(v).toFixed(1);
        return `<line class="grid-line" x1="${padL}" y1="${y}" x2="${width - padR}" y2="${y}" />
          <text x="${width - padR + 6}" y="${y}" dominant-baseline="middle">${escapeHtml(fmt(v))}</text>`;
      })
      .join("");
    const base =
      opts.baseline === undefined
        ? ""
        : `<line class="base-line" x1="${padL}" y1="${sy(opts.baseline).toFixed(1)}" x2="${width - padR}" y2="${sy(
            opts.baseline,
          ).toFixed(1)}" />`;
    const paths = usable
      .map((s) => {
        const d = s.points.map((p, i) => `${i ? "L" : "M"}${sx(p.t).toFixed(1)} ${sy(p.v).toFixed(1)}`).join("");
        return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.6" stroke-linejoin="round"
          stroke-linecap="round" ${s.dashed ? 'stroke-dasharray="4 3"' : ""} />`;
      })
      .join("");
    const xLabels = `<text x="${padL}" y="${height - 5}">${escapeHtml(dateFmt(t0))}</text>
      <text x="${width - padR}" y="${height - 5}" text-anchor="end">${escapeHtml(dateFmt(t1))}</text>`;
    el.innerHTML = `<svg class="svg-chart t-lines" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"
      role="img" aria-label="${escapeHtml(opts.ariaLabel)}">${rules}${base}${paths}${xLabels}</svg>`;
  };
  draw();
  registry.set(el, draw);
  if (!listening) {
    window.addEventListener("resize", onResize);
    listening = true;
  }
}

/** The key under a chart: a swatch, the series name and its last value. Text, so it wraps on a phone. */
export function lineLegend(series: LineSeries[], fmt: (v: number) => string): string {
  return `<div class="legend">${series
    .map((s) => {
      const last = s.points.length ? s.points[s.points.length - 1]!.v : null;
      return `<span class="key"><span class="swatch${s.dashed ? " dash" : ""}" style="--sw:${s.color}"></span>${escapeHtml(
        s.label,
      )}${last === null ? "" : ` <b class="num">${escapeHtml(fmt(last))}</b>`}</span>`;
    })
    .join("")}</div>`;
}
