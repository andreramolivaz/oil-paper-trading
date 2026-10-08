/** The contract of `site-data/desk.json` (engine/desk/export.py). Every number may be null: nothing is invented. */
import type { Num } from "./types";

export interface Quote {
  name: string;
  symbol: string;
  price: Num;
  asof?: string | null;
  change: Num;
  prev_close: Num;
  prev_close_day?: string | null;
  source?: string | null;
}

export interface Hormuz {
  asof: string;
  tankers_7d: Num;
  baseline: Num;
  ratio: Num;
  tankers_7d_month_ago: Num;
  closed: boolean;
  source: string;
}

export interface MarketBlock {
  brent?: Quote;
  wti?: Quote;
  bno?: Quote;
  ovx?: { value: Num; asof?: string | null; source?: string; note?: string };
  brent_wti?: { value: Num; note?: string };
  hormuz?: Hormuz | null;
}

export interface ForecastBlock {
  vehicle: string;
  underlying: string;
  day: string;
  trend: Num;
  carry: Num;
  carry_momentum: Num;
  combined: Num;
  combined_prev: Num;
  vol: Num;
  slope: Num;
  slope_pair: string;
  slope_approx: boolean;
  return_source: string;
  approx: boolean;
}

export interface CurvePoint {
  t: string;
  v: number;
}

export interface BookPosition {
  symbol: string;
  units: number;
  unit: string;
  side: "long" | "short";
  avg_price: Num;
  last_price: Num;
  notional: Num;
  unrealized: Num;
  opened_at?: string | null;
}

export interface OptionLeg {
  option: string;
  right: string;
  strike: number;
  qty: number;
  fill: number;
  bid: number;
  ask: number;
  delta: Num;
  iv: Num;
}

export interface OptionCandidate {
  underlying: string;
  kind: string;
  expiry: string;
  dte: number;
  contracts: number;
  short: OptionLeg;
  long: OptionLeg;
  width: number;
  credit: number;
  credit_mid: number;
  max_loss_per_contract: number;
  breakeven: number;
  credit_to_width: number;
  underlying_price: number;
  quote_ts: string;
}

export interface OptionStructure {
  id: string;
  underlying: string;
  expiry: string;
  opened_at: string;
  contracts: number;
  short: OptionLeg;
  long: OptionLeg;
  width: number;
  credit: number;
  max_loss: number;
  max_gain: number;
  breakeven: number;
  mark: number;
  marked_at?: string | null;
  mark_stale?: boolean;
  underlying_last?: Num;
  rationale?: string;
  awaiting_close?: boolean;
}

export interface SmilePoint {
  strike: number;
  iv: Num;
  bid: number;
  ask: number;
  rel_spread: number;
}

export interface OptionUnderlying {
  symbol: string;
  label: string;
  forecast_series: string;
  forecast: Num;
  realized_vol: Num;
  threshold: number;
  price?: Num;
  iv30?: Num;
  quote_ts?: string | null;
  quote_age_minutes?: Num;
  expiry?: string;
  dte?: number;
  points?: Record<string, SmilePoint>;
  straddle_pct?: Num;
  atm_iv?: Num;
  iv_minus_rv?: Num;
  median_rel_spread?: Num;
  tradeable?: boolean;
  candidate: OptionCandidate | null;
  no_candidate_reason: string;
  gate: { open: boolean; reason: string; checks: { name: string; ok: boolean }[] };
}

export interface DecisionRecord {
  ts: string;
  rationale?: string;
  status?: string;
  exposure?: Num;
  limited_by?: string;
  order_units?: Num;
}

export interface Book {
  id: string;
  kind: "linear" | "options";
  name: string;
  description: string;
  vehicle: string;
  vehicle_name?: string;
  underlying?: string;
  robinhood?: string;
  note?: string;
  experimental?: boolean;
  rules: Record<string, unknown>;
  cap_today?: number;
  cap_reduced_today?: boolean;
  equity: Num;
  initial_capital: number;
  pnl_total: Num;
  pnl_total_pct: Num;
  pnl_day: Num;
  pnl_day_pct: Num;
  drawdown: Num;
  leverage: Num;
  exposure: Num;
  margin_level?: Num;
  liquidation_price?: Num;
  status: string;
  epoch: number;
  started_at?: string | null;
  marked_at?: string | null;
  positions?: BookPosition[];
  pending?: { symbol: string; units: number; decided_at: string; reason: string }[];
  n_fills?: number;
  costs?: { commission: Num; spread_and_slippage: Num; financing: Num };
  last_decision?: DecisionRecord | null;
  decision_owed?: boolean;
  last_roll?: string | null;
  curve: CurvePoint[];
  // options book
  structures?: OptionStructure[];
  max_loss_open?: Num;
  max_loss_open_pct?: Num;
  n_closed?: number;
  n_wins?: number;
  realized_pnl?: Num;
  monitor?: { generated_at: string; underlyings: OptionUnderlying[]; rules: Record<string, unknown> } | null;
}

