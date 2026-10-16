/** A plain line chart drawn as SVG at the container's real pixel width, with a crosshair that reads every series.
 *
 * It is drawn at the measured width (and redrawn on resize) instead of being a scaled viewBox, because a scaled
 * chart shrinks its labels with it and an eleven-pixel label on a phone becomes six. No library. The hover layer
 * is part of the chart: a vertical hairline snaps to the nearest date, a dot marks each series there and a small
 * readout lists every series' value - the same on keyboard focus (left and right arrows move it). The tooltip
 * enhances, it never gates: the tables beside every chart carry the exact figures.
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
  /** A reference level drawn as a dashed rule (the starting capital, zero P&L): a threshold, not a grid line. */
  baseline?: number;
  /** A wash under the line, for a chart of one series. */
  area?: boolean;
  fmt?: (v: number) => string;
  dateFmt?: (t: number) => string;
  /** The longer date of the readout (defaults to `dateFmt`). */
  readoutDate?: (t: number) => string;
  /** A second figure in the readout, computed from the value (e.g. the P&L against the starting capital). */
  readoutExtra?: (v: number) => string;
  /** The figure format of the readout and of the end labels (defaults to `fmt`, the axis's). */
  readoutFmt?: (v: number) => string;
  /** Label each line at its end with its name and last value: for two to four series, beside the legend. */
  endLabels?: boolean;
  /** What to say when there is not enough history to draw. */
  emptyText?: string;
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
  new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "2-digit" }).format(t);

/** The index of the point of `points` (sorted by t) nearest to `t`. */
function nearest(points: LinePoint[], t: number): number {
  let lo = 0;
  let hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid]!.t <= t) lo = mid;
    else hi = mid;
  }
  return Math.abs(points[lo]!.t - t) <= Math.abs(points[hi]!.t - t) ? lo : hi;
}

