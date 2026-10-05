/** The JSON contract the engine writes (docs/SITE_DATA.md). Every number may be null: we never invent one. */

export type Num = number | null;

export interface Sourced<T> {
  value: T | null;
  asof?: string | null;
  source?: string | null;
}

export interface SourceHealth {
  source?: string | null;
  key?: string | null;
  status?: "green" | "yellow" | "red" | string | null;
  last_success_at?: string | null;
  data_asof?: string | null;
  latency_ms?: Num;
  message?: string | null;
  fallback_used?: string | null;
  rows?: Num;
}

export interface HealthDoc {
  generated_at?: string | null;
  overall?: string | null;
  checked_at?: string | null;
  sources?: SourceHealth[];
  cron_lag_minutes?: Num;
}

export interface PriceBlock {
  value: Num;
  asof?: string | null;
  source?: string | null;
  change_1d?: Num;
  ovx?: Sourced<number> | null;
  spot?: Sourced<number> | null;
}

export interface RegimeBlock {
  label?: string | null;
  confidence?: Num;
  probabilities?: Record<string, number> | null;
  change_point_prob?: Num;
  asof?: string | null;
  source?: string | null;
  approx?: boolean;
}

export interface PositionRow {
  instrument?: string;
  qty_bbl?: Num;
  avg_price?: Num;
  last_price?: Num;
  stop_price?: Num;
  target_price?: Num;
  strategies?: string[];
  opened_ts?: string | null;
}

export interface LeverageBlock {
  value?: Num;
  limited_by?: string | null;
  components?: Record<string, Num> | null;
}

export interface GateCondition {
  name?: string | null;
  ok?: boolean | null;
  detail?: string | null;
  value?: Num;
  threshold?: Num;
}

export interface GateBlock {
  passed?: boolean | null;
  reason?: string | null;
  conditions?: GateCondition[] | Record<string, GateCondition> | null;
  families_agreeing?: Num;
  ensemble_prob?: Num;
}

export interface AccountBlock {
  epoch?: Num;
  status?: string | null;
  dead?: boolean;
  initial_capital?: Num;
  equity?: Num;
  cash?: Num;
  pnl_day?: Num;
  pnl_total?: Num;
  pnl_total_pct?: Num;
  drawdown?: Num;
  peak_equity?: Num;
  margin_used?: Num;
  margin_level?: Num;
  gross_notional?: Num;
  net_notional?: Num;
  liquidation_price?: Num;
  leverage?: LeverageBlock | null;
  gate?: GateBlock | null;
  positions?: PositionRow[];
  asof?: string | null;
}

export interface PortfolioBlock {
  n_strategies?: Num;
  n_active?: Num;
  lifecycle_counts?: Record<string, number> | null;
  master_flat_by_design?: boolean | null;
  explanation?: string | null;
  source?: string | null;
  asof?: string | null;
}

export interface SummaryDoc {
  generated_at?: string | null;
  brent?: PriceBlock | null;
  regime?: RegimeBlock | null;
  data_status?: { overall?: string | null; checked_at?: string | null; sources?: SourceHealth[] } | null;
  account?: AccountBlock | null;
  portfolio?: PortfolioBlock | null;
  last_run?: { job?: string | null; ts?: string | null; status?: string | null; message?: string | null; cron_lag_minutes?: Num } | null;
  reset?: { workflow_url?: string; dispatch_api?: string; confirm_text?: string } | null;
  disclaimer?: string | null;
}

export interface EquityPoint {
  t: string;
  master: Num;
  leverage?: Num;
  drawdown?: Num;
  buy_hold_brent?: Num;
}

export interface EquityDoc {
  generated_at?: string | null;
  series?: EquityPoint[];
  shadows?: Record<string, { t: string; v: Num }[]>;
  master_1x?: EquityPoint[] | null;
  note?: string | null;
  source?: string | null;
}

