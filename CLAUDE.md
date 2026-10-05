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
1. `fetch` writes raw snapshots to `state/raw/<source>/<key>/<observed_at>.parquet` (plus `latest.parquet`).
   Features only use rows with `published_at <= decision_time` (point-in-time). The weekly job compacts the
   archive to the last two snapshots plus the first of each ISO week, which keeps revisions (the GPR index is
   recomputed when its file grows) without growing the data branch.
2. `eod` builds features → regime → strategy signals → allocator → gate → orders → paper broker fills
   at the **next** available price (never the signal price). Everything appended to `state/*.jsonl`.
3. `update` (30 min) marks positions, evaluates stops/targets intrabar, enforces circuit breakers, refreshes health.
4. `export-site` writes compact JSON to `site-data/`; the dashboard fetches it from the `data` branch
   (`raw.githubusercontent.com`, ~5-min CDN cache) with a bundled fallback snapshot.

### Things that are easy to get wrong here
- **The session has two paths and they must agree.** `strict_pit=True` rebuilds the features from
  `md.truncate(settlement)` for every day; the default builds the history once and slices it, and precomputes
  the regime walk-forward. The fast path is only legitimate because every feature is causal, which
  `tests/test_no_lookahead.py` pins down. If you add a feature that is not causal, that test must fail.
- **Multi-leg instruments need a price AND a registration.** A spread symbol gets its price from its legs
  (`TradingSession.price_instrument`) and its legs from `register_instrument`, and the outright legs (WTI,
  RBOB, heating oil fronts) are marked daily so the broker can measure a spread notional against the first
  leg's outright price. Skip either and every spread order is rejected for a missing reference price.
- **A backtest must never write into the live state.** `run_backtest_report` logs into a store under its own
  report directory; the live store only receives `validation.json` and the trials registry.
- **A missing feature is not always a reason to stop.** When a feature decides the DIRECTION (the curve for
  S4-S7, the inventory releases for S11) a strategy that cannot see it must return None. When it only decides
  the SIZE (the curve slope in S1) the honest behaviour is the conservative size, labelled in the signal.

### State on the `data` branch
`state/account.json`, `state/positions.json`, `state/trades.jsonl`, `state/equity.jsonl`,
`state/forecasts.jsonl` (append-only), `state/epochs.json` (reset history), `state/health.json`,
`state/regime.json`, `state/strategies.json`, `state/raw/...`. Rotation: monthly rollover of jsonl files,
weekly compaction of raw snapshots to parquet. Never rewrite history; revisions are new observations.

## Hard rules (enforced by `tests/test_leverage_invariants.py` and `tests/test_no_lookahead.py`)
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

## State of play (2026-10-05)
Everything in `docs/PLAN.md` phases 1-9 is implemented and committed. Verified by running it: 624 offline
tests, ruff/format/mypy clean, a real fetch recovering 23 of 25 sources with no API keys, an end-to-end
`eod` producing signals and 20 archived forecasts, and the dashboard rendering the real curve, COT and
geopolitical series. The 18.5-year validation promoted NOTHING (see `docs/VALIDATION.md`): the master is
flat by construction and the dashboard says so.

Read the Actions runs, do not assume them. Every CI run up to 3cf52aa was a **startup failure** — a
job-level `if: ${{ hashFiles(...) }}`, which GitHub rejects at parse time, so no job ever ran and the
failure looked like a normal red cross. The giveaway: the run's name was the file path instead of `ci`.
Likewise a `continue-on-error` step ending in `|| true` is always green and tells you nothing.

The owner's four setup actions are DONE and verified (README.md records them): the repo is public,
Pages is on with the GitHub Actions source, the workflows have write permission, and both API keys are set.
Verified on GitHub: `ci`, `update`, `eod` and `deploy` all green; the site is live at
https://andreramolivaz.github.io/oil-paper-trading/; `raw.githubusercontent.com` serves the `data` branch
(200), so the dashboard reads live JSON instead of the bundled fallback; 22 of 25 sources green, the three
yellows being data-quality warnings rather than missing keys.

## Market context (verify at startup, never hard-code regimes)
As of 2026-10-05 the Brent market is in a geopolitical-shock regime (US/Israel–Iran war since 2026-02-28, Hormuz
disruption). Real data on 2026-09-29: EIA Brent spot 113.96 $, WTI spot 96.16 $, Dec-26 Brent future ≈101 $,
steep backwardation (Dec-26 101 → Dec-27 82 → Dec-28 74). Use this only to prioritise features/stress tests.
