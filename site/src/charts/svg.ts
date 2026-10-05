/** Tiny SVG charts for the forms lightweight-charts does not cover: fan chart, curve, stacked regimes, bands. */
import { escapeHtml } from "../format";

export interface Pt {
  x: number;
  y: number;
}

function scale(values: number[], size: number, pad: number, invert = false): (v: number) => number {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const usable = size - 2 * pad;
  // A constant series has no span: centre it instead of pinning every point to one edge.
  if (!(max > min)) return () => pad + usable / 2;
  // 8% headroom so a line never touches the frame.
  const headroom = (max - min) * 0.08;
  const lo = min - headroom;
  const span = max - min + 2 * headroom;
  return (v: number) => {
    const t = (v - lo) / span;
    return invert ? pad + (1 - t) * usable : pad + t * usable;
  };
}

/** Horizontal rules with their value, the terminal convention: a price ladder, never a grid.
 *  `padR` must be the chart's RIGHT padding: sizing the ladder off the vertical padding pushed every
 *  label past the viewBox edge, where it was clipped. */
function gridLines(
  sy: (v: number) => number,
  values: number[],
  w: number,
  padL: number,
  padR: number,
  fmt: (v: number) => string,
): string {
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (!(max > min)) return "";
  const ticks = [min, min + (max - min) / 2, max];
  const right = w - padR;
  return ticks
    .map((v) => {
      const y = sy(v).toFixed(1);
      return `<line class="grid-line" x1="${padL}" y1="${y}" x2="${right}" y2="${y}" />
        <text x="${right + 5}" y="${y}" dominant-baseline="middle">${fmt(v)}</text>`;
    })
    .join("");
}