export function lineChart(el: HTMLElement, series: LineSeries[], opts: LineOptions): void {
  const usable = series
    .filter((s) => s.points.length >= 2)
    .map((s) => ({ ...s, points: [...s.points].sort((a, b) => a.t - b.t) }));
  const draw = (): void => {
    const width = Math.max(280, Math.floor(el.clientWidth || 600));
    const height = opts.height ?? 200;
    const padL = 4;
    const padR = 64;
    const padT = 10;
    const padB = 22;
    if (!usable.length) {
      el.innerHTML = `<div class="t-empty">${escapeHtml(
        opts.emptyText ?? "Not enough history for a chart yet: it needs at least two readings.",
      )}</div>`;
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
    const headroom = (f(hi) - f(lo)) * 0.08;
    const yLo = f(lo) - headroom;
    const ySpan = f(hi) - f(lo) + 2 * headroom;
    const plotR = width - padR;
    const plotB = height - padB;
    const sx = (t: number): number => padL + ((t - t0) / Math.max(1, t1 - t0)) * (plotR - padL);
    const sy = (v: number): number => padT + (1 - (f(v) - yLo) / ySpan) * (plotB - padT);
    const fmt = opts.fmt ?? ((v: number) => v.toFixed(0));
    const readoutFmt = opts.readoutFmt ?? fmt;
    const dateFmt = opts.dateFmt ?? defaultDate;

    const ticks = (log ? logTicks(lo, hi) : linearTicks(lo, hi)).filter((v) => v >= lo && v <= hi);
    const rules = ticks
      .map((v) => {
        const y = sy(v).toFixed(1);
        return `<line class="grid-line" x1="${padL}" y1="${y}" x2="${plotR}" y2="${y}" />
          <text x="${plotR + 6}" y="${y}" dominant-baseline="middle">${escapeHtml(fmt(v))}</text>`;
      })
      .join("");
    const base =
      opts.baseline === undefined
        ? ""
        : `<line class="base-line" x1="${padL}" y1="${sy(opts.baseline).toFixed(1)}" x2="${plotR}" y2="${sy(
            opts.baseline,
          ).toFixed(1)}" />`;
    const pathOf = (s: (typeof usable)[number]): string =>
      s.points.map((p, i) => `${i ? "L" : "M"}${sx(p.t).toFixed(1)} ${sy(p.v).toFixed(1)}`).join("");
    const wash =
      opts.area && usable.length === 1
        ? (() => {
            const s = usable[0]!;
            const floor = opts.baseline !== undefined ? sy(opts.baseline) : plotB;
            const first = s.points[0]!;
            const last = s.points[s.points.length - 1]!;
            return `<path d="${pathOf(s)}L${sx(last.t).toFixed(1)} ${floor.toFixed(1)}L${sx(first.t).toFixed(1)} ${floor.toFixed(
              1,
            )}Z" style="fill:${s.color}" fill-opacity="0.1" stroke="none" />`;
          })()
        : "";
    const paths = usable
      .map(
        (s) => `<path d="${pathOf(s)}" fill="none" style="stroke:${s.color}" stroke-width="2" stroke-linejoin="round"
          stroke-linecap="round" ${s.dashed ? 'stroke-dasharray="4 3"' : ""} />`,
      )
      .join("");
    const xLabels = `<text x="${padL}" y="${height - 6}">${escapeHtml(dateFmt(t0))}</text>
      <text x="${plotR}" y="${height - 6}" text-anchor="end">${escapeHtml(dateFmt(t1))}</text>`;
    // end labels: name and last value just left of each line's end, pushed apart so that two never overlap
    const ends = opts.endLabels
      ? usable
          .map((s) => {
            const p = s.points[s.points.length - 1]!;
            return { s, x: sx(p.t), y: sy(p.v), ly: 0, text: `${s.label} ${readoutFmt(p.v)}` };
          })
          .sort((a, b) => a.y - b.y)
      : [];
    let prev = -Infinity;
    for (const e of ends) {
      e.ly = Math.max(e.y - 7, prev + 14);
      prev = e.ly;
    }
    const overflow = ends.length ? ends[ends.length - 1]!.ly - (plotB - 3) : 0;
    if (overflow > 0) for (const e of ends) e.ly -= overflow;
    for (const e of ends) e.ly = Math.max(padT + 9, e.ly);
    const endMarks = ends
      .map(
        (e) => `<circle class="lc-end" cx="${e.x.toFixed(1)}" cy="${e.y.toFixed(1)}" r="3" style="fill:${e.s.color}" />
        <text class="lc-label" x="${(e.x - 7).toFixed(1)}" y="${e.ly.toFixed(1)}" text-anchor="end" dominant-baseline="middle">${escapeHtml(
          e.text,
        )}</text>`,
      )
      .join("");
    el.innerHTML = `<div class="lc">
      <svg class="svg-chart t-lines" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"
        role="img" aria-label="${escapeHtml(opts.ariaLabel)}" tabindex="0">${rules}${base}${wash}${paths}${xLabels}${endMarks}
        <g class="lc-hover" visibility="hidden"><line class="lc-cross" x1="0" y1="${padT}" x2="0" y2="${plotB}" />${usable
          .map((s) => `<circle class="lc-dot" r="4" style="fill:${s.color}" />`)
          .join("")}</g>
        <rect class="lc-hit" x="${padL}" y="0" width="${plotR - padL}" height="${height}" fill="transparent" />
      </svg>
      <div class="lc-tip" role="status" aria-live="polite" hidden></div>
    </div>`;

    const svg = el.querySelector<SVGSVGElement>("svg")!;
    const hover = svg.querySelector<SVGGElement>(".lc-hover")!;
    const cross = svg.querySelector<SVGLineElement>(".lc-cross")!;
    const dots = [...svg.querySelectorAll<SVGCircleElement>(".lc-dot")];
    const tip = el.querySelector<HTMLDivElement>(".lc-tip")!;
    const lead = usable.reduce((a, b) => (b.points.length > a.points.length ? b : a));
    let index = lead.points.length - 1;

    const show = (t: number): void => {
      const x = sx(t);
      cross.setAttribute("x1", x.toFixed(1));
      cross.setAttribute("x2", x.toFixed(1));
      tip.replaceChildren();
      const when = document.createElement("div");
      when.className = "lc-when";
      when.textContent = (opts.readoutDate ?? dateFmt)(t);
      tip.append(when);
      usable.forEach((s, i) => {
        const dot = dots[i]!;
        // a series that had not started (or had ended) at that date has no value there: no dot, no line
        const inside = t >= s.points[0]!.t - 60_000 && t <= s.points[s.points.length - 1]!.t + 60_000;
        dot.setAttribute("visibility", inside ? "visible" : "hidden");
        if (!inside) return;
        const p = s.points[nearest(s.points, t)]!;
        dot.setAttribute("cx", sx(p.t).toFixed(1));
        dot.setAttribute("cy", sy(p.v).toFixed(1));
        const row = document.createElement("div");
        row.className = "lc-row";
        const key = document.createElement("span");
        key.className = "lc-key";
        key.style.background = s.color;
        const value = document.createElement("b");
        value.textContent = readoutFmt(p.v);
        row.append(key, value);
        if (opts.readoutExtra) {
          const extra = document.createElement("span");
          extra.className = "lc-extra";
          extra.textContent = opts.readoutExtra(p.v);
          row.append(extra);
        }
        if (usable.length > 1) {
          const name = document.createElement("span");
          name.className = "lc-name";
          name.textContent = s.label;
          row.append(name);
        }
        tip.append(row);
      });
      hover.setAttribute("visibility", "visible");
      tip.hidden = false;
      // the readout sits beside the crosshair, on the side with room
      const box = el.getBoundingClientRect();
      const tipW = tip.offsetWidth || 160;
      const left = x + 12 + tipW > box.width ? Math.max(0, x - 12 - tipW) : x + 12;
      tip.style.left = `${left}px`;
    };
    const hide = (): void => {
      hover.setAttribute("visibility", "hidden");
      tip.hidden = true;
    };
    const fromPointer = (ev: PointerEvent): void => {
      const rect = svg.getBoundingClientRect();
      const x = ((ev.clientX - rect.left) / rect.width) * width;
      const t = t0 + ((x - padL) / Math.max(1, plotR - padL)) * (t1 - t0);
      index = nearest(lead.points, t);
      show(lead.points[index]!.t);
    };
    const hit = svg.querySelector<SVGRectElement>(".lc-hit")!;
    hit.addEventListener("pointermove", fromPointer);
    hit.addEventListener("pointerdown", fromPointer);
    hit.addEventListener("pointerleave", hide);
    svg.addEventListener("focus", () => show(lead.points[index]!.t));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", (ev: KeyboardEvent) => {
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
      ev.preventDefault();
      index = Math.max(0, Math.min(lead.points.length - 1, index + (ev.key === "ArrowRight" ? 1 : -1)));
      show(lead.points[index]!.t);
    });
  };
  draw();
  registry.set(el, draw);
  if (!listening) {
    window.addEventListener("resize", onResize);
    listening = true;
  }
}

/** The key under a chart: a line swatch, the series name and its last value. Text, so it wraps on a phone. */
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
