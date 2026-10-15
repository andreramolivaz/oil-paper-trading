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
  /** The two markets the macro sleeves read: last daily close on file. */
  copper?: MacroQuote;
  dollar?: MacroQuote;
}

export interface MacroQuote {
  name: string;
  value: Num;
  asof: string;
  source: string;
}

export interface ForecastBlock {
  vehicle: string;
  underlying: string;
  day: string;
  /** The seven sleeves (engine/desk/signals.py), each on the -20..+20 scale. */
  trend: Num;
  accel: Num;
  skew: Num;
  carry: Num;
  carry_momentum: Num;
  copper: Num;
  dollar: Num;
  /** The average of each source's sleeves: the three numbers the combination is made of. */
  sources: { prezzo?: Num; curva?: Num; macro?: Num };
  /** The date of the close each macro sleeve read: always before `day`; null when none was recent enough. */
  macro_day: { copper?: string | null; dollar?: string | null };
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
  /** The side of the OIL exposure: shares of an inverse fund (multiplier -2) are a short position. */
  side: "long" | "short";
  multiplier?: number;
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

/** What the allocator handed a book on its last decision day (engine/desk/allocator.py). */
export interface AllocatorScale {
  /** Multiplies the volatility target: 1 = the book's own rules, 0.5 = the bubble switch, 0 = retired. */
  risk: Num;
  /** Multiplies the ceiling before a weekend or a holiday (the strait's rule). */
  weekend: Num;
  reasons: string[];
  day?: string;
}

/** A book's live record as the lifecycle rule reads it (daily closes of its own equity log). */
export interface LifecycleRecord {
  role: string;
  months_live: Num;
  sharpe_live: Num;
  max_drawdown_live: Num;
  since?: string | null;
}

export interface RoleChange {
  role: string;
  since: string;
  reason: string;
}

export interface Book {
  id: string;
  kind: "linear" | "options";
  /** "core": one of the books the desk is judged on; "candidate": a paper book run beside them (phase 12);
   *  "retired": flat by the lifecycle rule, reopened only by the owner. */
  role?: "core" | "candidate" | "retired";
  /** The role in config/books.yaml; `role` differs when the lifecycle rule changed it (`role_change` says why). */
  configured_role?: string;
  role_change?: RoleChange | null;
  allocator?: AllocatorScale | null;
  lifecycle?: LifecycleRecord | null;
  /** Which forecast the book reads: "desk" (the seven sleeves, replayed in the backtest) or "kalshi" (the
   *  prediction markets' de-escalation index, phase 14: no history, no backtest, live fills only). */
  forecast_family?: "desk" | "kalshi";
  /** False for a book the replay cannot run: it has no row in the backtest, by construction. */
  backtested?: boolean;
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
  /** Cash the fund paid its holders, credited on the ex-date (the raw price falls by it that day). */
  distributions?: Num;
  /** The book's return on a gap of the vehicle's price ("-20%" -> fraction of equity), before any stop. */
  stress?: Record<string, Num>;
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
  monitor?: {
    generated_at: string;
    session_open?: boolean;
    underlyings: OptionUnderlying[];
    rules: Record<string, unknown>;
  } | null;
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

/** Several books held together with equal capital, rebalanced daily (engine/desk/report.py). */
export interface PortfolioStats {
  label: string;
  books: string[];
  start: string;
  end: string;
  stats: Stats;
  yearly?: Record<string, number>;
  method?: string;
}

export interface BacktestSummary {
  generated_at?: string;
  fill_rule?: string;
  portfolio?: Record<string, PortfolioStats>;
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
  /** The books on file the replay cannot run (the Kalshi book of phase 14), with the reason: no numbers. */
  excluded?: ExcludedBook[];
}

export interface ExcludedBook {
  id: string;
  name: string;
  vehicle: string;
  role?: string;
  forecast?: string;
  reason: string;
}

/** The prediction markets' de-escalation index the speculative book reads (phase 14, engine/desk/oracle.py):
 *  today's reading, the reference a week before, the change and the forecast it makes, with the rule. */
export interface OracleBlock {
  day: string;
  forecast: Num;
  index: Num;
  /** Today's index over the components both readings have: `index_common - index_reference` is the change. */
  index_common?: Num;
  index_reference: Num;
  components_common?: number;
  components_read?: number;
  change: Num;
  reading_day: string | null;
  reading_ts?: string | null;
  reference_day: string | null;
  components: { series: string; label: string; p: Num; p_reference: Num }[];
  note: string;
  rule: {
    components: { series: string; label: string }[];
    horizon_days: number;
    lookback_days: number;
    reference_window_days: number;
    stale_days: number;
    max_spread: number;
    deadband: number;
    forecast_per_point: number;
    text: string;
  };
  books: string[];
  archive_days: number;
  archive_first_day: string | null;
  source: string;
  disclaimer?: string;
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

/** The allocator's rules and what they say today (desk.json["allocator"]). */
export interface AllocatorBlock {
  rules: Record<string, { enabled: boolean; rule: string }>;
  /** The bubble switch per vehicle: on, and the last day the two-year run-up held. */
  runup: Record<string, { active: boolean; last_run_up: string | null }>;
  hormuz: { transit_ratio: Num; closed: boolean; weekend_scale: Num };
  /** The scales of the last decision, by book id. */
  books: Record<string, AllocatorScale>;
  lifecycle: Record<string, unknown> | null;
  role_changes: Record<string, RoleChange>;
}

/** One Kalshi market: a contract paying 1 $ if the event happens, so its price is a probability. */
export interface PredictionMarket {
  ticker: string;
  label: string;
  strike_type?: string | null;
  floor: Num;
  cap: Num;
  deadline?: string | null;
  yes_bid: Num;
  yes_ask: Num;
  last: Num;
  /** The probability read: the mid of bid and ask (the last trade when there is no real quote). */
  mid: Num;
  /** The same a day ago (the API's own "previous" quote) and the change since. */
  mid_24h_ago?: Num;
  change_24h?: Num;
  volume: Num;
  volume_24h?: Num;
  open_interest: Num;
  open_time?: string | null;
  close_time?: string | null;
  /** ≈ P(above the strike) under a lognormal at the fund's ATM implied volatility (price ladders only). */
  options_p_above?: Num;
}

export interface PredictionEvent {
  series: string;
  event: string;
  title: string;
  label: string;
  /** "price": strikes in $/bbl on Brent or WTI; "threshold": other numeric strikes; "deadline": "before a date". */
  kind: "price" | "threshold" | "deadline" | "list";
  close_time?: string | null;
  hours_to_close: Num;
  markets: PredictionMarket[];
  volume: Num;
  open_interest: Num;
  /** P(X > x) at the strikes, sorted by x. */
  curve?: [Num, Num][];
  /** `p_above_reference_bound`: when the reference falls outside the ladder, the bound the nearest strike gives. */
  implied?: { median: Num; p_above_reference?: Num; p_above_reference_bound?: string | null; note?: string };
  /** The desk's own quote the price ladder is read against. */
  reference?: { symbol: string | null; price: Num; asof?: string | null } | null;
  /** What the market settles on (its own price feed), when it is not the contract the desk quotes. */
  settles_on?: string | null;
  options_implied?: {
    symbol?: string | null;
    atm_iv?: Num;
    quote_ts?: string | null;
    median?: Num;
    p_above_reference?: Num;
    approx?: boolean;
    note?: string;
    stale?: boolean;
    quote_age_hours?: Num;
  } | null;
  /** Deadline ladders: the probability of the event before each date, in date order. */
  ladder?: { before: string; p: Num }[];
  /** What the desk measures itself about the same question (the strait's transits, the EIA stocks). */
  measured?: Record<string, unknown> | null;
  summary: string;
}

/** What the day's trades on a flagged market looked like (engine/desk/watch.py::footprint). */
export interface TradeFootprint {
  trades: number;
  note?: string;
  contracts?: number;
  top_share?: Num;
  top_trades?: number;
  side?: "yes" | "no";
  side_share?: Num;
  largest?: { contracts: number; yes_price: Num; at: string; side: string };
  first_large?: { contracts: number; yes_price: Num; at: string };
  block_trades?: number;
  price_path?: [Num, Num] | null;
  window_start?: string;
}

/** One unusual-activity alert: an event whose markets moved a lot on unusual volume in 24 hours. */
export interface PredictionAlert {
  key: string;
  day: string;
  ts: string;
  series: string;
  event: string;
  group: string;
  title: string;
  markets: {
    ticker: string;
    label: string;
    mid: Num;
    mid_24h_ago: Num;
    change_24h: Num;
    volume_24h: Num;
    open_interest: Num;
    move: string;
    volume: string;
    baseline_days: number;
  }[];
  n_markets: number;
  rest_of_ladder_move: Num;
  rest_flat: boolean;
  close_time?: string | null;
  footprint: TradeFootprint | null;
  footprint_note?: string | null;
  footprint_ticker?: string;
  text: string;
}

export interface PredictionBlock {
  source: string;
  unit: string;
  note: string;
  asked: string[];
  /** The unusual-activity alerts of the last week, newest first, and the rule they come from. */
  alerts?: PredictionAlert[];
  watch?: Record<string, string>;
  asof: string | null;
  age_hours?: Num;
  stale: boolean;
  markets?: number;
  groups: { id: string; label: string; events: PredictionEvent[] }[];
  past_close: { series: string; event: string; title: string; markets: number; close_time: string | null; note: string }[];
  empty_series: string[];
}

export interface DeskDoc {
  generated_at: string;
  sample?: boolean;
  disclaimer: string;
  market: MarketBlock;
  forecast: Record<string, ForecastBlock>;
  books: Book[];
  /** The sum of the core books, and apart the sum of the candidate books. */
  total: { equity: Num; initial_capital: Num; pnl: Num; pnl_pct: Num; n_books?: number };
  total_candidates?: { equity: Num; initial_capital: Num; pnl: Num; pnl_pct: Num; n_books?: number };
  decisions: DecisionRow[];
  fills: FillRow[];
  health: HealthBlock;
  backtest: BacktestSummary | null;
  calendar: { today_ny: string; pre_closure: boolean; next_roll: Record<string, string> };
  data_notes: string[];
  data_missing: string[];
  allocator?: AllocatorBlock;
  prediction_markets?: PredictionBlock;
  /** The index the speculative Kalshi book reads (phase 14); null when no book is on it. */
  oracle?: OracleBlock | null;
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
  /** One sleeve, one source, the three sleeves read before phase 11, or all seven. */
  kind?: "sleeve" | "source" | "before" | "all";
  name: string;
  stats: Stats;
  stats_same_close: Stats;
}

export interface BacktestDoc {
  meta: { generated_at: string; fill_rule: string; initial_capital: number; cost_multiplier: number };
  books: Record<string, BacktestBook>;
  portfolio?: Record<string, PortfolioStats>;
  benchmarks: Record<string, { name: string; stats: Stats; yearly: Record<string, number>; curve: CurvePoint[] }>;
  double_cost: Record<string, { stats: Stats }>;
  same_close: Record<string, { stats: Stats }>;
  /** One table per way a vehicle is traded: "BNO" (long only), "BNO + SCO" (short side in the inverse fund), "MCL". */
  sleeves: Record<
    string,
    { vehicle?: string; vol_target: number; long_only: boolean; short_via?: string | null; note: string; rows: SleeveRow[] }
  >;
  books_config: { id: string; name: string; vehicle: string; vol_target: number; max_leverage: number; role?: string }[];
  /** The books on file the replay cannot run (phase 14), with the reason. */
  excluded?: ExcludedBook[];
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
  /** The allocator the replay ran with (phase 13) and the same replay without it: always both. */
  allocator?: { rules: Record<string, { enabled: boolean; rule: string }>; config?: Record<string, unknown> } | null;
  without_allocator?: {
    books: Record<string, { stats: Stats; yearly?: Record<string, number> }>;
    portfolio?: Record<string, PortfolioStats>;
  } | null;
}

/** The portfolios the payload may carry, in the order they are shown, with the English label of each.
 *  "core_matched" is the core books cut to the candidates' window: the row to compare "all" with. */
export const PORTFOLIO_ORDER: [string, string][] = [
  ["core", "Core books together"],
  ["core_matched", "Core books, over the candidates' window"],
  ["all", "Core and candidates together"],
];

export function portfolios(block: Record<string, PortfolioStats> | undefined): [string, string, PortfolioStats][] {
  if (!block) return [];
  return PORTFOLIO_ORDER.filter(([key]) => key in block).map(([key, label]) => [key, label, block[key]!]);
}

/** One colour per book, the same everywhere a book is drawn. The order is the validated series palette. */
export const BOOK_COLOR: Record<string, string> = {
  prudente: "var(--series-1)",
  dinamico: "var(--series-3)",
  spinto: "var(--series-2)",
  opzioni: "var(--series-6)",
  gold: "var(--series-4)",
  copper: "var(--series-5)",
  agri: "var(--series-7)",
  // phase 13: fourteen books on seven hues, so the candidates repeat the palette (every key sits by its name)
  bonds: "var(--series-6)",
  equities: "var(--series-1)",
  uranium: "var(--series-2)",
  utilities: "var(--series-3)",
  lithium: "var(--series-5)",
  semis: "var(--series-4)",
  clean: "var(--series-7)",
};

export const bookColor = (id: string): string => BOOK_COLOR[id] ?? "var(--series-4)";
