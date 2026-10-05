/** Thin wrappers over lightweight-charts v5 so the pages do not repeat the theme wiring. */
import {
  AreaSeries,
  BaselineSeries,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  LineStyle,
  type LineWidth,
  type PriceFormatterFn,
  type Time,
  createChart,
} from "lightweight-charts";

import { dateOnly, num } from "../format";

/** Short Italian month ticks: the library would otherwise print "Oct '26" inside an Italian page. */
const MONTHS_IT = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"];

function timeToDate(time: Time): Date {
  if (typeof time === "number") return new Date(time * 1000);
  if (typeof time === "string") return new Date(`${time}T00:00:00Z`);
  return new Date(Date.UTC(time.year, time.month - 1, time.day));
}

function timeToIso(time: Time): string {
  return timeToDate(time).toISOString();
}

export const SERIES_COLORS = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6"];

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#3987e5";
}

const charts: IChartApi[] = [];

/** Dispose every chart of the previous page: the router calls this on each navigation. */
export function disposeCharts(): void {
  while (charts.length) {
    const c = charts.pop();
    try {
      c?.remove();
    } catch {
      /* already gone */
    }
  }
}

export function baseChart(
  el: HTMLElement,
  opts: { rightPrice?: boolean; timeVisible?: boolean; priceFormatter?: PriceFormatterFn } = {},
): IChartApi {
  const chart = createChart(el, {
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      // The axis is scaffolding: it reads muted and in the figure face, like the numbers it labels.
      textColor: cssVar("--text-muted"),
      fontFamily: cssVar("--mono"),
      fontSize: 10,
      attributionLogo: false,
    },
    // Horizontal rules only: vertical gridlines on a time series are noise, and the terminal convention
    // is a price ladder, not a grid.
    grid: {
      vertLines: { visible: false },
      horzLines: { color: cssVar("--border"), style: LineStyle.Solid },
    },
    // Every number the chart draws itself goes through the same it-IT formatters as the HTML around it.
    localization: {
      locale: "it-IT",
      priceFormatter: opts.priceFormatter ?? ((p: number) => num(p, 2)),
      timeFormatter: (time: Time) => dateOnly(timeToIso(time)),
    },
    rightPriceScale: {
      visible: opts.rightPrice !== false,
      borderVisible: false,
      scaleMargins: { top: 0.12, bottom: 0.1 },
    },
    timeScale: {
      borderVisible: false,
      timeVisible: opts.timeVisible ?? false,
      secondsVisible: false,
      // Pin the series to the full width: the default right offset left a third of the plot empty on a
      // short series, which reads as missing data rather than as a short history.
      rightOffset: 0,
      fixLeftEdge: true,
      fixRightEdge: true,
      tickMarkFormatter: (time: Time) => {
        const d = timeToDate(time);
        if (opts.timeVisible) {
          return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
        }
        return d.getUTCMonth() === 0
          ? String(d.getUTCFullYear())
          : `${MONTHS_IT[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
      },
    },
    crosshair: {
      mode: 1,
      vertLine: { color: cssVar("--border-strong"), width: 1, style: 2, labelBackgroundColor: cssVar("--surface-3") },
      horzLine: { color: cssVar("--border-strong"), width: 1, style: 2, labelBackgroundColor: cssVar("--surface-3") },
    },
    handleScale: { axisPressedMouseMove: false },
    // Without this the chart swallows vertical page scrolling on a phone.
    handleScroll: { vertTouchDrag: false, pressedMouseMove: true, horzTouchDrag: true, mouseWheel: false },
    autoSize: true,
  });
  charts.push(chart);
  return chart;
}

export function addLine(
  chart: IChartApi,
  data: ChartPoint[],
  color: string,
  opts: {
    width?: number;
    dashed?: boolean;
    title?: string;
    /** The unit decides the precision. A contract count formatted to 2 decimals reads as "274047,00". */
    precision?: number;
    priceFormat?: "price" | "percent";
    lastValue?: boolean;
  } = {},
): ISeriesApi<"Line"> {
  const precision = opts.precision ?? 2;
  const series = chart.addSeries(LineSeries, {
    color,
    lineWidth: (opts.width ?? 2) as LineWidth,
    lineStyle: opts.dashed ? LineStyle.Dashed : LineStyle.Solid,
    title: opts.title,
    priceLineVisible: false,
    // Off by default: with two series the two badges stack on each other. The readout strip shows them.
    lastValueVisible: opts.lastValue ?? false,
    priceFormat:
      opts.priceFormat === "percent"
        ? { type: "percent" }
        : { type: "price", precision, minMove: precision === 0 ? 1 : 10 ** -precision },
  });
  series.setData(data);
  return series;
}

export function addArea(
  chart: IChartApi,
  data: ChartPoint[],
  color: string,
  opacity = 0.18,
  opts: { precision?: number } = {},
): ISeriesApi<"Area"> {
  const precision = opts.precision ?? 2;
  const series = chart.addSeries(AreaSeries, {
    lineColor: color,
    topColor: withAlpha(color, opacity),
    bottomColor: withAlpha(color, 0.02),
    lineWidth: 2,
    priceLineVisible: false,
    // The readout strip carries the last value; a badge here would sit on top of the other series'.
    lastValueVisible: false,
    priceFormat: { type: "price", precision, minMove: precision === 0 ? 1 : 10 ** -precision },
    autoscaleInfoProvider: paddedAutoscale(data),
  });
  series.setData(data);
  return series;
}

/**
 * A series that never moves has no range, so the price scale repeats one tick three times and the line sits
 * on an edge. An account that has not traded yet is exactly that series, so it is worth handling: give a
 * constant series a symmetric window around its value.
 */
function paddedAutoscale(data: ChartPoint[]) {
  const values = data.map((d) => d.value).filter((v) => Number.isFinite(v));
  if (!values.length) return undefined;
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (max > min) return undefined;
  const pad = Math.max(Math.abs(min) * 0.002, 1);
  return () => ({ priceRange: { minValue: min - pad, maxValue: max + pad } });
}

export function addBaseline(
  chart: IChartApi,
  data: ChartPoint[],
  baseValue: number,
): ISeriesApi<"Baseline"> {
  const series = chart.addSeries(BaselineSeries, {
    baseValue: { type: "price", price: baseValue },
    topLineColor: cssVar("--good"),
    topFillColor1: withAlpha(cssVar("--good"), 0.18),
    topFillColor2: withAlpha(cssVar("--good"), 0.02),
    bottomLineColor: cssVar("--critical"),
    bottomFillColor1: withAlpha(cssVar("--critical"), 0.02),
    bottomFillColor2: withAlpha(cssVar("--critical"), 0.18),
    lineWidth: 2,
    priceLineVisible: false,
  });
  series.setData(data);
  return series;
}

function withAlpha(color: string, alpha: number): string {
  const hex = color.replace("#", "");
  if (hex.length !== 6) return color;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export interface ChartPoint {
  time: Time;
  value: number;
}

/** Chart data needs ascending unique times; the engine already sorts, this guards against duplicates.
 *
 * `intraday` keeps the instant instead of collapsing to the calendar date. The engine ticks every 30 minutes,
 * so collapsing by day threw that away and a one-day-old account could not be plotted at all. */
export function toLineData(
  points: { t: string; v: number | null }[] | undefined,
  opts: { intraday?: boolean } = {},
): ChartPoint[] {
  if (!points) return [];
  const seen = new Map<number | string, number>();
  const out: ChartPoint[] = [];
  for (const p of points) {
    if (p.v === null || p.v === undefined || !Number.isFinite(p.v)) continue;
    let key: number | string;
    let time: Time;
    if (opts.intraday) {
      const ms = Date.parse(p.t);
      if (Number.isNaN(ms)) continue;
      key = Math.floor(ms / 1000);
      time = key as Time;
    } else {
      key = p.t.slice(0, 10);
      time = key as Time;
    }
    const at = seen.get(key);
    if (at !== undefined) {
      out[at] = { time, value: p.v };
      continue;
    }
    seen.set(key, out.length);
    out.push({ time, value: p.v });
  }
  out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  return out;
}

/** True when a series spans more than one calendar day — the only case where a date axis says anything. */
export function spansMultipleDays(points: { t: string }[] | undefined): boolean {
  if (!points) return false;
  const days = new Set(points.map((p) => String(p.t).slice(0, 10)));
  return days.size > 1;
}

/**
 * The crosshair readout: a fixed strip above the plot that shows the hovered values, and the LAST values
 * when the pointer is away. It is why the series can drop their last-value badges, and it is the only way
 * a 500-point series in a 180px box is readable at all.
 */
export function attachReadout(
  chart: IChartApi,
  strip: HTMLElement,
  series: { api: ISeriesApi<"Line"> | ISeriesApi<"Area"> | ISeriesApi<"Baseline">; label: string; color: string; fmt: (v: number) => string }[],
): void {
  const render = (time: Time | undefined, values: Map<unknown, number>) => {
    const when = time === undefined ? "" : dateOnly(timeToIso(time));
    const parts = series.map((s) => {
      const v = values.get(s.api);
      return `<span class="k"><span class="swatch" style="background:${s.color}"></span>${s.label}
        <span class="v">${v === undefined ? "—" : s.fmt(v)}</span></span>`;
    });
    strip.innerHTML = `<span class="t">${when}</span>${parts.join("")}`;
  };

  const last = () => {
    const values = new Map<unknown, number>();
    let time: Time | undefined;
    for (const s of series) {
      const data = s.api.data();
      const point = data[data.length - 1] as { time: Time; value?: number } | undefined;
      if (point && typeof point.value === "number") {
        values.set(s.api, point.value);
        time = point.time;
      }
    }
    render(time, values);
  };

  chart.subscribeCrosshairMove((param) => {
    if (!param.time || !param.point) {
      last();
      return;
    }
    const values = new Map<unknown, number>();
    for (const s of series) {
      const d = param.seriesData.get(s.api) as { value?: number } | undefined;
      if (d && typeof d.value === "number") values.set(s.api, d.value);
    }
    render(param.time, values);
  });

  last();
}

export function fit(chart: IChartApi): void {
  chart.timeScale().fitContent();
}