export interface ForecastRow {
  horizon?: string;
  asof?: string | null;
  price_now?: Num;
  median?: Num;
  q05?: Num;
  q25?: Num;
  q75?: Num;
  q95?: Num;
  p_up?: Num;
  expected_vol?: Num;
  drivers?: string[];
  model?: string | null;
  approx?: boolean;
}

export interface ForecastsDoc {
  generated_at?: string | null;
  asof?: string | null;
  price?: Num;
  models?: Record<string, Record<string, number>> | null;
  horizons?: Record<string, ForecastRow>;
  track_record?: Record<string, unknown> | null;
  ovx_implied_range?: { low: Num; high: Num; days?: Num; approx?: boolean; source?: string | null } | null;
  source?: string | null;
}

export interface StrategyRow {
  id: string;
  name?: string | null;
  family?: string | null;
  lifecycle?: string | null;
  weight?: Num;
  weight_explanation?: string | null;
  performance?: Record<string, Num | string | Record<string, number>> | null;
  validation?: Record<string, unknown> | null;
  signal?: { ts?: string | null; direction?: string | null; prob?: Num; horizon_days?: Num; rationale?: string | null } | null;
}

export interface StrategiesDoc {
  generated_at?: string | null;
  strategies?: StrategyRow[];
  source?: string | null;
}

export interface MarketDoc {
  generated_at?: string | null;
  curve?: {
    asof?: string | null;
    m1_code?: string | null;
    points?: { rank: number; price: number }[];
    approx?: boolean;
    source?: string | null;
    note?: string | null;
  } | null;
  prices?: Record<string, { t: string; v: number }[]> | null;
  spreads?: Record<string, { t: string; v: number }[]> | null;
  cot?: { source?: string | null; series?: { t: string | null; mm_net: Num; oi: Num }[] } | null;
  geopolitics?: { source?: string | null; series?: { t: string; v: number }[] } | null;
  inventories?: {
    source?: string | null;
    unit?: string | null;
    band_years?: string | null;
    band?: { week: number; min: number; max: number; mean: number }[];
    current?: { week: number; v: number; t: string }[];
  } | null;
  events?: { id: string; name: string; ts: string; binary: boolean; confirmed: boolean; hours_away: number }[];
  regime_history?: { ts?: string | null; label?: string | null; confidence?: Num }[];
  source?: string | null;
}

export interface TradeRow {
  ts?: string | null;
  instrument?: string | null;
  qty_bbl?: Num;
  price?: Num;
  reference_price?: Num;
  slippage?: Num;
  commission?: Num;
  realized_pnl?: Num;
  reason?: string | null;
  price_source?: string | null;
  regime?: string | null;
  rationale?: string | null;
  strategies_for?: string[];
  strategies_against?: string[];
  gate_passed?: boolean | null;
  leverage?: Num;
  leverage_limited_by?: string | null;
}

export interface TradesDoc {
  generated_at?: string | null;
  trades?: TradeRow[];
  n_total?: Num;
  csv_url?: string | null;
  source?: string | null;
}

export interface RiskDoc {
  generated_at?: string | null;
  available?: boolean;
  var?: Record<string, Num> | null;
  es?: Record<string, Num> | null;
  leverage_history?: { t: string; v: Num }[] | null;
  margin?: Record<string, Num> | null;
  ruin?: Record<string, unknown> | null;
  stress?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface EpochRow {
  epoch?: Num;
  started_ts?: string | null;
  ended_ts?: string | null;
  start_equity?: Num;
  end_equity?: Num;
  max_equity?: Num;
  min_equity?: Num;
  end_reason?: string | null;
  n_trades?: Num;
  current?: boolean;
}

export interface EpochsDoc {
  generated_at?: string | null;
  epochs?: EpochRow[];
}

export interface ValidationDoc {
  generated_at?: string | null;
  available?: boolean;
  strategies?: Record<string, Record<string, unknown>>;
  master?: Record<string, unknown>;
  [key: string]: unknown;
}

export type DataOrigin = "live" | "bundled" | "sample";

export interface Loaded<T> {
  data: T | null;
  origin: DataOrigin | null;
  fetchedAt: string;
  error?: string;
}
