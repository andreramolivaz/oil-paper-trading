# CLAUDE.md — oil-paper-trading

Brent crude research + paper-trading system. Engine in Python 3.12 runs on scheduled GitHub Actions,
persists JSON state on the `data` branch, and a static dashboard (GitHub Pages, Italian, dark, mobile-first)
reads that JSON. **Paper trading only. Real data only. Never fabricate a value.**

The full brief is in `docs/BRIEF.md` (authoritative requirements). Data-source verification notes are in
`docs/DATA_SOURCES.md`. Strategy theses are in `docs/STRATEGIES.md`.

## Commands

```bash
# environment (uv, Python 3.12 pinned in .python-version)
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.lock
source .venv/bin/activate

# quality gates (CI runs exactly these)
ruff check . && ruff format --check .
mypy engine
pytest -q                      # unit tests, offline, deterministic (no network)
pytest -q -m network           # opt-in tests that hit real sources

# engine entry points (all idempotent; all read/write state under $OPT_STATE_DIR, default ./state)
python -m engine.cli fetch --snapshot          # pull all sources, write point-in-time snapshot + health
python -m engine.cli update                    # intraday tick: prices, stops, mark-to-market, 30-min cadence
python -m engine.cli eod                       # after ICE settlement: signals, regime, forecasts, orders
python -m engine.cli weekly                    # COT, rig count, retrain, full backtest, validation report
python -m engine.cli backtest --start 1990-01-01 --end 2026-10-01 --report out/backtest
python -m engine.cli reset --confirm RESET     # archive epoch, flat, equity back to 10 000 $
python -m engine.cli export-site               # build compact JSON for the dashboard (site-data/)

# dashboard
cd site && npm ci && npm run dev               # Vite dev server
cd site && npm run build                       # static build to site/dist
```

## Architecture (one event loop for backtest and live)

```
engine/
  core/        events (Bar, Signal, Order, Fill), Clock, instruments, contract calendar (ICE Brent expiry)
  data/        source adapters + fallbacks + point-in-time store + quality checks + health
  features/    Yang-Zhang RV, OVX, curve slopes, Hurst/VR, spreads, inventories vs 5y, COT, news, events
  regime/      HMM (walk-forward) + BOCPD, readable labels, probabilities with history
  strategies/  S1..S20, each a `Strategy` emitting `Signal`s; each has a shadow account
  forecast/    RW/curve benchmarks, ARIMA/ETS, GARCH, HAR-RV, LightGBM quantile, stacking; evaluation
  portfolio/   S20 allocator, vol targeting, fractional Kelly, alpha gate, leverage components, breakers
  broker/      paper broker: next-price fills, slippage, commissions, margin 10%, stop-out, roll, audit
  backtest/    `TradingSession` event loop — SAME class drives backtest and live
  live/        wraps TradingSession with persisted state, idempotency keys, cron guards (London/NY DST)
  validation/  walk-forward, purged CV, CPCV/PBO, DSR, cost sensitivity, regime/crisis slices, CUSUM
  report/      backtest report + site JSON export
  monitoring/  source health, cron lag, GitHub issue alerts, live-vs-backtest divergence
config/        strategies.yaml, risk.yaml, events.yaml, data_sources.yaml
site/          Vite + TypeScript (vanilla) + TradingView Lightweight Charts
tests/         pytest; `network` marker for tests hitting real sources
.github/workflows/  ci, update, eod, weekly, reset, deploy, keepalive
```

### Data flow
1. `fetch` writes raw snapshots to `state/raw/<source>/<asof>.parquet|json` with `(observed_at, published_at)`.
   Features only use rows with `published_at <= decision_time` (point-in-time).
2. `eod` builds features → regime → strategy signals → allocator → gate → orders → paper broker fills
   at the **next** available price (never the signal price). Everything appended to `state/*.jsonl`.
3. `update` (30 min) marks positions, evaluates stops/targets intrabar, enforces circuit breakers, refreshes health.
4. `export-site` writes compact JSON to `site-data/`; the dashboard fetches it from the `data` branch
   (`raw.githubusercontent.com`, ~5-min CDN cache) with a bundled fallback snapshot.

### State on the `data` branch
`state/account.json`, `state/positions.json`, `state/trades.jsonl`, `state/equity.jsonl`,
`state/forecasts.jsonl` (append-only), `state/epochs.json` (reset history), `state/health.json`,
`state/regime.json`, `state/strategies.json`, `state/raw/...`. Rotation: monthly rollover of jsonl files,
weekly compaction of raw snapshots to parquet. Never rewrite history; revisions are new observations.

## Hard rules (enforced by tests in `tests/test_invariants.py`)
- Leverage ≤ 10x always; > 1x only when `AlphaGate.passed` is true (all 7 conditions).
- Margin requirement = 10% notional; stop-out when equity/margin < 50%; account dead when equity ≤ 5% of 10 000 $ → trading halts until reset.
- Fills happen at the first price strictly after the signal timestamp.
- Stop and target in the same bar → stop wins. Gap through stop → fill at gap open.
- Idempotency: every order carries `idempotency_key = sha1(epoch, decision_ts, strategy_set, instrument)`; re-running a job never duplicates a fill.
- No look-ahead: truncating the data at T must not change any signal at t < T (`tests/test_no_lookahead.py`).
- Missing/stale source → health yellow/red, no NEW risk; never fill gaps with invented numbers.

## Conventions
- Code, comments, commit messages: **English**. UI, README, docs for the user: **Italian**.
- Type hints everywhere; `mypy engine` must pass. `ruff` with the config in `pyproject.toml`.
- All timestamps UTC-aware (`datetime` with `tzinfo=UTC`). London/New York via `zoneinfo`.
- Prices in USD/bbl; positions in barrels (lot = 100 bbl default, `config/risk.yaml`).
- Every number shown to the user carries `source` and `asof`.
- Approximations (synthetic options, curve proxies, GDELT gaps) are labelled `approx=true` in JSON and "≈" in the UI.
- Tests are offline by default; fixtures under `tests/fixtures/` are small real snapshots (dated, sourced).
- One commit per completed phase; messages `phase N: ...`.
- Secrets only via GitHub Secrets: `EIA_API_KEY`, `FRED_API_KEY` (both optional; system degrades gracefully).

## Market context (verify at startup, never hard-code regimes)
As of 2026-10-05 the Brent market is in a geopolitical-shock regime (US/Israel–Iran war since 2026-02-28, Hormuz
disruption). Real data on 2026-09-29: EIA Brent spot 113.96 $, WTI spot 96.16 $, Dec-26 Brent future ≈101 $,
steep backwardation (Dec-26 101 → Dec-27 82 → Dec-28 74). Use this only to prioritise features/stress tests.
