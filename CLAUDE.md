# CLAUDE.md — oil-paper-trading

Brent crude research + paper-trading system. Engine in Python 3.12 runs on GitHub Actions (one long-lived
runner that ticks every 30 minutes), persists JSON state on the `data` branch, and a static site (GitHub Pages,
Italian, dark, mobile-first) reads that JSON. **Paper trading only. Real data only. Never fabricate a value.**

There are two systems in this repository and it matters which one you are touching:

* **The desk** (`engine/desk`, `config/books.yaml`) is what trades: four paper books of 10 000 $ that buy one
  forecast (seven sleeves from three sources: the price of crude, the futures curve, copper and the dollar)
  through instruments a Robinhood account can hold - the BNO fund, the inverse fund SCO for the short side of
  a fund book, the /MCL micro WTI future, put credit spreads on USO/BNO. The terminal page shows it.
* **The first system** (`engine/strategies`, `engine/portfolio`, `engine/backtest/session.py`, ...) is 21
  strategies on shadow accounts behind a seven-condition validation gate. Nothing passed, so its master account
  is flat by construction. It still runs daily (the tick runs its end of day) and is reachable from the site's
  archive page. Do not "fix" its flat account by loosening the gate: that verdict is a result.

The full brief is in `docs/BRIEF.md` (authoritative requirements for the first system). What was measured, what
was rejected and every source: `docs/RESEARCH.md`. Book rules and strategy theses: `docs/STRATEGIES.md`.
Data-source verification notes: `docs/DATA_SOURCES.md`.

## Commands

```bash
# environment (uv, Python 3.12 pinned in .python-version)
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.lock
source .venv/bin/activate

# quality gates (CI runs exactly these)
ruff check . && ruff format --check .
mypy engine
pytest -q                      # unit tests, offline, deterministic (no network; property tests derandomized)
pytest -q -m network           # opt-in tests that hit real sources

# engine entry points (all idempotent; all read/write state under $OPT_STATE_DIR, default ./state)
python -m engine.cli tick                      # WHAT THE SCHEDULER RUNS: fetch what is due, fill, roll, decide, mark
python -m engine.cli tick --no-legacy          # the same without the first system's end of day (much faster)
python -m engine.cli desk-backtest             # replay the desk books, write state/desk/backtest.json
python -m engine.cli fetch --snapshot          # pull all sources, write point-in-time snapshot + health
python -m engine.cli eod                       # first system, after ICE settlement (the tick calls it when due)
python -m engine.cli weekly                    # COT, rig count, retrain, full backtest, validation report
python -m engine.cli backtest --start 1990-01-01 --end 2026-10-01 --report out/backtest
python -m engine.cli reset --confirm RESET [--book prudente|dinamico|spinto|opzioni|all]
python -m engine.cli export-site               # site-data/: desk.json for the terminal + the first system's files

# dashboard
cd site && npm ci && npm run dev               # Vite dev server
cd site && npm run build                       # static build to site/dist
```

## The desk (what trades)

```
engine/desk/
  signals.py    the seven sleeves (trend, accel, skew | carry, carry-momentum | copper, dollar), the
                combination by source, exposure = f/10 * target/vol
  data.py       the series: which contract is held when, its return and where it came from, the curve slope,
                the macro closes read a day late (known_before), the inverse fund's bars (DeskData.legs)
  vehicles.py   BNO and /MCL as Robinhood offers them: lot, margin, fees, financing, decision time; SCO, the
                inverse fund a fund book BUYS to be short
  book.py       one book: sizing, ceilings, Carver's position buffer, one or two legs, the orders and their
                Italian rationale
  engine.py     Desk: bars -> roll -> decision. The SAME three steps in the replay and live
  backtest.py   daily replay, performance, yearly returns, block-bootstrap ruin
  live.py       the tick: feed completed 30-minute bars, charge financing, roll, decide once a day, mark
  options.py    the options book on real Cboe quotes + its model replay (always approx)
  report.py     desk-backtest payload, attribution by sleeve and by source, the rules' fingerprint
  export.py     site-data/desk.json
config/books.yaml   the three linear books and the options book
```

