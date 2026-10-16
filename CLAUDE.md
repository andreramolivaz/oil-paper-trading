# CLAUDE.md — oil-paper-trading

Brent crude research + paper-trading system. Engine in Python 3.12 runs on GitHub Actions (one long-lived
runner that ticks every 30 minutes), persists JSON state on the `data` branch, and a static site (GitHub Pages,
English since 2026-10-13, dark, mobile-first, four pages) reads that JSON. **Paper trading only. Real data only.
Never fabricate a value.**

There are two systems in this repository and it matters which one you are touching:

* **The desk** (`engine/desk`, `config/books.yaml`) is what trades: four paper books of 10 000 $ (`role: core`)
  that buy one forecast (seven sleeves from three sources: the price of crude, the futures curve, copper and the
  dollar) through instruments a Robinhood account can hold - the BNO fund, the inverse fund SCO for the short
  side of a fund book, the /MCL micro WTI future, put credit spreads on USO/BNO - and, since phases 12 and 13,
  ten CANDIDATE books (`role: candidate`: gold GLD, copper CPER, agriculture DBA, Treasuries TLT, equities SPY,
  uranium URA, utilities XLU, lithium LIT, semiconductors SMH, clean energy ICLN) that run the prudent book's
  rules on the price source alone, beside the four and in their own total. Since phase 13 an ALLOCATOR
  (`engine/desk/allocator.py`, `config/allocator.yaml`) scales every book's volatility target and weekend
  ceiling by published rules (bubble switch, strait rule, lifecycle; drawdown control measured and off), and
  the tick archives Kalshi's prediction markets (`engine/desk/prediction.py`) that no core book trades on.
  Since phase 14 ONE candidate book, `oracle` (`forecast: kalshi`, `engine/desk/oracle.py`), trades the micro
  WTI future in the direction of a de-escalation index read on that archive: speculative, no backtest, judged
  on live fills only, never promoted. Since phase 15 `oracle.py` is a REGISTRY of three indices (de-escalation,
  the Fed path read off the FOMC meetings' outcome markets, the recession probability off the yearly NBER
  markets) and two more speculative books read them with a sign per sleeve, long only, on the S&P 500 and
  utilities funds (`oracle_equities`, `oracle_utilities`); two others (bonds, gold) were registered, measured a
  negative Sharpe and do not exist. The terminal page shows all of it.
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
python -m engine.cli reset --confirm RESET [--book prudente|dinamico|spinto|opzioni|gold|...|clean|oracle|oracle_equities|oracle_utilities|all]
python -m engine.cli export-site               # site-data/: desk.json for the terminal + the first system's files