/** A flat inline sparkline: no library, no axes, honest when the series never moves. */
export function sparkline(points: number[], color: string, opts: { w?: number; h?: number } = {}): string {
  const w = opts.w ?? 72;
  const h = opts.h ?? 20;
  if (points.length < 2) {
    return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="storico non disponibile">
      <line x1="1" y1="${h / 2}" x2="${w - 1}" y2="${h / 2}" stroke="var(--border-strong)" stroke-width="1" stroke-dasharray="2 3" />
    </svg>`;
  }
  const sy = scale(points, h, 2, true);
  const step = (w - 2) / (points.length - 1);
  const d = points.map((v, i) => `${i ? "L" : "M"}${(1 + i * step).toFixed(1)},${sy(v).toFixed(1)}`).join(" ");
  const flat = Math.max(...points) === Math.min(...points);
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img"
      aria-label="andamento su ${points.length} osservazioni${flat ? ", piatto" : ""}">
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"
      vector-effect="non-scaling-stroke" ${flat ? 'stroke-dasharray="3 3"' : ""} />
  </svg>`;
}

/** Forward curve: the shape of the term structure, read left to right.
 *
 * Plotted by INDEX, not by contract rank: the real ranks are 1..15 then 19, 25, 31, so a linear rank axis
 * crammed the front thirteen months into 40% of the width and put a visible kink at M15. Equal spacing shows
 * the shape the curve actually has; the labels still carry the real months.
 */
export function curveChart(points: { rank: number; price: number }[], opts: { height?: number; approx?: boolean } = {}): string {
  if (points.length < 2) return "";
  const w = 640;
  const h = opts.height ?? 220;
  const padL = 28;
  const padR = 52; // room for the price ladder on the right
  const padY = 26;
  const ys = points.map((p) => p.price);
  const sy = scale(ys, h, padY, true);
  const step = (w - padL - padR) / (points.length - 1);
  const sx = (i: number) => padL + i * step;
  const path = points.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.price).toFixed(1)}`).join(" ");
  const area = `${path} L${sx(points.length - 1).toFixed(1)},${h - padY} L${sx(0).toFixed(1)},${h - padY} Z`;
  const first = points[0];
  const last = points[points.length - 1];
  const fmt = (v: number) => v.toFixed(2).replace(".", ",");
  const everyNth = Math.max(1, Math.ceil(points.length / 7));
  const dots = points
    .map((p, i) =>
      i === 0 || i === points.length - 1 || i % everyNth === 0
        ? `<circle cx="${sx(i).toFixed(1)}" cy="${sy(p.price).toFixed(1)}" r="2.5" fill="var(--series-1)" />`
        : "",
    )
    .join("");
  const xLabels = points
    .map((p, i) =>
      i === 0 || i === points.length - 1 || i % everyNth === 0
        ? `<text x="${sx(i).toFixed(1)}" y="${h - 6}" text-anchor="${i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}">M${p.rank}</text>`
        : "",
    )
    .join("");
  const slope = last.price - first.price;
  return `<svg viewBox="0 0 ${w} ${h}" class="svg-chart" role="img"
      aria-label="Curva dei futures Brent da M${first.rank} (${fmt(first.price)} dollari) a M${last.rank} (${fmt(last.price)} dollari)">
    ${gridLines(sy, ys, w, padL, padR, fmt)}
    <path d="${area}" fill="var(--series-1)" fill-opacity="0.08" stroke="none" />
    <path d="${path}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round"
      vector-effect="non-scaling-stroke" />
    ${dots}
    ${xLabels}
    <text class="value" x="${sx(0) + 6}" y="${(sy(first.price) - 9).toFixed(1)}">${fmt(first.price)}</text>
    <text class="value" x="${(sx(points.length - 1) - 6).toFixed(1)}" y="${(sy(last.price) - 9).toFixed(1)}" text-anchor="end">${fmt(last.price)}</text>
    <text x="${padL}" y="14">M${first.rank}\u2192M${last.rank} ${slope >= 0 ? "+" : "\u2212"}${fmt(Math.abs(slope))} $ ${slope < 0 ? "(backwardation)" : "(contango)"}</text>
    ${opts.approx ? `<text class="approx" x="${w - padR}" y="14" text-anchor="end" fill="var(--series-4)">\u2248 approssimata</text>` : ""}
  </svg>`;
}

/** Fan chart: quantile bands around the median, one panel per horizon. */
export function fanChart(
  rows: { label: string; q05: number; q25: number; median: number; q75: number; q95: number }[],
  priceNow: number,
  opts: { height?: number } = {},
): string {
  if (!rows.length) return "";
  const w = 640;
  const h = opts.height ?? 230;
  const padX = 46;
  const padY = 24;
  const all = [priceNow, ...rows.flatMap((r) => [r.q05, r.q95])];
  const sy = scale(all, h, padY, true);
  const step = (w - 2 * padX) / Math.max(1, rows.length);
  const fmt = (v: number) => v.toFixed(1).replace(".", ",");
  const body = rows
    .map((r, i) => {
      const cx = padX + step * (i + 0.5);
      const bw = Math.min(46, step * 0.5);
      const outer = `<rect x="${(cx - bw / 2).toFixed(1)}" y="${sy(r.q95).toFixed(1)}" width="${bw.toFixed(1)}"
        height="${Math.max(1, sy(r.q05) - sy(r.q95)).toFixed(1)}" rx="4" fill="var(--series-1)" fill-opacity="0.16" />`;
      const inner = `<rect x="${(cx - bw / 2).toFixed(1)}" y="${sy(r.q75).toFixed(1)}" width="${bw.toFixed(1)}"
        height="${Math.max(1, sy(r.q25) - sy(r.q75)).toFixed(1)}" rx="4" fill="var(--series-1)" fill-opacity="0.34" />`;
      const med = `<line x1="${(cx - bw / 2 - 3).toFixed(1)}" x2="${(cx + bw / 2 + 3).toFixed(1)}"
        y1="${sy(r.median).toFixed(1)}" y2="${sy(r.median).toFixed(1)}" stroke="var(--series-1)" stroke-width="2.5" stroke-linecap="round" />`;
      const label = `<text x="${cx.toFixed(1)}" y="${h - 6}" fill="var(--text-muted)" font-size="11" text-anchor="middle">${escapeHtml(r.label)}</text>`;
      const value = `<text x="${cx.toFixed(1)}" y="${(sy(r.median) - 8).toFixed(1)}" fill="var(--text-primary)" font-size="11" text-anchor="middle">${fmt(r.median)}</text>`;
      return outer + inner + med + label + value;
    })
    .join("");
  const nowY = sy(priceNow).toFixed(1);
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Previsioni per orizzonte con intervalli di confidenza"
      class="svg-chart">
    <line x1="${padX - 12}" x2="${w - padX + 12}" y1="${nowY}" y2="${nowY}" stroke="var(--text-muted)" stroke-width="1" stroke-dasharray="4 4" />
    <text x="${padX - 14}" y="${nowY}" dy="4" fill="var(--text-muted)" font-size="10" text-anchor="end">${fmt(priceNow)}</text>
    ${body}
  </svg>`;
}

