/** Tiny SVG charts for the forms lightweight-charts does not cover: fan chart, curve, stacked regimes, bands. */
import { escapeHtml } from "../format";

export interface Pt {
  x: number;
  y: number;
}

function scale(values: number[], size: number, pad: number, invert = false): (v: number) => number {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return (v: number) => {
    const t = (v - min) / span;
    return invert ? pad + (1 - t) * (size - 2 * pad) : pad + t * (size - 2 * pad);
  };
}

/** Forward curve: M1..Mn with a 2px line and labelled ends (the brief's "curva futures"). */
export function curveChart(points: { rank: number; price: number }[], opts: { height?: number; approx?: boolean } = {}): string {
  if (points.length < 2) return "";
  const w = 640;
  const h = opts.height ?? 200;
  const pad = 28;
  const xs = points.map((p) => p.rank);
  const ys = points.map((p) => p.price);
  const sx = scale(xs, w, pad);
  const sy = scale(ys, h, pad, true);
  const path = points.map((p, i) => `${i ? "L" : "M"}${sx(p.rank).toFixed(1)},${sy(p.price).toFixed(1)}`).join(" ");
  const first = points[0];
  const last = points[points.length - 1];
  const dots = points
    .map((p) => `<circle cx="${sx(p.rank).toFixed(1)}" cy="${sy(p.price).toFixed(1)}" r="3" fill="var(--series-1)" />`)
    .join("");
  const fmt = (v: number) => v.toFixed(2).replace(".", ",");
  return `<svg viewBox="0 0 ${w} ${h}" class="svg-chart" role="img"
      aria-label="Curva dei futures Brent da M${first.rank} a M${last.rank}" preserveAspectRatio="none" style="width:100%;height:${h}px">
    <path d="${path}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round" />
    ${dots}
    <text x="${pad}" y="${h - 8}" fill="var(--text-muted)" font-size="11">M${first.rank}</text>
    <text x="${w - pad}" y="${h - 8}" fill="var(--text-muted)" font-size="11" text-anchor="end">M${last.rank}</text>
    <text x="${sx(first.rank) + 6}" y="${sy(first.price) - 8}" fill="var(--text-secondary)" font-size="11">${fmt(first.price)} $</text>
    <text x="${sx(last.rank) - 6}" y="${sy(last.price) - 8}" fill="var(--text-secondary)" font-size="11" text-anchor="end">${fmt(last.price)} $</text>
    ${opts.approx ? `<text x="${w / 2}" y="16" fill="var(--series-4)" font-size="11" text-anchor="middle">≈ curva approssimata</text>` : ""}
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
      preserveAspectRatio="none" style="width:100%;height:${h}px">
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
      preserveAspectRatio="none" style="width:100%;height:${h}px">
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
      preserveAspectRatio="none" style="width:100%;height:${h}px">
    ${bars}
    <text x="0" y="${h - 4}" fill="var(--text-muted)" font-size="10">${escapeHtml(first)}</text>
    <text x="${w}" y="${h - 4}" fill="var(--text-muted)" font-size="10" text-anchor="end">${escapeHtml(last)}</text>
  </svg><div class="legend">${keys}</div>`;
}