python -m engine.cli watch                     # the prediction markets alone (the weekend job): read, archive, judge
python -m engine.cli kalshi-backfill [--since 2026-01-01] [--file history.json]   # the archive's past from the candlesticks

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
                the macro closes read a day late (known_before), the inverse fund's bars (DeskData.legs); the
                satellite funds (SATELLITE_TABLES) on their own adjusted close, price source only
  vehicles.py   BNO and /MCL as Robinhood offers them: lot, margin, fees, financing, decision time; SCO, the
                inverse fund a fund book BUYS to be short; GLD, CPER, DBA, TLT, SPY, URA, XLU, LIT, SMH, ICLN
                for the candidate books (`_fund`)
  book.py       one book: sizing, ceilings, Carver's position buffer, one or two legs, the orders and their
                English rationale; `set_scale` takes the allocator's multipliers (`vol_target` is cfg x risk)
  engine.py     Desk: bars -> roll -> allocate -> decision. The SAME steps in the replay and live; roles
                (`state.roles`) and the scales of the day (`state.allocator`) persisted in desk_state.json
  allocator.py  the rules that recalibrate the desk: bubble switch (run-up), drawdown control (off), the
                strait's weekend rule (PortWatch transit ratio, publication lag), the lifecycle (promote /
                retire on the live record), all measured in docs/RESEARCH.md ch. 9 before being switched on
  prediction.py Kalshi's markets: the daily archive (desk/kalshi-YYYY-MM.jsonl, one record per event and day)
                and the terminal block (price ladders against the desk's quote and the options' lognormal twin,
                deadline and threshold ladders beside what the desk measures). No book reads it.
  oracle.py     the speculative Kalshi books' forecasts: a registry of indices (`INDICES`), each a sleeve read
                on the daily archive with one rule (today's reading against the one a week before, the change
                over the components both have, deadband, cap +-20). `deescalation` (phase 14: P within 90 days
                that the strait is normal, the US and Iran meet, a deal is signed; three deadline ladders at a
                constant horizon; -2 per point), `fedpath` (phase 15: the expected change of the Fed's target
                rate over the FOMC meetings within 90 days, each meeting's five outcome markets weighted by
                their odds, summed in bp; -2.5 per bp, dovish = positive), `recession` (phase 15: the yearly
                NBER markets as one ladder by year end, P within 365 days; +4 per point). A book names its
                sleeves WITH A SIGN (`sleeves` in books.yaml) and averages them; these books cannot be replayed
                (no history in the desk's series), so they are out of the backtest, the tables and the
                fingerprint (`report.replayed_books`) and the lifecycle never promotes them
  backfill.py   the archive's past (phase 14): Kalshi's daily candlesticks (bid, ask, last, volume, open
                interest at midnight New York, since each market opened) as the records the tick would have
                written, one per event and UTC day, `backfilled: true`; a day the tick read is never replaced;
                `engine.cli kalshi-backfill` and the manual workflow `kalshi-backfill.yml`, idempotent
  watch.py      the unusual-activity alert on the event markets: a 24-hour move of 10 points (or 5 with the
                odds tripled) on a day's volume far above the market's own (3x its median day, else a quarter
                of its open interest), one alert per event and day (desk/kalshi_alerts-*.jsonl) with the
                footprint of the day's trades; a GitHub issue for two days; a footprint, never a verdict
  backtest.py   daily replay (allocate before decide, run-up and transit ratios computed once per vehicle),
                performance, yearly returns, block-bootstrap ruin
  live.py       the tick: feed completed 30-minute bars, charge financing, credit distributions, roll, review
                the lifecycle once a day, allocate, decide once a day, mark; one vehicle failing never stops
                the others (`_tick_vehicle`)
  options.py    the options book on real Cboe quotes + its model replay (always approx)
  report.py     desk-backtest payload with and without the allocator, attribution by sleeve and by source,
                the rules' fingerprint (books, options, risk.yaml, allocator.yaml)
  export.py     site-data/desk.json (books with role/allocator/lifecycle, `allocator`, `prediction_markets`)
config/books.yaml      the three linear books, the options book, the ten candidate books (role: candidate) and the
                       speculative Kalshi books `oracle`, `oracle_equities`, `oracle_utilities` (role: candidate,
                       forecast: kalshi, `sleeves` with a sign)
config/allocator.yaml  the allocator's four rules with their published parameters and on/off switches
engine/data/adapters/kalshi.py   the public Kalshi API, one call a second (429 after about thirty in a burst);
                                 `fetch_history`: every market of a series with its daily candlesticks (the
                                 live endpoint, else `/historical/...` for a market archived after settlement)
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
   the US close, the context tables once a day, the prediction markets every hour at any hour
   (`DESK_HOURLY_ENTRIES`), copper and the dollar on the tick that owes the FIRST decision of a day (never the
   evening before), and a table that was added to a list and never downloaded at once (it alone). Option
   chains hourly in US hours and on the tick that owes the options decision.
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
- **Kalshi's market listing forgets settled markets.** `/markets?series_ticker=` stops listing a market a few
  weeks after it settles (read 2026-10-15: the September 2026 FOMC markets were there, July's were not, and
  `status=settled` returned the same sixty): a series' history starts where the listing does, so the Fed-path
  index has a past from 18 June 2026 only, and `fetch_history` asks for the settled markets by name anyway. The
  research download takes any series (`research_fetch.py --only kalshi-history --series A,B --since`).
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
- **A fund's distribution is cash, not a loss.** The forecast reads Yahoo's adjusted close, the fills and marks
  the raw bars: on an ex-date the raw price falls by the amount paid. `data.fund_distributions` reads the
  amounts off the jump in the adjustment factor (no dividend table is downloaded), `Desk.credit_distributions`
  has the broker credit `shares x amount` once per (symbol, ex-date) on the shares held when the ex-date
  opened (`day_start_positions`), in the replay before the day's bar and live at the tick whose download shows
  the ex-date (until then, that morning, the loss is visible). Measured on the archive: only DBA ever paid
  (3.5-4.4 % every December since 2023); BNO, SCO, GLD, CPER never. `MODEL_VERSION` 3. A split is still not
  handled.
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
- **A book that reads another forecast never reads the desk's.** `BookConfig.forecast` names the family
  (`desk`: the seven sleeves; `kalshi`: `oracle.SLEEVES`, one per index, read with the sign the book gives it in
  `sleeves`: the recession sleeve is +1 for a bond book and -1 for an equity book, and `combined_forecast` is the
  signed mean over the sleeves with a value, None when none has one). `Desk.decide` hands a non-desk book ONLY
  its entry of `book_forecasts` (the live tick builds it from the archive, `live.oracle_forecasts`, each index
  read once for every book on it) and `{}` otherwise,
  which is "missing data: position unchanged"; the replay skips such books; `report.replayed_books` is what the
  backtest, the fingerprint (tick and weekly job alike) and the attribution tables see. The reading of UTC day
  D is the first of the day (about 00:20 UTC, the evening before in New York): the book reads last night's
  prices, on purpose, so that the rule can be replayed on the archive line by line. The archive's past is
  Kalshi's daily candlesticks (`backfill.py`: a candle ends at midnight New York, 04:00 UTC in summer -
  the same "evening before" as the tick's reading, three hours later); a day with a live reading is never
  overwritten, so `record_reading` and `apply_backfill` agree on the key `day|event`, and the backfilled
  records sit AFTER the live ones in the month's file: every reader sorts by day, none by line.
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
site/          Vite + TypeScript (vanilla), SVG charts drawn by hand, no chart library; four pages: terminal,
               backtest, reset, archive (one English page on the first system)
tests/         pytest; `network` marker for tests hitting real sources
.github/workflows/  ci, runner (the tick loop), watchdog (restarts it), weekly, reset, deploy, keepalive,
                    eod (manual re-run of one date), research-data (manual download for research),
                    kalshi-weekend (the prediction markets while the runner sleeps: `engine.cli watch`)
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
Desk: `state/desk/desk_state.json` (books' last decision days, roles changed by the lifecycle, the allocator's
scales of the day), `state/desk/decisions-*.jsonl`, `state/desk/kalshi-*.jsonl` (the prediction markets, one
record per event and day), `state/desk/kalshi_alerts-*.jsonl` and `kalshi_watch.json` (the unusual-activity
alerts and the last reading judged), `state/desk/backtest.json`, and one folder per book (the linear books, core and
candidate: the paper broker's `account.json`, `positions.json`, `trades`, `orders`, `equity` logs; `opzioni`:
`book.json`, `monitor.json`, `trades`, `decisions`, `equity`, `smile`).
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
- A linear book's size is `forecast / 10 * vol_target * m / vol`, nothing else, where `m` is the allocator's
  multiplier (1 by default; 0.5 under the bubble switch; 0 for a retired book; NEVER above 1), the same rule
  for every book, written daily to the state and the terminal. No vol target above 0.50, and the targets are
  NOT raised because a backtest measured a higher Sharpe: part of that is selection. An allocator rule is
  switched on only after it was measured on the replay (`docs/RESEARCH.md` ch. 9 B), and the backtest always
  reports the books with and without it (`without_allocator`).
- The prediction markets (Kalshi) are archived and shown, and no CORE book reads them until there is a year of
  archive to test a rule on. CANDIDATE books declared speculative (`forecast: kalshi`: `oracle`, phase 14, on
  the owner's explicit request; `oracle_equities` and `oracle_utilities`, phase 15) each trade a rule written in
  `docs/RESEARCH.md` (ch. 10, ch. 11) before its first decision, on the daily archive alone, and - since the
  archive has a past - measured ONCE on it before the book starts, with the declared filter (a negative
  after-cost Sharpe does not start: bonds and gold did not). They have no backtest in the desk's replay, no row
  in its tables, no part in the fingerprint, and the lifecycle never promotes them (a promotion needs a
  backtest drawdown to compare with - for every book). A sleeve's scale is a declared round number checked
  once against Carver's normalisation (mean |F| about 10) and touched only if off by a factor of two, as
  written before the check. A market past its close time is not a probability. The unusual-activity alert
  flags a footprint by a declared rule and says so; it never calls anyone an insider and never trades.
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
- Code, comments, commit messages: **English**. The site and every string the desk writes for the reader
  (rationales, statuses, notes, labels in `desk.json`): **English** (the owner's instruction of 2026-10-13; the
  site was Italian before). README and `docs/`: **Italian**. The first system's own strings (strategy names,
  regime labels, `summary.json` texts) are still Italian and the archive page translates them by id.
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

## State of play (2026-10-13)
Phases 1-13 of `docs/PLAN.md` are implemented. Phase 10 (8 October) added the desk after three days of a flat
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

Phase 11 (9 October, on the owner's request for a higher Sharpe) answered that a Sharpe rises with
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

Noticed that day, diagnosed on a copy of the live state and NOT fixed (it is a research decision on the first
system, the owner's to take; `docs/PLAN.md` has it in Italian): the first system's regime label read "Contango
+ eccesso d'offerta + trend ribassista" at 99.96 % on 6, 8 and 9 October, with the curve in backwardation
(M1-M6 +10.6 %), the 63-day trend positive (z +1.0) and stocks at their five-year average. Three causes:
(1) `WalkForwardHmm._select_columns` chooses the slope column on the whole FRAME: the four rows of real Brent
curve (the archive starts on 2026-10-05) select `slope_m1_m6` and switch off the M1-M2 proxy, which has 9 138
rows since 1987; every training window then drops the empty column, so all 146 fits run without any curve
feature (hence `approx`); (2) `labels.py` reads `slope_m1_m6` only: the fallback slope, when it is the one in
the fit, never reaches the rules or the centroids; (3) a label names the long-run MEAN of an HMM state, not
the day: the vector of 8 October (volatility +1.1, trend +1.0, volatility premium -1.4) falls in a catch-all
stressed state whose mean has stocks at +1.2 and nothing else of note. On 5 October, on another fit grid and
before the curve rows arrived, the same market was "Squeeze rialzista" at 58 %. With (1) and (2) repaired in
a what-if the label of 8 October becomes "Shock/crash" at 100 % (one-month return +6 %: wrong again) and 3 442
days out of 9 917 change label. So it is not a one-line fix: it rewrites the regime history that S3, S8, S10,
S11, S14, S17, the gate's "geopolitical long" condition, the S20 weights and the forecast ensemble read, and
it must be followed by a new validation. Until then no label says "geopolitico" in the middle of a war, so
the safeguards those strategies hang on that word are off. Nothing on the desk reads the regime.

The first weekend (9-12 October), read on GitHub and on the data branch. On Friday the runner whose turn ended
at 22:05 UTC handed over; its successor ticked twice (22:05 off the slot, 22:18), found the market closed at
22:48 and started nobody ("weekend: no successor"). On Saturday at 10:05 the weekly job ran for the first time
with the desk live (17 minutes, ok): it recomputed the desk backtest under the same fingerprint (0.56 / 0.77 /
0.64 through 9 October) and the first system's validation (nothing promoted, master Sharpe -0.76, PBO 0.80),
saved `models/regime.pkl`, deleted 280 raw snapshots (the state went from 88 to 64 MB) and squashed the data
branch to one commit. The scheduled watchdog ran ten times from Friday night to Sunday evening, three to seven
hours apart: nine times "weekend: nothing to do", and at 22:27 UTC on Sunday - 27 minutes after Globex reopened,
57 after its own window - it found no runner and started one. That runner's first tick (22:27, off the slot)
refreshed the daily tables as "older than 36 hours", the futures' too: that Sunday evening Yahoo had no row
after Friday's and Friday's rows were those of Saturday, so the evening refresh that spoils a weekday's row did
no harm - seen once, not proven. It fed no bar - the 22:00 one was still forming and nothing of Friday was fed
again - and the fund books stayed on Friday's official close (63.99, `last_price_asof` 2026-10-09 20:30) for 32
ticks. Copper's row of Friday, a live 6.704 when it was read during the session, was 6.6425 with the spot
month's volume on Saturday morning: final a day later, as measured. On Monday the 13:48 tick marked the fund
books on the first print (BNO +2.3 % on Friday's close), the 14:18 tick fed the first bar (13:30-14:00) as news
(no breaker, no stop, no order), the runner's turn ended at 14:28, its successor ticked at 14:28:51 without
taking the forming bar for finished, and the 14:48 tick fed the 14:00 bar once. One thing to know when reading
an evening: on the day itself Yahoo's half-hour table of a fund ends with a row stamped 16:00 New York that
holds the official close (the books were marked on it on Friday); by the next session that row is gone. From
Friday 20:18 to Monday 14:48 UTC: 42 ticks, all ok, 194 downloads, none failed. Still NOT seen: a decision that
TRADES under the new forecast, a short position through SCO, a roll.

Phase 12 (13 October, on the owner's request to "find new strategies for the current situation, keep the active
ones, make the site simple and in English"; `docs/RESEARCH.md` chapter 8) was pre-registered before any test:
four satellite markets through Robinhood funds (gold GLD, copper CPER, agriculture DBA, natural gas UNG with
KOLD for the short side) under the prudent book's rules and the price source alone, two new sources for the oil
books (basis-momentum, the geopolitical risk index), the calendar spread as a measurement, and a declared
filter: a negative after-cost Sharpe over the whole history does not start. Results, all on real adjusted
closes downloaded by the `research-data` workflow (Yahoo rate-limits the workspace): gold 0.50 (t 2.3, 20.7
years), copper 0.26, agriculture 0.34, gas -0.12 (filtered out: UNG lost 99.9 % to its contango since 2007,
KOLD its daily reset); basis-momentum 0.02, GPR -0.07 to 0.05, both rejected; the calendar spread 0.62 gross
(t 3.9) and -0.64 net (34 %/year of /MCL legs on a 10 000 $ account) - the term premium exists and is not
collectable with these instruments. The three oil books plus the three satellites at equal capital: Sharpe
0.80 -> 0.87, volatility 29 % -> 15 %, max drawdown -61 % -> -30 %, 2023/24/25 from -17/-19/-27 % to -9/-3/-8 %
(and the good years halved). It is diversification, not alpha, and the site says so. The three satellites run
as CANDIDATE books (`role: candidate`, own total, judged on real fills); the four core books and their backtest
payload are byte-identical before and after (verified by re-running the previous code on the same series).
The owner also asked for "a Sharpe above 3 with one strategy": answered that it does not exist on these
instruments at this frequency and that a backtest showing it would be the proof of an error, not of a strategy
(`docs/RESEARCH.md` has the paragraph). Engine side: `vehicles.GLD/CPER/DBA`, `data.SATELLITE_TABLES` and
`price_only_series`, `BookConfig.role`, `export` with `stress` (the book's return on a -30..+20 % gap of its
instrument), `total_candidates` and `backtest.portfolio` (`core`, `all`, `core_matched`: the core books cut to
the candidates' window, so the two rows compare like with like), the tick planning the satellites' tables like
the funds' and only while a book is on them. Every string the desk writes for the reader became English, so the
fingerprint changed and the first tick after the deploy recomputes the backtest once ("the rules changed").
Before it shipped, an independent reviewer attacked the engine side with its own simulators (the first live
ticks of the new books on the archive path, download planning over 36 slots, a core-only desk against the full
one tick by tick, main's code against this branch on one archive) and found the core books byte-identical and
one defect that mattered: a fund's cash distribution booked as a loss (fixed, above, with the DBA numbers), plus
hardening items left for later (`docs/PLAN.md`: per-vehicle isolation in `live_tick`, the satellites' intraday
tables in `INTRADAY_ENTRIES`/`FRESHNESS`, `core_matched` cut at both ends, a fingerprint blind to cosmetic text,
the intraday group downloaded whole, a red candidate table retried every tick).
Site: four English pages (terminal with the candidates group and the "if -20 % gap" column, backtest with the
portfolios and the phase-12 rejects, reset, one archive page on the first system that translates its Italian
strategy names and regime labels by id); the six Italian pages of the first system and their chart library are
gone (bundle 300 -> 73 kB), their paths redirect to the archive.

Phase 12 went live on 2026-10-13 at 21:46 UTC (PR #10, rebase-merged as 84e944f4; CI green on the branch and on
main; Pages redeployed in 35 s). Checked on GitHub and on the data branch, not assumed: the push cancelled the
runner in progress (its last tick, 21:18, was pushed) and started a new one; its first tick (21:47, off the
slot, 118 s) asked for exactly `gld_daily`, `cper_daily`, `dba_daily` beside the half-hour group (10 downloads,
none failed), recomputed the backtest once ("computed: the rules changed", 7 books, portfolio `core` /
`core_matched` / `all`), took the three candidate decisions on the day's last print - copper: forecast +10,
volatility 22 % against a target of 12 %, 0.55x wanted, 123 CPER queued for the 14th's open (0.50x, at the
edge of the inertia band); agriculture: forecast -0.8, long only, cash; gold: forecast +0.9, 0.05x, no
position - and left the core books exactly where the previous runner had them (prudente 10 035.97 with 4 BNO
and dinamico 10 072.94 with 6 BNO queued at 19:18 and filled at the 20:18 tick, under the old code). The health
warnings are the five that were there before the deploy (copper's live row, DXY from FRED, the rig-count page,
STEO). The public page renders the four English pages on the live JSON with no console error; the last Italian
words on it are the decision texts the old engine wrote, which age out of the list. Still NOT seen: the first
fill of a candidate book (due at the 13:30 UTC open of the 14th), a DBA distribution (late December), a tick
with a failed vehicle.

Phase 13 (13-14 October, on the owner's requests: "signals and insights for the current situation - Hormuz,
the scenarios, electrification and the data centres' demand, bubbles", "dynamic: it changes depending on how
it goes", "also the four books", "also the Kalshi markets that know first: take the data, and everything you
said in a proper algorithm"; `docs/RESEARCH.md` chapter 9, written down before any test). What was measured,
all on real adjusted closes from the `research-data` workflow: five theme funds under the prudent book's rules
(uranium URA 0.35, utilities XLU 0.43, lithium LIT 0.67 t 2.6, semiconductors SMH 0.47, clean energy ICLN 0.75
t 3.1; all pass the declared filter, which therefore did not bite: a selection, said as such), plus Treasuries
TLT 0.37 and equities SPY 0.58 from chapter 8 D; thirteen books at equal capital since 2013: Sharpe 1.18
(t 4.4), +9.1 %/year, volatility 7.7 %, max drawdown −13 %, against 0.93 for the eight without the themes -
diversification, not alpha. The allocator's four rules: the bubble switch (Greenwood-Shleifer-You: +100 % over
504 days → risk × 0.5 for 252 days) raised the Sharpe of every portfolio in both halves (core 0.80 → 0.87, all
0.93 → 1.00) and is ON; drawdown control (Grossman-Zhou) cut the drawdown by a third and the Sharpe by
0.05-0.14 and is OFF; strategy momentum (0.61 against 0.80) rejected; the strait's weekend rule (transits below
10 % of normal → weekend ceilings × 0.5) and the lifecycle (promote after 12 months live with Sharpe > 0 and
the drawdown within the backtest's; retire after 24 months with Sharpe < −0.5) enter as declared policies.
The volatility forecast (HAR-RV, multifractal) and the cleaned correlation weights (RMT) were tried in chapter
8 E and rejected: Carver's EWMA and 1/N stay. Hard rule amended (chapter 9 D): size = `forecast/10 × target ×
m / vol`. Today the bubble switch is on for BNO, MCL and GLD: the three oil books and the gold book run at half
their volatility target, and the terminal says so on every decision. Kalshi: the public API has the series the
desk wants (Brent and WTI by day/week/month, the strait, the US-Iran talks, OPEC, EIA stocks). The runner's
first reading (14 October, 01:22 UTC): 296 markets in 13 events - Brent for the 14th, the 16th and the 30th
(the crowd's medians 104.2-104.4 $ against the desk's Dec contract at 107.0: the market settles on its own
Brent feed, a basis of about a contract month), WTI for the 14th, 15th, 16th (a ladder of bins) and Nov 3,
the strait back to normal (before Dec 1 4 %, Jul 2027 38 %, Jan 2029 67 %), the week's transits (median 22),
October's maximum (at least 8: 52 %), a US-Iran meeting (before Jan 1 42 %) and deal (12 %), the EIA stocks
(median 421.4 against 424.1 the week before); five series had no open market. Lesson written down: the
probes made from the workspace through the fetch proxy had returned months-old copies of some series - only
the runner's download counts, and the tests' fixture is that download. The tick archives one reading a day;
nothing trades on it. Engine side: `allocator.py`,
`prediction.py`, `adapters/kalshi.py`, seven vehicles and books, `Desk.allocate` between roll and decision in
the replay and live, `review_lifecycle` once a day, per-vehicle isolation in `live_tick`, `without_allocator`
in the backtest payload, the fingerprint covering `allocator.yaml`. Site: an Allocator section and a
Prediction markets section on the terminal, the risk scale in the books' Cap column, "With and without the
allocator" on the backtest page. Tests: `tests/test_desk_allocator.py`, `tests/test_desk_prediction.py` (17, on
the runner's first reading, `tests/fixtures/kalshi_markets_2026-10-14.json`), `tests/test_desk_live_isolation.py`,
`tests/test_desk_distributions.py`. Not done, written in `docs/PLAN.md`: natural gas through /MNG, the short
side of the satellites via SH/TBF (tables downloaded, not used), the intraday group downloaded whole.

Phase 13 went live on 2026-10-14 at 01:20 UTC (PR #12, rebase-merged as cd316462; CI green on the branch and
on main; Pages redeployed), with a correction at 01:48 UTC (PR #13, 6d335e21). Checked on GitHub and on the
data branch, not assumed: the push cancelled the runner in progress and started a new one; its first tick
(01:22-01:28 UTC, six minutes) asked for exactly the seven new daily tables and `kalshi` beside the half-hour
group (22 downloads, none failed), archived 296 markets in 13 events, recomputed the backtest once ("computed:
the rules changed", 13 books, with and without the allocator: `core_matched` 0.80 -> 0.86, `all` 1.18 -> 1.23),
ran the lifecycle review (no change: the oldest book has 0.3 months), and left the seven new books' decisions
of the 13th "waiting, data end at 2026-10-12" - Yahoo's daily chart read at 21:22 New York had no row for the
13th, while GLD's table read at 17:47 had its row: a fund's daily table read late in the evening can lack the
day's row, so a new book's first decision comes the next session at its decision time (the 14th, 15:18 New
York). The bubble switch is on for BNO, MCL, GLD, LIT, SMH and URA; the strait is at 2 % of normal (weekend
ceilings halved). The tick after the correction took 20 s, downloaded only the half-hour group (14 tables),
recomputed nothing (same fingerprint) and archived nothing new (same day). The public page renders the
Allocator and Prediction markets sections on the live JSON with no console error. Still NOT seen: a decision
taken under the allocator (the first due at 14:48 New York on the 14th for /MCL, expected "risk x 0.5" in its
rationale), the first fills of the seven new books, a lifecycle change, a Kalshi reading older than a day.

The unusual-activity alert (14 October, on the owner's "there is a lot of insider trading on Kalshi: add that
as an alert"; `docs/RESEARCH.md` chapter 9 F, declared before it went live). The adapter now carries the API's
24-hour fields (`volume_24h`, the quote "a day ago", the sizes at the touch, `open_time`) and reads a market's
trades (`fetch_trades`); the tick reads the markets every hour and `engine/desk/watch.py` judges each reading:
an event market (never a price ladder) that moved 10 points in 24 hours (or 5 with the odds tripled) on a
day's volume of 3x its median day (after ten daily readings; before that a quarter of its open interest, for a
market open two days) makes one alert per event and day with the footprint of the day's trades on its
busiest market (number, the share of the five largest, the taker's dominant side, the largest and the first
large one) and whether the rest of the ladder moved; the alerts job opens a GitHub issue for two days
(`prediction_market_alerts`), the terminal shows a week. On the runner's reading of 03:03 UTC with a week of
real trades (fixtures `kalshi_markets_2026-10-14T03.json`, `kalshi_trades_2026-10-14.json`): the 181 506-contract
day on "strait normal before Nov 1" (one 100 000-contract trade at a cent, no move: a yield trade) is not
flagged, the WTI ladders that jumped with oil are out of scope, and two alerts stand - the EIA inventories
ladder down 10-24 points the evening the API's report came out (the whole ladder moved) and one weekly transit
strike down 12 points on twice its open interest with the rest flat. A footprint, never a verdict: the text
says so. The false-alarm rate is to be measured over the coming weeks; above one a day the thresholds are to
be revisited and written down. Live since 2026-10-14 03:34 UTC (PR #15, 97d48fa4); checked on the data branch
and on GitHub: the first tick (03:35, 36 s) asked for `desk_intraday` and `kalshi` ("kalshi: hourly"), judged
the reading (296 markets, 2 flagged, 2 alerts written), read the trades of both busiest markets (20 and 24
trades, footprints on file), and the alerts job that follows the runner's first tick opened issues #16 and
#17 (`kalshi-kxeiacrudew-26oct14`, `kalshi-kxhormuzweekly-26oct18`); the public page shows the block with no
console error. Still NOT seen: an issue closed by the alerter when its alert ages out (due around the 16th), a
baseline (ten daily readings: the 24th), a day with no alert. The weekend: the runner stops on Friday evening,
so `kalshi-weekend.yml` (hourly cron, best effort: past weekends saw a scheduled run every three to seven
hours) runs `engine.cli watch` - the markets alone, downloaded when the last attempt is an hour old, archived,
judged, exported, the issues opened - only on the weekend and only when no runner is alive; a reading every
few hours is enough because each carries the API's "a day ago" fields, so a move is caught by any reading
within 24 hours of it. Not seen yet: its first scheduled weekend run (the 17th-18th).

Phase 14 (15 October, 03:20 UTC, on the owner's "create a highly speculative, leveraged strategy based on
Kalshi"; `docs/RESEARCH.md` chapter 10, written before the first decision). A Kalshi contract cannot be bought
on margin and the desk has no paper broker for event contracts, so the leverage is on oil and Kalshi gives the
direction: `oracle`, a candidate book on /MCL, long and short, vol target 0.50 (the hard rules' maximum),
ceilings 10x and 3x, breaker 15 %, `forecast: kalshi`. The forecast (`engine/desk/oracle.py`): the mean of
P(within 90 days) read off three deadline ladders - the strait back to normal, a US-Iran senior meeting, a
nuclear deal - each interpolated in time between the markets quoted within 10 cents, with (today, 0) as the
first point; the change over 7 days (the reference: the newest reading 7-10 days before), component by
component over the components both readings have; forecast = -2 per percentage point, capped +-20, zero under
one point; no reading within 3 days: flat; no reference yet: no decision. The decision of day D reads the
archive's reading of UTC day D (about 00:20 UTC, the evening before in New York) so the rule can be replayed on
the archive line by line. The prior written with the rule: no edge (oil leads these thin markets, not the
reverse). The book has no backtest, no row in the replay's tables, no part in the fingerprint
(`report.replayed_books`), and the lifecycle never promotes it (`lifecycle_verdict` now requires a backtest
drawdown for any promotion). The scale is to be re-measured after 60 readings (late December) and written in
chapter 10; the owner reviews the book after six months. Checked before shipping: the replay of the thirteen
replayable books on a copy of the live archive is byte-identical with main's code (bca25828) and with this
branch's - every curve, statistic, year, cost, ruin number, attribution row and portfolio - and the payloads
differ only in `excluded`, the `forecast` field of `books_config`, the lifecycle's rule text, and the
fingerprint (539b55e08479 -> 62d61be3e097, because `BookConfig` gained a field: the first tick after the deploy
recomputes the backtest once, "the rules changed", with the same numbers). Reviewed before shipping by an agent
that had not seen it written (`docs/RESEARCH.md` ch. 10 F): a component-less reading counted as the day's
reading and flattened the book; a one-sided quote passed for a probability; a line cut short in the archive
broke the export and the book's forecast on every tick (`StateStore.iter_jsonl(skip_bad=True)` for the Kalshi
logs, `_oracle_or_error` in the export); each book is now fed its own contracts and a flat book is marked on
the day's contract (`_symbols_to_feed(book=...)`, `_mark_symbol`); the "missing data" decision carries the
scales. Its simulators found spinto's decisions, fills and equity identical with and without the oracle. On
the archive's two readings (14 and 15 October) the index reads 21.5 % and 21.3 %: the first decision with a
reference is the 21st's. Site: a "Kalshi index · the Oracle book" block under the forecast, the "speculative ·
no backtest" tag on the book's rows, a row without numbers in the backtest tables. Tests:
`tests/test_desk_oracle.py` (fixture `tests/fixtures/kalshi_log_2026-10.jsonl`, the runner's own archive).

Phase 14 went live on 2026-10-15 at 05:12 UTC (PR #21, rebase-merged as 31e59c8d; CI green on the branch and on
main; Pages redeployed). Checked on GitHub and on the data branch, not assumed: the push cancelled the runner in
progress and started a new one; its first tick (05:13-05:17 UTC, 227 s) recomputed the backtest once
("computed: the rules changed", `meta.model` 539b55e08479 -> 62d61be3e097, 13 books, `excluded: [oracle]`,
spinto 0.68, `all` 1.23, `core_matched` 0.86 - the same numbers as before), took the oracle's decision of the
14th ("missing data: position unchanged", `risk_scale` 0.5 on the record, the note "no Kalshi reading 7 to 10
days ago: the book waits for a week of archive"), and left every other book exactly where the previous runner
had them (spinto's decision of the 14th had already been taken under the old code; 14 books, the candidates'
total 109 922.55 over eleven accounts). `desk.json` carries the `oracle` block (index 21.5 % on the reading of
the 14th, no forecast, archive of two days) and `backtest.excluded`; the public page renders the "Kalshi index
· the Oracle book" block, the tag on the book's row and the row without numbers in both backtest tables, with no
console error.

The archive's past (15 October, afternoon, on the owner's "it does not work: data is missing"; `docs/RESEARCH.md`
10 H). The statement "no history exists" was wrong: Kalshi serves each market's daily candlesticks since it
opened. The runner downloaded them (`research-data`, `kalshi-history`, 17:59 UTC: the strait's ladder since
18 March, the deal's since 9 January, the meeting's since 24 September; 36 markets, 2 921 daily candles; fixture
`tests/fixtures/kalshi_history_2026-10-15.json`), `engine/desk/backfill.py` turns them into the archive's
records and `kalshi-backfill.yml` writes the missing days onto the data branch (live readings win). The
registered rule, measured once on that history against the desk's WTI series (`scripts/research_oracle.py`,
143 sessions, 18 March - 12 October): Sharpe 1.19 with a standard error of 1.7, +27.7 % on 10 000 $ at whole
lots, one month (July) making the result; mean |weekly change| 6.3 points, mean |F| 9.84 - the declared scale
holds and is not touched; the daily index change correlates −0.21 with the NEXT day's oil return (t about
2.5), the weekly change +0.03 with the next week's: an observation made after looking, written down as a
candidate to register later, not a change to the book. Live since 18:21 UTC (PR #23, 190e9128): the
backfill job (18:31-18:32 UTC) wrote 508 records for 278 days (9 January - 13 October), skipped the 6 the tick
had read, and the archive has 280 daily readings. Checked on the data branch and on the page, not assumed: the
18:48 tick took the day's two MCL decisions - spinto "+4.9, 0.32x, less than a whole lot" and the oracle's
first with a reference, "the strait 6.6 % (a week ago 13.6 %), the meeting 45.6 % (48.5 %), the deal 11.7 %
(13.1 %); index 21.3 %, −3.8 points in a week: forecast +7.5 out of 20 ... exposure 0.49x, less than a whole
lot: no position" -, ran the first system's end of day (8 minutes, as on every day's first tick after the
London settlement), and the page shows the block with both columns and no console error. One thing seen: the
runner's first tick (18:32) started while the backfill job was pushing, so its push failed and the loop
recovered at 18:48 as designed ("push failed: retried on the next tick"; `sync` took the branch's state) - a
manual job that pushes the data branch costs the runner the tick it overlaps. Still NOT seen: the oracle's
first order and fill (the forecast must reach about 15 at the halved target and 38 % volatility for one lot),
a day on which its reading is a fallback (a weekend), the scale's re-measurement on the live record (late
December).

Phase 15 (15 October, evening, on the owner's "improve on the other markets, and speculative Kalshi as the
reference"; `docs/RESEARCH.md` chapter 11, registered before any measurement). The catalogue (15 011 series) was
searched by theme and the plausible series downloaded with their daily candlesticks by the runner (`research-data`,
`kalshi-history --series`, four downloads, ~60 series): what exists with a readable history is the FOMC meeting
markets (`KXFEDDECISION`: five outcomes per meeting, quoted at one or two cents on the near meetings; the API's
listing has forgotten the January-July 2026 meetings, so the history starts on 18 June, when September's meeting
entered the 90-day window) and the yearly recession markets (`KXRECSSNBER-26/-27`, one-cent spreads, since
January); the trade, chips, energy-policy, Ukraine, agriculture and Taiwan series have one market, a few weeks of
history or deadlines years away; price ladders and monthly data releases were excluded by construction. Two
indices were registered beside the de-escalation one (the Fed path in bp over the meetings within 90 days, the
recession probability within 365 days), four books (TLT: fedpath +, recession +; SPY: fedpath +, recession -; GLD:
fedpath +, deescalation +; XLU: fedpath +), long only, 2x, vol target 0.50, with the phase-12 filter (a negative
after-cost Sharpe does not start) and Carver's normalisation check written down first. Measured once
(`scripts/research_kalshi_books.py`, the candles through 14 October, the live archive winning, the funds' own
series): mean |F| 3.9 (fedpath) and 4.7 (recession) against the 10 the rule asks for - off by more than two, so
the scales went from 1 and 2 to 2.5 and 4 as declared (not neutral on a capped long-only book: utilities 2.53 ->
1.69; the verdicts do not change); equities +1.60 (SE 1.71, 197 sessions, April and May making the year) and
utilities +1.69 (SE 2.73, 82 sessions) start, bonds -1.53 and gold -1.66 do not (the same fedpath sleeve measures
-2.7 on Treasuries and +1.6/+1.9/+1.7 on equities, gold and utilities over the same days: a regime or a
coincidence, 82-197 sessions cannot tell; gold's fedpath sleeve alone would be +1.9, and the book stays out as
registered: no sleeve is dropped after looking). Reviewed before shipping by an agent that had not seen it
written (ch. 11 G), which found the one that mattered: the yearly recession ladder moved by itself (the end of
2026 dropping out, the 2027 market listing on 25 April: +20 for a week inside the measured history, on nothing)
- the change is now read on the years both readings quote, the reference's ladder re-read at today's date
(`oracle.common_values`); also fixed: the meeting day was excluded from the horizon against the registered text,
a 0.99/1.00 outcome would have made a certain meeting unreadable (`CERTAIN_BID`), the page crashed on the
engine's error block, the book rows said "de-escalation index" for every Kalshi book (they carry `sleeves`
now), the rationale named sleeves without a value, the backfill job summarised the de-escalation readings alone,
a failed settled listing cost the whole series, the alert would have judged the FOMC markets
(`watch.SCHEDULED_SERIES`), and the fingerprint had changed for `FORECAST_FAMILIES` in the hash, not for the
books (`report.NOT_REPLAYED`). Known and left as registered: the same deadline-passing mechanics in the phase-14
ladder (two points on 1 December), for the December review.
Engine: `oracle.py` as a registry (`IndexSpec`, `INDICES`, `ALL_SERIES`; the forecast keys are namespaced
`<index>:index`, `<index>:change`, `<index>:c:<component>`, `<index>:r:<component>`), `Book.combined_forecast`
as the signed mean, `live.oracle_forecasts` per sleeve, `export.oracle_block` with `indices` and `books` (the
first index's fields repeated at the top level), the tick's hourly download and the backfill job covering
`ALL_SERIES`, `prediction.GROUPS` with "Federal Reserve" and "US economy"; `fetch_history` merges the settled
listing. Tests: `tests/test_desk_kalshi_indices.py` (fixture `tests/fixtures/kalshi_history_2026-10-15_macro.json`,
the runner's own download trimmed; the Fed-path expectation and the recession ladder worked out by hand in the
test). Site: one table per index with the books and their signs. Checked before shipping: the replay of the
thirteen replayable books on a copy of the data branch is byte-identical with main's code (1000bfd5) and with
this branch's (spinto 0.68, `all` 1.23; the payload differs only in `excluded` and in the fingerprint, which
changes once, 62d61be3e097 -> b66ecbb82b75, because the hash of book.py's constants now leaves the Kalshi
registry out: the first tick after the deploy recomputes the backtest once, same numbers).

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