The forecast (`signals.py`): three SOURCES of information weigh a third each, the sleeves inside a source weigh
the same. Price of crude: trend (EWMAC 8/32..64/256), acceleration (the trend minus itself n days ago), skew
(computed on the long WTI history, read by both vehicles). Curve: carry, carry-momentum. Other markets: the
same trend rule on copper and, inverted, on the dollar index, as of the PREVIOUS close. The combined multiplier
is 1.75 (Carver's formula on the measured correlations). Four of the seven were kept out of sixteen candidates
tried in October 2026: `docs/RESEARCH.md` has all sixteen with their numbers, and says how much of the gain is
selection.

How a tick works (`engine/live/jobs.py::tick`, driven by `scripts/runner_loop.sh`):
1. Fetch `desk_intraday` every tick. The daily tables are asked for BY NAME and only when needed
   (`_desk_daily_entries`): a vehicle's own tables on the tick that owes its decision, everything once after
   the US close, the context tables once a day, copper and the dollar on the tick that owes the FIRST decision
   of a day (never the evening before), and a table that was added to a list and never downloaded at once (it
   alone). Option chains hourly in US hours and on the tick that owes the options decision.
2. `live_tick`: each broker gets the 30-minute bars that COMPLETED since its last one (that is what fills the
   order queued by the previous decision and enforces margin, breaker and floor), financing, roll, then at
   most one decision per book per US session, taken at the first tick after the vehicle's decision time. A
   decision at 15:18 New York fills at the 15:30 open, and the fill shows up at the 16:18 tick.
3. `options_tick`: settle expired structures on the official close, mark at mid, decide once a session.
   A desk whose `desk/backtest.json` is missing, was computed under other rules (`meta.model`, see
   `report.model_signature`) or without a table that has since arrived computes it here once (a minute or
   two); otherwise the weekly job refreshes it.
4. Last, because it takes minutes: the first system's end of day, if the latest SETTLED London date has none
   (at most three attempts per date).
5. `export-site`, push the data branch.

### Desk things that are easy to get wrong
- **`BZ=F` / `CL=F` are not a contract.** Near an expiry Yahoo's intraday bars interleave two contracts (fake
  5-7 $ moves) and the daily series has fake roll returns. Books read the fund or the single contract
  (`cl_contracts`, `cl_intraday`); the legacy intraday entry asks for `front_contract_symbol()`.
- **Per-contract tables are cumulative.** Yahoo forgets a contract the day it expires; `fetch.py` merges each
  download with the archive. Never replace that with a plain overwrite.
- **Never feed a book bars older than what it already knows.** A symbol a book was never fed (a new book, the
  contract it has just rolled into, an inverse fund used for the first time) has no "last bar seen": replaying
  the archive would mark a live position at last week's price and trip a stop-out that never happened. "What
  it knows" is the time of the newest PRICE it was marked on (`last_price_asof`), not the clock of its last
  mark: every restart of the runner ticks at once, at any minute, and a tick that runs between a bar's end and
  its delivery has not seen that bar. One exception: the bar a queued order is waiting for is always fed, as a
  late bar if need be (`live_tick`; regression tests in `tests/test_desk_engine.py`).
- **Stamp decisions after the downloads.** `tick` re-reads the clock after fetching: an order dated before the
  data it was decided on could fill on a bar that opened before the decision was really taken.