export interface DecisionRow {
  ts: string;
  book: string;
  symbol: string;
  price: Num;
  forecast: Num;
  exposure: Num;
  limited_by?: string | null;
  order_units: Num;
  text: string;
  status: string;
}

export interface FillRow {
  ts: string;
  book: string;
  symbol: string;
  units: number;
  unit: string;
  price: Num;
  reference_price: Num;
  cost_bps: Num;
  commission: Num;
  reason: string;
  realized_pnl: Num;
  decided_at?: string | null;
  leverage_after: Num;
}

export interface Stats {
  years?: number;
  cagr?: number;
  vol?: number;
  sharpe?: Num;
  t_stat?: Num;
  max_drawdown?: number;
  worst_day?: number;
  worst_week?: Num;
  best_day?: number;
  longest_drawdown_days?: number;
  final_multiple?: number;
  leverage_median?: number;
  leverage_p95?: number;
  leverage_max?: number;
  share_days_above_1x?: number;
  share_days_invested?: number;
  share_days_short?: number;
  trades?: number;
  trades_per_year?: number;
  win_rate?: Num;
  avg_r?: Num;
  worst_r?: Num;
}

export interface Ruin {
  p_lose_half?: number;
  p_lose_quarter?: number;
  p_dead?: number;
  median_final?: number;
  p5_final?: number;
  p95_final?: number;
  method?: string;
}

export interface BacktestSummary {
  generated_at?: string;
  fill_rule?: string;
  books: Record<
    string,
    {
      vehicle: string;
      start: string;
      end: string;
      stats: Stats;
      ruin: Ruin;
      died: string | null;
      costs?: Record<string, number>;
      double_cost_sharpe: Num;
      same_close_sharpe: Num;
      last_years: Record<string, number>;
    }
  >;
  benchmarks: Record<string, { name: string; stats: Stats }>;
  options: { approx: boolean; stats: Stats; sharpe_range: [number, number] | null; method: string } | null;
}

export interface HealthBlock {
  overall?: string | null;
  checked_at?: string | null;
  n_sources: number;
  n_green: number;
  not_green: { source: string; status: string; message: string; asof?: string | null }[];
  last_tick?: { finished: string; status: string; message: string } | null;
  last_ok_tick?: string | null;
  ticks_24h: number;
  failed_24h: number;
}

export interface DeskDoc {
  generated_at: string;
  sample?: boolean;
  disclaimer: string;
  market: MarketBlock;
  forecast: Record<string, ForecastBlock>;
  books: Book[];
  total: { equity: Num; initial_capital: Num; pnl: Num; pnl_pct: Num };
  decisions: DecisionRow[];
  fills: FillRow[];
  health: HealthBlock;
  backtest: BacktestSummary | null;
  calendar: { today_ny: string; pre_closure: boolean; next_roll: Record<string, string> };
  data_notes: string[];
  data_missing: string[];
}

/** The full backtest payload (`desk_backtest.json`): what the backtest page draws. */
export interface BacktestBook {
  book: string;
  vehicle: string;
  start: string;
  end: string;
  stats: Stats;
  yearly: Record<string, number>;
  costs: Record<string, number>;
  ruin: Ruin;
  died: string | null;
  n_fills: number;
  curve: CurvePoint[];
}

export interface SleeveRow {
  id: string;
  name: string;
  stats: Stats;
  stats_same_close: Stats;
}

export interface BacktestDoc {
  meta: { generated_at: string; fill_rule: string; initial_capital: number; cost_multiplier: number };
  books: Record<string, BacktestBook>;
  benchmarks: Record<string, { name: string; stats: Stats; yearly: Record<string, number>; curve: CurvePoint[] }>;
  double_cost: Record<string, { stats: Stats }>;
  same_close: Record<string, { stats: Stats }>;
  sleeves: Record<string, { vol_target: number; long_only: boolean; note: string; rows: SleeveRow[] }>;
  books_config: { id: string; name: string; vehicle: string; vol_target: number; max_leverage: number }[];
  data: Record<
    string,
    { start: string; end: string; days: number; return_sources: Record<string, number>; slope_approx_share: number }
  >;
  options?: {
    approx: boolean;
    available: boolean;
    reason?: string;
    method?: string;
    sharpe_range?: [number, number] | null;
    headline?: { stats: Stats; yearly: Record<string, number>; curve: CurvePoint[]; start: string; end: string };
    cases?: { smile: string; cost_multiplier: number; stats: Stats }[];
  };
}

/** One colour per book, the same everywhere a book is drawn. The order is the validated series palette. */
export const BOOK_COLOR: Record<string, string> = {
  prudente: "var(--series-1)",
  dinamico: "var(--series-3)",
  spinto: "var(--series-2)",
  opzioni: "var(--series-6)",
};

export const bookColor = (id: string): string => BOOK_COLOR[id] ?? "var(--series-4)";