/** Inventories against the five-year seasonal band (the brief's "scorte contro range a 5 anni"). */
export function bandChart(
  band: { week: number; min: number; max: number; mean: number }[],
  current: { week: number; v: number }[],
  opts: { height?: number } = {},
): string {
  if (!band.length) return "";
  const w = 640;
  const h = opts.height ?? 220;
  const pad = 30;
  const weeks = band.map((b) => b.week);
  const values = [...band.flatMap((b) => [b.min, b.max]), ...current.map((c) => c.v)];
  const sx = scale(weeks, w, pad);
  const sy = scale(values, h, pad, true);
  const top = band.map((b, i) => `${i ? "L" : "M"}${sx(b.week).toFixed(1)},${sy(b.max).toFixed(1)}`).join(" ");
  const bottom = [...band]
    .reverse()
    .map((b) => `L${sx(b.week).toFixed(1)},${sy(b.min).toFixed(1)}`)
    .join(" ");
  const mean = band.map((b, i) => `${i ? "L" : "M"}${sx(b.week).toFixed(1)},${sy(b.mean).toFixed(1)}`).join(" ");
  const now = current.map((c, i) => `${i ? "L" : "M"}${sx(c.week).toFixed(1)},${sy(c.v).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Scorte di greggio contro il range stagionale a 5 anni"
      class="svg-chart">
    <path d="${top} ${bottom} Z" fill="var(--text-muted)" fill-opacity="0.16" />
    <path d="${mean}" fill="none" stroke="var(--text-muted)" stroke-width="1.5" stroke-dasharray="5 4" />
    <path d="${now}" fill="none" stroke="var(--series-2)" stroke-width="2.5" stroke-linejoin="round" />
    <text x="${pad}" y="${h - 8}" fill="var(--text-muted)" font-size="11">sett. ${Math.min(...weeks)}</text>
    <text x="${w - pad}" y="${h - 8}" fill="var(--text-muted)" font-size="11" text-anchor="end">sett. ${Math.max(...weeks)}</text>
  </svg>`;
}

/** Regime probability history as a stacked area (one band per regime label). */
export function regimeStack(
  history: { ts?: string | null; label?: string | null; confidence?: number | null }[],
  opts: { height?: number } = {},
): string {
  const rows = history.filter((h) => h.ts && h.label);
  if (rows.length < 2) return "";
  const labels = [...new Set(rows.map((r) => String(r.label)))];
  const colors = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6"];
  const w = 640;
  const h = opts.height ?? 120;
  const bw = w / rows.length;
  const bars = rows
    .map((r, i) => {
      const idx = labels.indexOf(String(r.label));
      const conf = Math.max(0.08, Math.min(1, Number(r.confidence ?? 0.5)));
      const barH = conf * (h - 18);
      return `<rect x="${(i * bw).toFixed(2)}" y="${(h - 18 - barH).toFixed(1)}" width="${Math.max(0.6, bw - 0.4).toFixed(2)}"
        height="${barH.toFixed(1)}" fill="var(${colors[idx % colors.length]})" />`;
    })
    .join("");
  const first = rows[0].ts?.slice(0, 10) ?? "";
  const last = rows[rows.length - 1].ts?.slice(0, 10) ?? "";
  const keys = labels
    .map(
      (l, i) =>
        `<span class="key"><span class="swatch" style="background:var(${colors[i % colors.length]});height:10px;width:10px;border-radius:2px"></span>${escapeHtml(l)}</span>`,
    )
    .join("");
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Storico dei regimi con la relativa confidenza"
      class="svg-chart">
    ${bars}
    <text x="0" y="${h - 4}" fill="var(--text-muted)" font-size="10">${escapeHtml(first)}</text>
    <text x="${w}" y="${h - 4}" fill="var(--text-muted)" font-size="10" text-anchor="end">${escapeHtml(last)}</text>
  </svg><div class="legend">${keys}</div>`;
}