- **A bar is complete fifteen minutes after it ends - on the table's clock, not on ours.** Yahoo returns
  the bar still forming and is 10-15 minutes late. A broker never looks twice at a bar, so `completed_bars`
  feeds one only when `FEED_DELAY` has passed (the runner ticks at :18 and :48 for this) SINCE THE DOWNLOAD
  (`observed_at`, `live._known_at`): when a tick's download fails, the table on file is the previous tick's,
  and its last row looks like a finished bar while holding the first minutes of one. Marks and sizing use
  `last_quote`, the newest print, which says how old it really is. The download time must REACH the tick:
  `build_desk_data` keeps `observed_at` on the half-hour tables and on nothing else. The first version of
  this rule was tested on tables built by hand and never ran in production, because the builder dropped the
  column - test such rules through the archive (`tests/test_desk_data.py`), not on a frame made for the test.
- **Risk is judged once per moment, on marks of that moment.** `Desk.feed` groups a book's bars by timestamp:
  every bar of a moment fills and marks (`on_bar(checks=False)`), then margin, floor and breaker are checked
  ONCE (`check_risk`), and only if the moment brought a bar for something the book holds. Judged bar by bar, a
  book holding the inverse fund was halted on the FUND's bar and its own half-hour-old quote. A bar arrived
  LATE (its table was missing for a tick or a day) when it belongs to an accounting day the broker has closed,
  or when it is a bar of something the book ALREADY HELD at the start of the tick and ends before the newest
  price that position was valued on: it fills what was waiting for it at its open and nothing else
  (`mark=False`) - replayed as news it priced today's position at yesterday morning's level against today's
  opening equity and tripped the breaker on a loss that never was. After a failed download the missed bars of
  the SAME day are news and are replayed in order; the bars that follow the late fill of a NEW position are
  the first news about it and are judged at once; a tick that finds no price for what a book holds leaves the
  age of its prices where it was (it never says "as of now").
- **The clock is read twice in a tick.** What to download is planned on the clock the tick starts with, the
  decisions are stamped with the clock read after the downloads. A decision time that passes in between
  (`jobs._overtaken_by_the_clock`: a restart ticks at any second) makes that vehicle wait for the next tick,
  which downloads its tables first.
- **One micro contract is 0.9x of a 10 000 $ account.** Sizing goes through `buffered_target` (Carver's
  inertia): half a lot wanted is not enough to buy one, and not little enough to sell one already held. The
  rationale tells the reader when whole lots moved the position away from the size wanted.
- **The options replay is a model.** No free history of option quotes exists (Alpha Vantage's
  `HISTORICAL_OPTIONS` answered "premium endpoint" on 2026-10-08). Everything derived from it
  carries `approx: true`, and no rule may be tuned on it: the book exists to replace it with real quotes.
- **A source going red must cost only what reads it.** `critical_sources` (config) is the only list that can
  turn the overall status red; `DESK_REQUIRES` (jobs.py) is what each vehicle needs for NEW decisions. Bars,
  stops, rolls and marks never wait for a download.
- **The runner checks the code out once.** It restarts on a push to main that touches engine, config or
  scripts (`runner.yml`); a change merged without such a push is not live until the next hand-over. A loop
  that ends in under fifteen minutes is restarted at once three times, then the chain stops and the scheduled
  watchdog takes over: a job that starts its own successor must not be able to respawn for ever.
- **Yahoo rate limits by address, and a limit costs the tick its bars.** Measured 2026-10-08: about two
  hundred chart requests in an hour, then HTTP 429 on everything for minutes. The "prices" group alone is
  sixty requests (36 + 12 curve months), so the tick never asks for a whole group it reads three tables of
  (`Fetcher.run_all(groups, entries)`), the end of day leaves the desk's tables to the tick, and after a 429
  that survives its retries `YahooAdapter` fails every call at once for two minutes instead of insisting.
- **A decision replaces the order still queued.** `Desk.decide` cancels a book's pending orders first: two
  decisions less than a bar apart (a book started at 14:48 catches up yesterday's, then takes today's at
  15:18) bought the target twice on the real data of 2026-10-08. And a book still on the old contract after a
  failed roll takes no decision on the new one (`ROLL_SYNC`: both legs priced within the same half hour).
