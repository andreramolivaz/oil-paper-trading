/** Thin wrappers over lightweight-charts v5 so the pages do not repeat the theme wiring. */
import {
  AreaSeries,
  BaselineSeries,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  type LineWidth,
  createChart,
} from "lightweight-charts";

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

export function baseChart(el: HTMLElement, opts: { rightPrice?: boolean; timeVisible?: boolean } = {}): IChartApi {
  const chart = createChart(el, {
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: cssVar("--text-secondary"),
      fontFamily: getComputedStyle(document.body).fontFamily,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: cssVar("--border"), style: 1 },
      horzLines: { color: cssVar("--border"), style: 1 },
    },
    rightPriceScale: { visible: opts.rightPrice !== false, borderColor: cssVar("--border") },
    timeScale: {
      borderColor: cssVar("--border"),
      timeVisible: opts.timeVisible ?? false,
      secondsVisible: false,
    },
    crosshair: {
      mode: 1,
      vertLine: { color: cssVar("--border-strong"), width: 1, style: 2, labelBackgroundColor: cssVar("--surface-3") },
      horzLine: { color: cssVar("--border-strong"), width: 1, style: 2, labelBackgroundColor: cssVar("--surface-3") },
    },
    handleScale: { axisPressedMouseMove: false },
    autoSize: true,
  });
  charts.push(chart);
  return chart;
}

export function addLine(
  chart: IChartApi,
  data: { time: string; value: number }[],
  color: string,
  opts: { width?: number; dashed?: boolean; title?: string; priceFormat?: "price" | "percent" } = {},
): ISeriesApi<"Line"> {
  const series = chart.addSeries(LineSeries, {
    color,
    lineWidth: (opts.width ?? 2) as LineWidth,
    lineStyle: opts.dashed ? 2 : 0,
    title: opts.title,
    priceLineVisible: false,
    lastValueVisible: true,
    priceFormat: opts.priceFormat === "percent" ? { type: "percent" } : { type: "price", precision: 2, minMove: 0.01 },
  });
  series.setData(data);
  return series;
}

export function addArea(
  chart: IChartApi,
  data: { time: string; value: number }[],
  color: string,
  opacity = 0.18,
): ISeriesApi<"Area"> {
  const series = chart.addSeries(AreaSeries, {
    lineColor: color,
    topColor: withAlpha(color, opacity),
    bottomColor: withAlpha(color, 0.02),
    lineWidth: 2,
    priceLineVisible: false,
  });
  series.setData(data);
  return series;
}

export function addBaseline(
  chart: IChartApi,
  data: { time: string; value: number }[],
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

/** Chart data needs ascending unique dates; the engine already sorts, this guards against duplicates. */
export function toLineData(points: { t: string; v: number | null }[] | undefined): { time: string; value: number }[] {
  if (!points) return [];
  const seen = new Set<string>();
  const out: { time: string; value: number }[] = [];
  for (const p of points) {
    if (p.v === null || !Number.isFinite(p.v)) continue;
    const time = p.t.slice(0, 10);
    if (seen.has(time)) {
      out[out.length - 1] = { time, value: p.v };
      continue;
    }
    seen.add(time);
    out.push({ time, value: p.v });
  }
  return out;
}

export function fit(chart: IChartApi): void {
  chart.timeScale().fitContent();
}