- **The short side of a fund book is ANOTHER FUND, bought.** `short_via: SCO` (books.yaml) gives a book a
  second leg: a negative exposure is a LONG position in the -2x fund, half the dollars, paid in cash. What
  follows, each pinned by a test in `tests/test_desk_engine.py`, `test_desk_book.py`, `test_broker_orders.py`:
  (1) a fund's table has ONE symbol and no code column, so asking BNO's table for SCO returns BNO's prices:
  the leg is priced, filled and marked on its own table whatever the configuration says today
  (`live._price_table`), and `latest_price` answers nothing for a symbol a fund's table does not hold;
  (2) when a book changes side the SALE is fed first and the purchase WAITS for it (`Order.after`): the two
  tables can deliver their bars a tick apart, and the book must not hold both sides in between;
  (3) the sale of a fund is `reduce_only`: it closes what is there when it fills, never more (a leverage-cap
  sale between decision and fill left a full-size order that sold the fund short);
  (4) the broker counts the inverse fund for its multiple against the leverage cap (`exposure_weights`), at
  submit, at the FILL price and after every mark: a short side decided at its ceiling and filled on a gap
  ended above it, partly on margin. Margin and financing stay in dollars;
  (5) the submit check counts what a queued sale on another instrument is about to free
  (`_released_by_pending`), or the purchase of the other leg is refused outright - an allowance a book turns
  on (`net_queued_sales`): with its defaults the broker is, for the first system, exactly what it was on main;
  (6) the "never fed" floor of a symbol is read BEFORE any bar of the tick is fed (`live_tick`);
  (7) without a RECENT price of the inverse fund the short side is never opened or added to, only reduced on
  its last mark; a fund still held after `short_via` was removed is an orphan the next decision sells
  (`Book.leg`, `Book.uses`), and a purchase of it still queued is cancelled before any bar is fed. The
  ceilings are on the OIL exposure (`Book.net_exposure`, `effective_leverage`); the inverse fund is never an
  "old contract to roll".
  NOT handled: a split or reverse split of SCO or BNO (SCO has had several) - reset the book when one is
  announced; and a rollback to code older than phase 11 while SCO is held - flatten the book first.
- **Copper and the dollar: the previous close, from a table read the day after.** A decision of day D reads
  their close of D-1 (`data.known_before`: strictly before, at most seven days old). And the row of the day a
  table is DOWNLOADED on is not that day's close (`data.final_rows`, measured on Yahoo's `HG=F`, 5-9 October
  2026): while the session is open it is the live quote of the active month (0.9 % above what the row
  became), after 18:00 New York it is overwritten with the first minutes of the next session or has no close,
  and only a day later it is the spot-month settlement the whole history is made of. So the tables are
  downloaded on the tick that owes the first decision of a day, and a row is read only once a later day's
  download has confirmed it. A single contract (`CLX26.NYM`) does the same in the evening: never add an
  evening refresh of a futures table. The archive stores dates in milliseconds and `merge_asof` refuses keys
  of different resolution: both sides are brought to nanoseconds. Without the two tables only the two sleeves
  go silent; their source drops out and the multiplier shrinks.
- **The combination is by source, and its scalar twin must agree.** `signals.combine` (series) and
  `combine_values` (one day, what `Book` uses with its own weights) are tested row by row against each other,
  holes included. The multiplier is a straight line in the number of sleeves present out of SEVEN, so a book
  given a subset (the attribution tables) is not over-scaled.
- **A backtest belongs to the rules it was computed with.** Changing a constant in `signals.py`, `data.py`,
  `backtest.py`, `book.py` or `options.py`, a book, a vehicle, an inverse fund or the account rules in
  `config/risk.yaml` changes `report.model_signature`; the next tick recomputes (and so does a table that was
  missing when it was computed and has arrived since). Bump
  `MODEL_VERSION` when the replay itself changes in a way the rules do not show. The check and the replay sit
  in one `try`: a replay on the side never costs the tick.

## The first system (one event loop for backtest and live)

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
.github/workflows/  ci, runner (the tick loop), watchdog (restarts it), weekly, reset, deploy, keepalive,
                    eod (manual re-run of one date), research-data (manual download for research)
```

### Data flow
1. `fetch` writes raw snapshots to `state/raw/<source>/<key>/<observed_at>.parquet` (plus `latest.parquet`).
   Features only use rows with `published_at <= decision_time` (point-in-time). The weekly job compacts the
   archive to the last two snapshots plus the first of each ISO week, which keeps revisions (the GPR index is
   recomputed when its file grows) without growing the data branch.
2. `eod` builds features → regime → strategy signals → allocator → gate → orders → paper broker fills
   at the **next** available price (never the signal price). Everything appended to `state/*.jsonl`.
3. `tick` (every 30 minutes, see "The desk") runs the `eod` above when a settled London date has none. The
   old `update` job is no longer scheduled.
4. `export-site` writes compact JSON to `site-data/`; the site fetches it from the `data` branch
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
First system: `state/account.json`, `state/positions.json`, `state/trades.jsonl`, `state/equity.jsonl`,
`state/forecasts.jsonl` (append-only), `state/epochs.json` (reset history), `state/health.json`,
`state/regime.json`, `state/strategies.json`, `state/raw/...`.
Desk: `state/desk/desk_state.json`, `state/desk/decisions-*.jsonl`, `state/desk/backtest.json`, and one folder
per book (`prudente`, `dinamico`, `spinto`: the paper broker's `account.json`, `positions.json`, `trades`,
`orders`, `equity` logs; `opzioni`: `book.json`, `monitor.json`, `trades`, `decisions`, `equity`, `smile`).
Rotation: monthly rollover of jsonl files. Raw snapshots are trimmed by every tick (intraday tables keep one
copy, option chains one a week, daily tables the last two plus the first of each ISO week). The weekly job
squashes the branch's git history to one commit (`scripts/data_branch.sh squash`): the audit trail lives
inside the append-only logs, not in git. Never rewrite those logs; revisions are new observations.

## Hard rules
First system: `tests/test_leverage_invariants.py`, `tests/test_no_lookahead.py`. Desk: `tests/test_desk_*.py`,
`tests/test_tick_job.py`.
- Leverage ≤ 10x always, for every account. First system: > 1x only when `AlphaGate.passed` is true (all 7
  conditions). Desk: a book never targets more than its own ceiling (`max_leverage`, the lower
  `weekend_max_leverage` before a closure, the vehicle's margin); there is no gate, the ceiling IS the policy.
- A linear book's size is `forecast / 10 * vol_target / vol`, nothing else. No vol target above 0.50, and the
  targets are NOT raised because a backtest measured a higher Sharpe: part of that is selection.
- A fund book is short only through an inverse fund it BUYS with cash: that fund's notional never exceeds the
  equity, and the book's ceilings (weekend included) bind the oil exposure on both sides - at the decision,
  at the fill price and after every mark. A fund is never sold short: its sale is reduce-only.
- Margin, the account floor and the daily-loss breaker are judged on completed bars of what the book holds,
  all of the same moment; never on a quote, on another symbol's bar or on a bar older than the last mark.
- A sleeve enters the forecast with a published rule and its author's parameters, written down before its
  result is looked at; every candidate tried is reported in `docs/RESEARCH.md`, kept or not.
- The options book only opens defined-risk structures: max loss per structure ≤ 5% of equity, at most two open,
  priced on real quotes less than 45 minutes old, never at a price the feed did not bracket.
- Margin as the vehicle has it (10% for /MCL, 50% for a fund; the first system keeps 10%); stop-out when
  equity/margin falls below the vehicle's level; account dead when equity ≤ 5% of 10 000 $ → halted until reset.
- Fills happen at the first price strictly after the signal timestamp.
- Stop and target in the same bar → stop wins. Gap through stop → fill at gap open.
- Idempotency: every order carries `idempotency_key = sha1(epoch, decision_ts, strategy_set, instrument)`; re-running a job never duplicates a fill.
- No look-ahead: truncating the data at T must not change any signal at t < T (`tests/test_no_lookahead.py`).
- Missing/stale CRITICAL source → no NEW risk; a missing optional source silences what reads it and nothing
  else. Never fill gaps with invented numbers.
- A job that has nothing to do exits 0. A red cross must mean something broke.

## Conventions
- Code, comments, commit messages: **English**. UI, README, docs for the user: **Italian**.
- Type hints everywhere; `mypy engine` must pass. `ruff` with the config in `pyproject.toml`.
- All timestamps UTC-aware (`datetime` with `tzinfo=UTC`). London/New York via `zoneinfo`.
- Prices in USD/bbl; positions in barrels (lot = 100 bbl default, `config/risk.yaml`).
- Every number shown to the user carries `source` and `asof`.
- Approximations (synthetic options, curve proxies, GDELT gaps) are labelled `approx=true` in JSON and "≈" in the UI.
- Tests are offline by default; fixtures under `tests/fixtures/` are small real snapshots (dated, sourced).
- One commit per completed phase; messages `phase N: ...`.
- Secrets only via GitHub Secrets: `EIA_API_KEY`, `FRED_API_KEY`, `ALPHAVANTAGE_API_KEY` (all optional; system degrades gracefully).
- A parameter of a book is a published value or a stated rule of thumb, never the best cell of a grid. When a
  result depends on a choice, show the neighbours (`docs/RESEARCH.md` does) instead of picking the winner.
- Site: the terminal (`site/src/pages/terminal.ts`) formats `desk.json` and computes nothing. Plain monospace
  text, colour only for sign and state.

## State of play (2026-10-09)
Phases 1-11 of `docs/PLAN.md` are implemented. Phase 10 (this date) added the desk after three days of a flat
account with zero trades. The four causes, all fixed and pinned by `tests/test_tick_job.py`:
1. nothing was promoted, so the master's weight was zero by construction (now: the desk books trade);
2. the end of day ran after midnight London and asked for a date that had not settled (now: latest SETTLED date);
3. an optional table that was never downloaded turned the health red and halted everything (now: critical
   list). The insider chain was broken in three places: adapter not registered, table archived but never read
   back, and `engine/features/insider.py` without the `compute` the builder calls (`tests/test_insider.py`
   now checks every default feature module for it);
4. the `*/30` schedule fired about four times a day (now: a long-lived runner; the schedule is only a watchdog).

What the research found, in one paragraph: daily trend + carry + carry-momentum has a Sharpe of 0.3-0.5 on one
oil market after real costs (t = 1.5 on 15 years of the Brent fund, 2.9 on 40 years of WTI); every intraday
idea tested (noise-area breakout, first/last hour, EIA Wednesdays, settlement window, BNO-USO reversion,
short leveraged-ETF pairs) failed after costs; oil options are expensive on average, buying both ways loses,
hedged premium selling does not survive four legs, and what is left is a put credit spread sold in the
direction of the forecast - weak, model-based evidence, hence a paper book marked experimental.

Phase 11 (the same date, on the owner's request for a higher Sharpe) answered that a Sharpe rises with
independent sources of return and with the short side, not with leverage or tuning. Sixteen published signals
were tried once each on the desk's own series; four were kept (acceleration, skew, copper trend, inverted
dollar trend) and twelve rejected with their numbers (breakout, continuous carry, trend-curve agreement,
value, COT flow and hedging pressure, open interest, inventories twice, crack-spread momentum - a timing
artefact that vanished with a day of delay -, the volatility premium, insiders). With the real engine the
books went from 0.39 / 0.37 / 0.45 to 0.56 / 0.77 / 0.64 (prudente, dinamico, spinto); the dinamico book's
short side through SCO is worth 0.54 -> 0.77 and nearly all of it comes from 2014, 2015 and 2020. About half
of the forecast's improvement survives the selection-free estimate (everything tried, equal weights: 0.61
against 0.53 before and 0.70 chosen, WTI); copper and the dollar gave nothing from 2022 to 2025; all three
books lost money in 2023, 2024 and 2025 under the old forecast and under the new one; the options book's model
replay did not improve (0.63 before and after). The honest ceiling on ONE market is about 0.7-0.8: the next
real step is more markets, not more parameters. The volatility targets were deliberately left where they were.

Before it shipped, phase 11 was reviewed by an agent that had not seen it being written. Seven defects, each
reproduced, fixed and pinned by a test (the bullets under "easy to get wrong" say how): the breaker judged on
another symbol's bar and a stale quote; SCO marked on BNO's table after a configuration change; the short
side's ceilings held only at the decision price; one leg's table a tick behind the other (both sides held, or
the fund sold short); late bars replayed against today's opening equity (as old as the desk); the backtest's
staleness check outside its guard; copper probed for both macro tables. Checking the fixes against real
downloads found two more things about Yahoo, also fixed: a bar read while forming looks finished when the
next download fails, and the daily row of the day a table is read on is not that day's close. None of this
moved a number of the replay (the payload is identical before and after): they are all about degraded live
conditions the daily replay never meets.

A second reviewer then attacked the fixes with simulators of its own (minute-level truth, a late feed, failed
downloads, restarts at any minute, about 45 000 ticks) and found the one that mattered: the "complete on the
table's clock" rule never ran on the live path, because the builder dropped the download time before the
tick could read it - every test had put the column on by hand. Also fixed from that pass: the queued-sale
allowance leaked into the first system's default broker (now an opt-in); a new position opened by a late fill
waited a bar for its first risk check; a decision time passing during the downloads; a queued purchase of the
inverse fund surviving the removal of `short_via`; refused orders counted as queued and two false sentences in
the rationale; a fingerprint blind to `risk.yaml`. Its simulators, re-run on the fixed code, report only the
two classes that are by design (yesterday's last bar arriving after the accounting day has turned, and the
daily-close fallback dating a running row at "now"). A third pass on those fixes, same reviewer, same
simulators at the same volume, found nothing new but a cancel that left no note. The lesson is in the bullets
above: test a rule on the path production takes.

Every rule above was also checked by putting the defect back and watching a test fail (the tally is in
`docs/VALIDATION.md`). The property tests are deterministic (`tests/conftest.py`, profile `deterministic`);
search for counterexamples with `HYPOTHESIS_PROFILE=explore --hypothesis-seed=N` when the code under them
changes (120 seeds x 150 examples were clean on the broker's cap invariant).

Live since 2026-10-08 21:17 UTC. Checked on GitHub that evening, not assumed: the first tick downloaded
13 tables, took three decisions (21 and 43 BNO queued for the next open, no /MCL contract at 0.47x), computed
the backtest and ran the first system's end of day; `insider_form4` went green with 30 910 rows (the Alpha
Vantage key works); a runner cancelled on purpose was restarted by the watchdog; a two-minute runner handed
over to a successor started by `github-actions[bot]`; three pushes to main each replaced the runner in
progress, whose hand-over step was skipped. A tick takes about eight seconds and five requests. What was NOT
seen that evening: a fill (the first is due at the 9:30 New York open of 2026-10-09) and a scheduled run of the
watchdog (GitHub had not fired it yet; its logic was run by hand).

Phase 11 went live on 2026-10-09 at 06:03 UTC (commit 9611ac6). Checked, not assumed: CI ran its three jobs
on the pull request and again on main; the push cancelled the runner in progress and started a new one; its
first tick (06:04, off the slot) asked for exactly `sco_daily`, `copper_daily` and `dxy_daily` beside the
half-hour tables (seven downloads, none failed), recomputed the backtest once ("le regole sono cambiate",
fingerprint 5bf433f2e42a, the numbers above) and left the two BNO orders in the queue; the 06:18 tick fed the
bar that had ended at 06:00 - which the 06:04 tick had rightly not taken for finished -, downloaded the four
half-hour tables and recomputed nothing (the fingerprint is the same in another process); the public page
shows the seven sleeves by source, copper at its confirmed close of the 8th, and no console error. The
scheduled watchdog has now been seen: ONE run in nine hours (01:26 UTC) for a cron that asks for two an hour.

The first full day (2026-10-09), read on the data branch that evening. Both queued BNO orders filled at the
13:30 UTC open (reference 63.37, paid 63.392, 3.5 bp, no commission), one fill each. The 18:48 tick
downloaded copper and the dollar for the day's first decision - the row of the 8th was still 6.519, and the
row of the 9th (a live quote at 6.704, with thirty times the volume) was on file and was not read -, took the
futures book's decision (0.46x wanted, less than one lot: no position) and ran the first system's end of day
in the same tick: 337 seconds, the longest of the day, against a median of 7. The 19:18 tick took the fund
books' decisions on tables read at that tick, without asking for copper again ("posizione invariata", inside
the band, no order), and the options book's on quotes 44 seconds old (USO: forecast 3.8 under its threshold
of 5; BNO: the puts quoted 61-77 % wide). The 20:18 tick downloaded every daily table once for the official
closes; the 20:48 tick none. 56 ticks in 24 hours, all ok, no failed download, no slot missed, three
hand-overs; the scheduled watchdog ran four times in 23 hours. Equity at the close: 10 012.56, 10 025.70,
10 000, 10 000. Still NOT seen: a decision that TRADES under the new forecast, a short position through SCO,
a roll, the weekend stop of the runner and its restart by the watchdog on Sunday evening.

Noticed and not investigated: the first system's regime label read "Contango + eccesso d'offerta + trend
ribassista" at 99.96 % on 6, 8 and 9 October, with the curve in 25 % backwardation. It is the label of the
nearest state of an HMM fitted in August (`engine/regime/labels.py`, `approx`); nothing trades on it, but the
archive page shows it.

Read the Actions runs, do not assume them. Every CI run up to 3cf52aa was a **startup failure** — a
job-level `if: ${{ hashFiles(...) }}`, which GitHub rejects at parse time, so no job ever ran and the
failure looked like a normal red cross. The giveaway: the run's name was the file path instead of `ci`.
Likewise a `continue-on-error` step ending in `|| true` is always green and tells you nothing. And a
schedule is not a clock: check WHEN a scheduled run actually started before believing it ran on time.

The owner's setup is DONE (README.md records it): the repo is public, Pages is on with the GitHub Actions
source, the workflows have write permission, and the EIA and FRED keys are set as secrets (the Alpha Vantage
key was added by the owner on 2026-10-08: `insider_form4` turning green in `health.json` is the proof, check
it rather than assume it). The site is live at
https://andreramolivaz.github.io/oil-paper-trading/ and reads the `data` branch through
`raw.githubusercontent.com`.

## Market context (verify at startup, never hard-code regimes)
As of 2026-10-08 the Brent market is in a geopolitical-shock regime (US/Israel–Iran war since 2026-02-28, Hormuz
disruption: tanker transits about 1 a day against a norm of 50, IMF PortWatch). Real data on 2026-10-08:
Dec-26 Brent 104 $, Nov-26 WTI 91 $, BNO 64 $, realised volatility about 44%, OVX 49, backwardation about 25% a
year between Dec-26 and Dec-27. Weekend gaps in 2026 reached +16%. Use this only to prioritise features and
stress tests.
