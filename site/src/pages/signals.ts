/** Signals: what the books read, in one column of text.
 *
 * The desk page says what the books hold; this one says why. Top to bottom: what oil and the markets around it
 * are doing, the forecast the oil books read (seven sleeves from three sources), the Kalshi indices the
 * speculative books read, the allocator's scales, the options book's quotes, the prediction markets, the backtest
 * in one table, and whether the machine is alive. No number is computed here: the page formats what `desk.json`
 * carries and says "n/a" for what it does not.
 */
import { repoUrl } from "../config";
import { load } from "../data";
import {
  bookColor,
  portfolios,
  type DeskDoc,
  type ForecastBlock,
  type OptionUnderlying,
  type OracleBlock,
  type OracleIndex,
  type PredictionAlert,
  type PredictionEvent,
  type Quote,
} from "../desk";
import { MINUS, section, span, statusLine, toneSpan } from "../deskview";
import { EMPTY, clock, day, escapeHtml, leverage, num, percent, relative, signed, signedPercent, usd } from "../format";

// ---------------------------------------------------------------------------------------------- market
function quoteCell(q: Quote | undefined): string {
  if (!q) return "";
  const stale = q.asof ? (Date.now() - Date.parse(q.asof)) / 3_600_000 > 6 : true;
  return `<div class="t-quote">
    <div class="k">${escapeHtml(q.name)} <span class="t-dim">${escapeHtml(q.symbol)}</span></div>
    <div class="v">${q.price === null ? EMPTY : num(q.price, 2)} ${toneSpan(q.change, signedPercent(q.change, 1))}</div>
    <div class="s${stale ? " warn" : ""}">${q.asof ? escapeHtml(clock(q.asof)) : "no price"}</div>
  </div>`;
}

function market(doc: DeskDoc): string {
  const m = doc.market;
  const fc = doc.forecast.BNO;
  const cells = [quoteCell(m.brent), quoteCell(m.wti), quoteCell(m.bno)];
  if (m.ovx) {
    cells.push(`<div class="t-quote">
      <div class="k">OVX <span class="t-dim">implied vol</span></div>
      <div class="v">${num(m.ovx.value, 1)}</div>
      <div class="s">${escapeHtml(day(m.ovx.asof))}</div>
    </div>`);
  }
  for (const q of [m.copper, m.dollar]) {
    if (!q) continue;
    cells.push(`<div class="t-quote">
      <div class="k">${escapeHtml(q.name)}</div>
      <div class="v">${num(q.value, 2)}</div>
      <div class="s">close of ${escapeHtml(day(q.asof))}</div>
    </div>`);
  }
  if (fc && fc.slope !== null) {
    cells.push(`<div class="t-quote">
      <div class="k">Brent curve <span class="t-dim">${escapeHtml(fc.slope_pair)}${fc.slope_approx ? " ≈" : ""}</span></div>
      <div class="v">${signedPercent(fc.slope, 1)}<span class="t-dim">/year</span></div>
      <div class="s">${fc.slope > 0 ? "backwardation: the near month is worth more" : "contango: the far month is worth more"}</div>
    </div>`);
  }
  if (m.hormuz) {
    const h = m.hormuz;
    cells.push(`<div class="t-quote">
      <div class="k">Hormuz <span class="t-dim">tankers/day</span></div>
      <div class="v">${num(h.tankers_7d, 1)} ${span(h.closed ? "down" : "", `${percent(h.ratio, 0)} of normal`)}</div>
      <div class="s">7-day mean to ${escapeHtml(day(h.asof))} · normal ${num(h.baseline, 0)}</div>
    </div>`);
  }
  return section(
    "Market",
    `<div class="t-quotes">${cells.join("")}</div>`,
    "Yahoo prices, 10-15 minutes late; copper and dollar: daily closes; Hormuz: IMF PortWatch (AIS), weekly",
    "market",
  );
}

// ---------------------------------------------------------------------------------------------- forecast
function forecastTable(doc: DeskDoc): string {
  const cols: { key: string; title: string; f: ForecastBlock }[] = [];
  if (doc.forecast.BNO) cols.push({ key: "BNO", title: "Brent · BNO", f: doc.forecast.BNO });
  if (doc.forecast.MCL) cols.push({ key: "MCL", title: "WTI · /MCL", f: doc.forecast.MCL });
  if (!cols.length) return section("Forecast", `<div class="t-empty">No forecast: the daily series are missing.</div>`, "", "forecast");
  const row = (label: string, hint: string, pick: (f: ForecastBlock) => string, cls = ""): string =>
    `<tr${cls ? ` class="${cls}"` : ""}><th scope="row">${escapeHtml(label)}<small>${escapeHtml(hint)}</small></th>${cols
      .map((c) => `<td class="n">${pick(c.f)}</td>`)
      .join("")}</tr>`;
  const fc = (v: number | null | undefined): string => toneSpan(v ?? null, signed(v ?? null, 1));
  const source = (label: string, hint: string, pick: (f: ForecastBlock) => number | null | undefined): string =>
    row(label, hint, (f) => `<b>${fc(pick(f))}</b>`, "t-group");
  const sleeve = (label: string, hint: string, pick: (f: ForecastBlock) => number | null): string =>
    row(label, hint, (f) => fc(pick(f)), "t-sleeve");
  const macroDay = (f: ForecastBlock): string => {
    const days = [...new Set([f.macro_day?.copper, f.macro_day?.dollar].filter((d): d is string => !!d))];
    return days.length ? days.map((d) => escapeHtml(day(d))).join(" · ") : EMPTY;
  };
  const body = `<div class="t-scroll"><table class="t-table t-forecast">
    <thead><tr><th></th>${cols.map((c) => `<th class="n">${escapeHtml(c.title)}</th>`).join("")}</tr></thead>
    <tbody>
      ${source("Price of crude", "one third of the forecast", (f) => f.sources?.prezzo)}
      ${sleeve("Trend", "four moving-average pairs, 8/32 to 64/256 days", (f) => f.trend)}
      ${sleeve("Acceleration", "today's trend minus 16, 32 and 64 days ago", (f) => f.accel)}
      ${sleeve("Skew", "rare large falls more than usual: holders get paid · read on WTI", (f) => f.skew)}
      ${source("Futures curve", "one third", (f) => f.sources?.curva)}
      ${sleeve("Carry", "+10 in backwardation, −10 in contango", (f) => f.carry)}
      ${sleeve("Carry momentum", "slope above or below its 20-day mean", (f) => f.carry_momentum)}
      ${source("Other markets", "one third · read at the previous close", (f) => f.sources?.macro)}
      ${sleeve("Copper", "copper trend: the global cycle", (f) => f.copper)}
      ${sleeve("Dollar", "dollar trend, sign inverted", (f) => f.dollar)}
      <tr class="t-total"><th scope="row">Forecast<small>sources averaged × 1.75 · ${MINUS}20 to +20; +10 is a normal conviction</small></th>${cols
        .map(
          (c) =>
            `<td class="n"><b>${fc(c.f.combined)}</b> <span class="t-dim">yesterday ${signed(c.f.combined_prev, 1)}</span></td>`,
        )
        .join("")}</tr>
      ${row("Volatility", "annualised, last two months", (f) => percent(f.vol, 0))}
      ${row("Data as of", "last session in the calculation", (f) => `${escapeHtml(day(f.day))}${f.approx ? " ≈" : ""}`)}
      ${row("Copper and dollar as of", "the previous session's close", macroDay)}
    </tbody></table></div>`;
  return section("Forecast", body, "the same for every book on that instrument: only the size differs", "forecast");
}

// ---------------------------------------------------------------------------------------------- oracle
/** The prediction markets' indices the speculative books read (phases 14 and 15): separate forecasts, never
 *  mixed with the seven sleeves above. One table per index: its components today and a week ago, the index, the
 *  sleeve it makes and the books that read it with their sign. */
function oracleTable(doc: DeskDoc): string {
  const o: OracleBlock | null | undefined = doc.oracle;
  if (!o) return "";
  if (o.error) {
    // the engine could not read the archive: the block says so instead of costing the page (found in review)
    const names = (o.books ?? []).map((b) => escapeHtml(typeof b === "string" ? b : b.id)).join(", ");
    return section(
      "Kalshi indices and the Oracle books",
      `<p class="warn">The Kalshi archive could not be read on this export: ${escapeHtml(String(o.error))}.</p><p class="t-dim">The books on it (${names}) took no new decision on it; their positions and marks are on <a href="#/">the desk</a>.</p>`,
      "the crowd's probabilities, read at a constant horizon",
      "kalshi",
    );
  }
  const indices: OracleIndex[] = o.indices && o.indices.length ? o.indices : [{ ...o, books: undefined }];
  const archive = o.archive_days
    ? `${o.archive_days} daily reading${o.archive_days === 1 ? "" : "s"} since ${escapeHtml(day(o.archive_first_day))}${
        o.archive_backfilled_days
          ? `, ${o.archive_backfilled_days} of them filled from Kalshi's daily candlesticks (the close at midnight New York) for the days before the desk read these markets itself`
          : ""
      }`
    : "no reading archived yet";
  const bookList = (o.books as { id: string; name?: string; vehicle?: string; sleeves?: Record<string, number> }[] | string[])
    .map((b) =>
      typeof b === "string"
        ? escapeHtml(b)
        : `${escapeHtml(b.id)}${b.vehicle ? ` on ${escapeHtml(b.vehicle)}` : ""}${
            b.sleeves
              ? ` (${Object.entries(b.sleeves)
                  .map(([k, v]) => `${k} ${v < 0 ? MINUS : "+"}`)
                  .join(", ")})`
              : ""
          }`,
    )
    .join("; ");
  const tables = indices.map(oracleIndexTable).join("");
  const body = `${tables}
    <p class="t-dim">Books: ${bookList}. Archive: ${archive}. ${o.disclaimer ? escapeHtml(o.disclaimer) : ""} <span class="warn">The prior written with every rule is that it has no edge.</span></p>`;
  return section(
    "Kalshi indices and the Oracle books",
    body,
    "the crowd's probabilities on de-escalation, the Fed's path and a recession, read at a constant horizon; a sleeve each, read with a sign per book",
    "kalshi",
  );
}

function oracleIndexTable(o: OracleIndex): string {
  const bp = o.unit === "bp";
  const val = (v: number | null | undefined): string =>
    v === null || v === undefined ? EMPTY : bp ? `${signed(v, 0)} bp` : percent(v, 1);
  const horizon = bp ? `decided within ${o.rule.horizon_days} days` : `within ${o.rule.horizon_days} days`;
  const rows = o.components
    .map(
      (c) => `<tr class="t-sleeve"><th scope="row">${escapeHtml(c.label)}<small>${escapeHtml(c.series)} · ${horizon}</small></th><td class="n">${val(
        c.p,
      )}</td><td class="n">${val(c.p_reference)}</td></tr>`,
    )
    .join("");
  const partial =
    o.components_common !== undefined && o.components_read !== undefined && o.components_common < o.components_read
      ? ` (over ${o.components_common} of ${o.components_read} components)`
      : "";
  const change =
    o.change === null || o.change === undefined
      ? EMPTY
      : bp
        ? `${signed(o.change, 1)} bp${partial}`
        : `${signed(o.change * 100, 1)} points${partial}`;
  const forecast =
    o.forecast === null || o.forecast === undefined
      ? span("t-dim", "no value yet")
      : `<b>${toneSpan(o.forecast, signed(o.forecast, 1))}</b> out of 20`;
  const per = o.rule.per_unit ?? (o.rule.forecast_per_point === null || o.rule.forecast_per_point === undefined ? null : -o.rule.forecast_per_point);
  const scale =
    per === null || per === undefined
      ? ""
      : `${per < 0 ? MINUS : "+"}${num(Math.abs(per), 1).replace(/\.0$/, "")} per ${bp ? "basis point" : "point"} of weekly change`;
  const meaning = bp
    ? "turning dovish = positive"
    : o.id === "recession"
      ? "odds of a recession rising = positive"
      : "rising de-escalation = short oil";
  const aggregate =
    o.rule.aggregate === "sum"
      ? "the sum over the meetings"
      : o.components.length === 1
        ? "one component"
        : `the mean of the ${o.components.length || "components"}`;
  const readers = (o.books ?? [])
    .map((b) => `${escapeHtml(b.id)} ${b.sign < 0 ? MINUS : "+"}`)
    .join(", ");
  const label = o.label.charAt(0).toUpperCase() + o.label.slice(1);
  return `<div class="t-scroll"><table class="t-table t-forecast">
    <thead><tr><th>${escapeHtml(label)}</th><th class="n">Now · ${escapeHtml(day(o.reading_day))}</th><th class="n">Week ago · ${escapeHtml(
      day(o.reference_day),
    )}</th></tr></thead>
    <tbody>
      ${rows}
      <tr class="t-group"><th scope="row">${escapeHtml(label)}<small>${aggregate}</small></th><td class="n"><b>${val(
        o.index,
      )}</b></td><td class="n"><b>${val(o.index_reference)}</b></td></tr>
      <tr class="t-total"><th scope="row">Sleeve<small>${scale} · ${meaning} · ${MINUS}20 to +20${readers ? ` · read by ${readers}` : ""}</small></th>
        <td class="n">${forecast}</td><td class="n">${escapeHtml(change)}</td></tr>
    </tbody></table></div>
    <p>${escapeHtml(o.note.charAt(0).toUpperCase() + o.note.slice(1))}.</p>
    <p class="t-dim">${escapeHtml(o.rule.text)}</p>`;
}

// ---------------------------------------------------------------------------------------------- allocator
function allocator(doc: DeskDoc): string {
  const a = doc.allocator;
  if (!a) return "";
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  const chip = (on: boolean): string => `<span class="t-dot ${on ? "ok" : "dim"}">${on ? "on" : "off"}</span>`;
  const runup = Object.entries(a.runup)
    .sort(([x], [y]) => x.localeCompare(y))
    .map(([v, r]) =>
      r.active
        ? `<b>${escapeHtml(v)}</b> on (last held ${escapeHtml(day(r.last_run_up))})`
        : `${escapeHtml(v)} off${r.last_run_up ? ` <span class="t-dim">(last ${escapeHtml(day(r.last_run_up))})</span>` : ""}`,
    )
    .join(" · ");
  const h = a.hormuz;
  const strait =
    h.transit_ratio === null || h.transit_ratio === undefined
      ? "no transit reading recent enough: ceilings unchanged"
      : `transits at ${percent(h.transit_ratio, 0)} of normal → ${
          h.closed ? `weekend ceilings × ${num(h.weekend_scale, 1)}` : "ceilings unchanged"
        }`;
  const live = doc.books
    .filter((b) => {
      const months = b.lifecycle?.months_live;
      return months !== null && months !== undefined && months >= 1; // under a month there is nothing to read
    })
    .map((b) => {
      const l = b.lifecycle!;
      return `${escapeHtml(b.name)} ${num(l.months_live, 1)} months${
        l.sharpe_live === null || l.sharpe_live === undefined ? "" : `, Sharpe ${signed(l.sharpe_live, 2)}`
      }`;
    });
  const changes = Object.entries(a.role_changes).map(
    ([id, c]) => `<b>${escapeHtml(names.get(id) ?? id)}</b> → ${escapeHtml(c.role)} since ${escapeHtml(day(c.since))} (${escapeHtml(c.reason)})`,
  );
  const scaled = doc.books
    .filter((b) => b.allocator && b.allocator.risk !== null && b.allocator.risk !== undefined && b.allocator.risk < 1)
    .map((b) => `${escapeHtml(b.name)} ×${num(b.allocator!.risk, 2).replace(/\.?0+$/, "")}`);
  const row = (name: string, key: string, today: string): string => {
    const r = a.rules[key];
    if (!r) return "";
    return `<tr><th scope="row">${escapeHtml(name)}</th><td>${chip(r.enabled)}</td>
      <td class="w">${escapeHtml(r.rule)}</td><td class="w">${today}</td></tr>`;
  };
  const body = `<div class="t-scroll"><table class="t-table t-log">
    <thead><tr><th>Rule</th><th></th><th>What it does</th><th>Today</th></tr></thead>
    <tbody>
      ${row("Bubble switch", "runup", `${runup || EMPTY}<br><span class="t-dim">a book on a vehicle that is "on" runs at half its volatility target</span>`)}
      ${row("Strait of Hormuz", "hormuz", strait)}
      ${row(
        "Drawdown control",
        "drawdown_control",
        `<span class="t-dim">measured and not adopted: it cut the worst loss by a third and the Sharpe by 0.05-0.14; a lower volatility target does the first without the second</span>`,
      )}
      ${row("Lifecycle", "lifecycle", `${live.length ? live.join(" · ") : '<span class="t-dim">no book has a month of live record yet</span>'}${
        changes.length ? `<br>${changes.join("<br>")}` : ""
      }`)}
    </tbody></table></div>
    <p class="t-dim">Scaled today: ${scaled.length ? scaled.join(" · ") : "no book"} · a book's size is
    <code>forecast / 10 × volatility target × m / volatility</code>, where m is the allocator's multiplier; nothing here ever
    raises a position above the book's own rules. Every rule was measured on the replay before it was switched on (and
    the backtest page shows the books with and without it).</p>`;
  return section("Allocator", body, "how the desk recalibrates itself, by published rules", "allocator");
}

// ---------------------------------------------------------------------------------------------- prediction markets
function pct(p: number | null | undefined): string {
  return p === null || p === undefined || !Number.isFinite(p) ? EMPTY : `${num(100 * p, 0)}%`;
}

function predictionLadder(e: PredictionEvent): string {
  if (e.kind === "deadline") {
    const rows = (e.ladder ?? [])
      .map((r) => `<tr><td>before ${escapeHtml(day(r.before))} ${escapeHtml(r.before.slice(0, 4))}</td><td class="n"><b>${pct(r.p)}</b></td></tr>`)
      .join("");
    return rows ? `<div class="t-scroll"><table class="t-table t-log"><tbody>${rows}</tbody></table></div>` : "";
  }
  const withOptions = e.markets.some((m) => m.options_p_above !== undefined && m.options_p_above !== null);
  const change = (m: PredictionEvent["markets"][number]): string => {
    const c = m.change_24h;
    if (c === null || c === undefined) return "";
    const pts = Math.round(100 * c);
    return pts === 0 ? span("t-dim", "0") : toneSpan(c, `${pts > 0 ? "+" : MINUS}${Math.abs(pts)}`);
  };
  const rows = e.markets
    .filter((m) => m.mid !== null && m.mid !== undefined)
    .map(
      (m) => `<tr><td>${escapeHtml(m.label)}</td>
        <td class="n"><b>${pct(m.mid)}</b><small>${num(m.yes_bid, 2)}–${num(m.yes_ask, 2)}</small></td>
        <td class="n">${change(m)}</td>
        ${withOptions ? `<td class="n">${m.options_p_above === undefined ? "" : `≈ ${pct(m.options_p_above)}`}</td>` : ""}
        <td class="n t-dim">${m.volume_24h === null || m.volume_24h === undefined ? "" : num(m.volume_24h, 0)}</td>
        <td class="n t-dim">${m.volume === null || m.volume === undefined ? "" : num(m.volume, 0)}</td></tr>`,
    )
    .join("");
  return `<div class="t-scroll"><table class="t-table t-log">
    <thead><tr><th>Market</th><th class="n">Crowd</th><th class="n">24h</th>${withOptions ? '<th class="n">Options ≈</th>' : ""}<th class="n">Vol 24h</th><th class="n">Volume</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function predictionHeadline(e: PredictionEvent): string {
  if (e.kind === "price") {
    const ref = e.reference;
    const im = e.implied;
    const opt = e.options_implied;
    const parts: string[] = [];
    if (im?.median !== null && im?.median !== undefined) parts.push(`the crowd's median <b>${num(im.median, 2)} $</b>`);
    if (ref && ref.price !== null && ref.price !== undefined) {
      const p = im?.p_above_reference;
      const bound = im?.p_above_reference_bound;
      parts.push(
        `${escapeHtml(ref.symbol ?? "")} now ${num(ref.price, 2)} $${
          p !== null && p !== undefined ? ` → P(above it) <b>${pct(p)}</b>` : bound ? ` → P(above it) ${escapeHtml(bound)}` : ""
        }`,
      );
    }
    if (opt?.median !== null && opt?.median !== undefined) {
      parts.push(`≈ ${escapeHtml(opt.symbol ?? "")} options' median ${num(opt.median, 2)} $ (lognormal at ${percent(opt.atm_iv ?? null, 0)} IV)`);
    } else if (opt?.stale) {
      parts.push(`<span class="t-dim">options' twin not shown: the chain is ${num(opt.quote_age_hours ?? null, 0)} hours old</span>`);
    }
    return `${escapeHtml(e.label)} on ${escapeHtml(day(e.close_time))} ${escapeHtml(e.close_time?.slice(0, 4) ?? "")}: ${parts.join(" · ")}`;
  }
  if (e.kind === "deadline") {
    const steps = (e.ladder ?? []).map((r) => `${escapeHtml(day(r.before))} ${escapeHtml(r.before.slice(0, 4))} <b>${pct(r.p)}</b>`);
    return steps.length ? `before ${steps.join(" · ")}` : escapeHtml(e.summary);
  }
  if (e.kind === "threshold") {
    const m = e.implied?.median;
    return m === null || m === undefined ? escapeHtml(e.summary) : `the crowd's median <b>${num(m, 1)}</b>`;
  }
  return escapeHtml(e.summary);
}

function measuredLine(e: PredictionEvent): string {
  const m = e.measured;
  if (!m) return "";
  if ("tankers_7d" in m) {
    const ratio = m.ratio as number | null;
    return `<p class="t-dim">The desk's own reading, IMF PortWatch to ${escapeHtml(day(m.asof as string))}: ${num(
      m.tankers_7d as number,
      1,
    )} tankers a day, ${ratio === null ? EMPTY : percent(ratio, 0)} of normal. The market counts every transit call against its own threshold; the two are not the same number.</p>`;
  }
  if ("crude_stocks_mmbbl" in m) {
    return `<p class="t-dim">Last EIA figure: ${num(m.crude_stocks_mmbbl as number, 1)} million barrels, week ending ${escapeHtml(
      day(m.week_ending as string),
    )}.</p>`;
  }
  return "";
}

function alertLine(a: PredictionAlert): string {
  const strikes = a.markets
    .slice(0, 4)
    .map(
      (m) =>
        `${escapeHtml(m.label)} ${pct(m.mid_24h_ago)} → <b>${pct(m.mid)}</b> ${toneSpan(
          m.change_24h,
          `(${m.change_24h !== null && m.change_24h !== undefined && m.change_24h > 0 ? "+" : MINUS}${Math.abs(Math.round(100 * (m.change_24h ?? 0)))})`,
        )}`,
    )
    .join(" · ");
  const more = a.markets.length > 4 ? ` · and ${a.markets.length - 4} more` : "";
  const busiest = a.markets[0];
  const fp = a.footprint;
  const trades = fp && fp.trades
    ? `Trades on ${escapeHtml(a.footprint_ticker ?? busiest.ticker)}: ${fp.trades} trades, ${num(fp.contracts ?? null, 0)} contracts; the ${fp.top_trades ?? 5} largest carry ${pct(fp.top_share)}; ${pct(fp.side_share)} with the taker on ${escapeHtml((fp.side ?? "").toUpperCase())}${
        fp.largest ? `; the largest ${num(fp.largest.contracts, 0)} at ${num(fp.largest.yes_price, 2)}, ${escapeHtml(clock(fp.largest.at))}` : ""
      }${fp.block_trades ? `; ${fp.block_trades} block trades` : ""}.`
    : a.footprint_note
      ? `<span class="t-dim">${escapeHtml(a.footprint_note)}.</span>`
      : "";
  const scope = a.rest_flat
    ? span("warn", "the rest of the ladder is flat")
    : a.rest_of_ladder_move !== null && a.rest_of_ladder_move !== undefined
      ? "the whole ladder moved too"
      : "no other market to compare with";
  return `<div class="t-alert">
    <div><span class="t-dot warn">unusual activity</span> <b>${escapeHtml(a.group)}</b> · ${escapeHtml(a.title)} <span class="t-dim">${escapeHtml(a.event)} · read ${escapeHtml(clock(a.ts))}</span></div>
    <div>${a.markets.length} of ${a.n_markets} markets: ${strikes}${more} — on ${escapeHtml(busiest.volume)}; ${scope}.</div>
    ${trades ? `<div class="t-dim">${trades}</div>` : ""}
  </div>`;
}

function predictionAlerts(doc: DeskDoc): string {
  const pm = doc.prediction_markets;
  if (!pm || !pm.watch || !Object.keys(pm.watch).length) return "";
  const alerts = pm.alerts ?? [];
  const body = alerts.length
    ? alerts.map(alertLine).join("")
    : `<p class="t-dim">Nothing unusual in the last seven days of readings: no event market moved ${escapeHtml(
        pm.watch.move ?? "10% in 24 hours",
      )} on a day's volume far above its own usual.</p>`;
  return `<div class="t-watch" id="alerts">
    <h3 class="t-sub">Unusual activity <span class="t-dim">${alerts.length ? `${alerts.length} in the last week` : "none in the last week"}</span></h3>
    ${body}
    <p class="t-dim">The rule, declared before it went live: ${escapeHtml(pm.watch.move ?? "")}; ${escapeHtml(
      pm.watch.volume ?? "",
    )}; ${escapeHtml(pm.watch.scope ?? "")}. ${escapeHtml(pm.watch.meaning ?? "")}. The alert also opens a GitHub issue for two days.</p>
  </div>`;
}

function predictionMarkets(doc: DeskDoc): string {
  const pm = doc.prediction_markets;
  if (!pm) return "";
  if (!pm.asof) {
    return section(
      "Prediction markets",
      `<div class="t-empty">No reading from Kalshi yet: the tick asks for it every hour.</div>`,
      "",
      "prediction",
    );
  }
  const events = pm.groups.flatMap((g) => g.events);
  const blocks = events
    .map(
      (e) => `<details class="t-details">
        <summary><b>${escapeHtml(e.label)}</b> <span class="t-dim">${escapeHtml(e.series)} · closes ${escapeHtml(
          day(e.close_time),
        )} ${escapeHtml(e.close_time?.slice(0, 4) ?? "")}</span><br><span class="t-dim">${escapeHtml(e.title)}</span><br>${predictionHeadline(e)}</summary>
        ${predictionLadder(e)}
        ${measuredLine(e)}
        ${e.settles_on ? `<p class="t-dim">Settles on ${escapeHtml(e.settles_on)}: part of the gap between the crowd's median and the reference is a basis, not an expectation.</p>` : ""}
        ${
          e.kind === "price" && e.options_implied?.approx
            ? `<p class="t-dim">≈ ${escapeHtml(e.options_implied.note ?? "")}.</p>`
            : ""
        }
      </details>`,
    )
    .join("");
  const foot: string[] = [];
  if (pm.past_close.length) {
    foot.push(
      `Still "active" past their close time, not settled and not counted: ${pm.past_close
        .map((p) => `${escapeHtml(p.series)} (${p.markets} markets, closed ${escapeHtml(day(p.close_time))})`)
        .join(", ")}`,
    );
  }
  if (pm.empty_series.length) foot.push(`No open market in ${pm.empty_series.map(escapeHtml).join(", ")}`);
  const body = `${predictionAlerts(doc)}
    ${events.length ? blocks : `<div class="t-empty">The reading holds no open market in the series asked for.</div>`}
    <p class="t-dim">${escapeHtml(pm.note)} "24h" is the move since the quote a day ago, in points. A price ladder's "crowd median" is where the ladder crosses one half; "≈" marks the
    options' twin, a driftless lognormal at the ATM implied volatility of the fund's options with no smile and no basis
    between the fund and the future.${foot.length ? ` ${foot.join(". ")}.` : ""}</p>`;
  return section(
    "Prediction markets",
    body,
    `Kalshi, read ${escapeHtml(clock(pm.asof))}${pm.stale ? ' <span class="warn">stale</span>' : ""}; ${pm.markets ?? 0} open markets; the price of a contract paying 1 $ is the crowd's probability`,
    "prediction",
  );
}

// ---------------------------------------------------------------------------------------------- options
function optionRow(u: OptionUnderlying): string {
  const noChain = u.price === undefined || u.price === null;
  if (noChain) {
    return `<tr><th scope="row">${escapeHtml(u.symbol)}<small>${escapeHtml(u.label)}</small></th>
      <td colspan="7" class="t-dim">no option chain archived</td></tr>`;
  }
  return `<tr>
    <th scope="row">${escapeHtml(u.symbol)}<small>${escapeHtml(u.label)}</small></th>
    <td class="n">${num(u.price, 2)}</td>
    <td class="n">${percent(u.atm_iv ?? u.iv30 ?? null, 0)}</td>
    <td class="n">${percent(u.realized_vol, 0)}</td>
    <td class="n">${u.straddle_pct === null || u.straddle_pct === undefined ? EMPTY : `±${percent(u.straddle_pct, 1)}`}<small>by ${escapeHtml(
      day(u.expiry),
    )}</small></td>
    <td class="n">${span(u.tradeable ? "" : "down", percent(u.median_rel_spread ?? null, 0))}<small>${
      u.tradeable ? "tradeable" : "too wide"
    }</small></td>
    <td class="n">${toneSpan(u.forecast, signed(u.forecast, 1))}<small>threshold +${num(u.threshold, 0)}</small></td>
    <td class="w">${u.gate.open ? span("up", "all conditions met") : escapeHtml(u.gate.reason)}</td>
  </tr>`;
}

function candidateLine(u: OptionUnderlying, open: boolean): string {
  const c = u.candidate;
  if (u.price === undefined || u.price === null) return "";
  if (!c) {
    return u.no_candidate_reason
      ? `<p class="t-dim">${escapeHtml(u.symbol)}: no tradeable structure ${open ? "right now" : "on the session's last quotes"}. ${escapeHtml(
          u.no_candidate_reason,
        )}.</p>`
      : "";
  }
  const lead = open ? "what the book would sell now:" : "market closed; on the session's last quotes the structure would be:";
  return `<p><span class="t-dim">${escapeHtml(u.symbol)}, ${lead}</span> put ${num(c.short.strike, 0)}/${num(c.long.strike, 0)} expiring ${escapeHtml(
    day(c.expiry),
  )} (${c.dte} days) · credit ${num(c.credit, 2)} $ per share (${num(c.credit_mid, 2)} at the mid) · max loss ${usd(
    c.max_loss_per_contract,
    0,
  )} · breakeven ${num(c.breakeven, 2)}, ${percent(c.breakeven / c.underlying_price - 1, 1)} from the price.</p>`;
}

function options(doc: DeskDoc): string {
  const book = doc.books.find((b) => b.kind === "options");
  const mon = book?.monitor;
  if (!book || !mon || !mon.underlyings.some((u) => u.price !== undefined && u.price !== null)) {
    return section(
      "Options",
      `<div class="t-empty">The options book has not read a chain yet: quotes are downloaded in New York market hours and the
      decision is taken once a day after 15:00.</div>`,
      "",
      "options",
    );
  }
  const body = `<div class="t-scroll"><table class="t-table t-options">
    <thead><tr><th>Underlying</th><th class="n">Price</th><th class="n">Implied vol</th><th class="n">Realised vol</th>
      <th class="n">Priced move</th><th class="n">Bid-ask</th><th class="n">Forecast</th><th>Today</th></tr></thead>
    <tbody>${mon.underlyings.map(optionRow).join("")}</tbody></table></div>
    ${mon.underlyings.map((u) => candidateLine(u, mon.session_open !== false)).join("")}
    <p class="t-dim">"Priced move" is the cost of a call plus a put at the money: how far the fund must move, up or down, for
    buying both to break even. Since 2007 that price has on average exceeded the move that followed: buying both ways
    lost, and the book does not do it.</p>`;
  return section("Options", body, `Cboe quotes, 15 minutes late; read ${escapeHtml(clock(mon.generated_at))}`, "options");
}

// ---------------------------------------------------------------------------------------------- backtest
function backtest(doc: DeskDoc): string {
  const bt = doc.backtest;
  if (!bt) {
    return section("Backtest", `<div class="t-empty">The backtest is computed on the first tick that has the data, then weekly: not published yet.</div>`, "", "backtest");
  }
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  const roles = new Map(doc.books.map((b) => [b.id, b.role ?? "core"]));
  const order = new Map(doc.books.map((b, i) => [b.id, i]));
  const entries = Object.entries(bt.books).sort(([a], [b]) => (order.get(a) ?? 99) - (order.get(b) ?? 99));
  const rowOf = ([id, b]: (typeof entries)[number]): string => {
    const s = b.stats;
    return `<tr>
      <th scope="row"><span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}<small>${escapeHtml(
        b.vehicle,
      )} · ${num(s.years ?? null, 0)} years</small></th>
      <td class="n">${signedPercent(s.cagr ?? null, 1)}</td>
      <td class="n">${percent(s.vol ?? null, 0)}</td>
      <td class="n">${num(s.sharpe ?? null, 2)}<small>t ${num(s.t_stat ?? null, 1)}</small></td>
      <td class="n down">${percent(s.max_drawdown ?? null, 0)}</td>
      <td class="n">${leverage(s.leverage_p95 ?? null)}<small>max ${leverage(s.leverage_max ?? null)}</small></td>
      <td class="n">${percent(b.ruin?.p_lose_half ?? null, 1)}</td>
      <td class="n">${num(b.double_cost_sharpe, 2)}</td>
    </tr>`;
  };
  const coreRows = entries.filter(([id]) => roles.get(id) !== "candidate").map(rowOf).join("");
  const candRows = entries.filter(([id]) => roles.get(id) === "candidate").map(rowOf).join("");
  const together = portfolios(bt.portfolio)
    .map(([, label, p]) => {
      const s = p.stats;
      return `<tr class="t-total">
        <th scope="row">${escapeHtml(label)}<small>equal capital, ${escapeHtml(p.start.slice(0, 4))}–${escapeHtml(
          p.end.slice(0, 4),
        )}</small></th>
        <td class="n">${signedPercent(s.cagr ?? null, 1)}</td><td class="n">${percent(s.vol ?? null, 0)}</td>
        <td class="n"><b>${num(s.sharpe ?? null, 2)}</b><small>t ${num(s.t_stat ?? null, 1)}</small></td>
        <td class="n down">${percent(s.max_drawdown ?? null, 0)}</td><td></td><td></td><td></td>
      </tr>`;
    })
    .join("");
  const bench = Object.entries(bt.benchmarks)
    .map(([id, b]) => {
      const s = b.stats;
      return `<tr class="t-bench">
        <th scope="row">${escapeHtml(id)} bought and held<small>${num(s.years ?? null, 0)} years, no leverage</small></th>
        <td class="n">${signedPercent(s.cagr ?? null, 1)}</td><td class="n">${percent(s.vol ?? null, 0)}</td>
        <td class="n">${num(s.sharpe ?? null, 2)}<small>t ${num(s.t_stat ?? null, 1)}</small></td>
        <td class="n down">${percent(s.max_drawdown ?? null, 0)}</td><td class="n">1.00x</td><td></td><td></td>
      </tr>`;
    })
    .join("");
  const opt = bt.options
    ? `<tr class="t-bench">
        <th scope="row">≈ Options<small>a model, not a history of quotes</small></th>
        <td class="n">${signedPercent(bt.options.stats.cagr ?? null, 1)}</td><td class="n">${percent(bt.options.stats.vol ?? null, 0)}</td>
        <td class="n">${
          bt.options.sharpe_range ? `${num(bt.options.sharpe_range[0], 1)}–${num(bt.options.sharpe_range[1], 1)}` : EMPTY
        }<small>by smile and costs</small></td>
        <td class="n down">${percent(bt.options.stats.max_drawdown ?? null, 0)}</td><td></td><td></td><td></td>
      </tr>`
    : "";
  const excluded = (bt.excluded ?? [])
    .map(
      (x) => `<tr class="t-bench">
        <th scope="row"><span class="t-key" style="--sw:${bookColor(x.id)}"></span>${escapeHtml(names.get(x.id) ?? x.name)}<small>${escapeHtml(
          x.vehicle,
        )} · speculative</small></th>
        <td colspan="7" class="t-dim w">No backtest: ${escapeHtml(x.reason)}.</td>
      </tr>`,
    )
    .join("");
  const body = `<div class="t-scroll"><table class="t-table t-backtest t-summary">
    <thead><tr><th>Book</th><th class="n">Yearly return</th><th class="n">Volatility</th><th class="n">Sharpe</th>
      <th class="n">Max drawdown</th><th class="n">Leverage 95th</th><th class="n">P(${MINUS}50% in a year)</th>
      <th class="n">Sharpe at 2× costs</th></tr></thead>
    <tbody>${coreRows}${opt}${candRows || excluded ? `<tr class="t-break"><th colspan="8" class="t-dim w">Candidates</th></tr>${candRows}${excluded}` : ""}${together}${bench}</tbody></table></div>
    <p class="t-dim">Real costs, ${escapeHtml(bt.fill_rule ?? "")}. The "t" under each Sharpe is how many standard errors it sits
    from zero: above 2 is serious evidence, not certainty. Part of the result is selection among the signals tried, and
    2023, 2024 and 2025 were all negative for the oil books. <a href="#/backtest">Year by year, components and sensitivity →</a></p>`;
  return section("Backtest", body, `computed ${escapeHtml(relative(bt.generated_at))}`, "backtest");
}

// ---------------------------------------------------------------------------------------------- health
function health(doc: DeskDoc): string {
  const h = doc.health;
  const bad = h.not_green
    .map(
      (s) => `<tr><td class="t-nowrap"><span class="t-dot ${s.status === "red" ? "bad" : "warn"}">${escapeHtml(s.source)}</span></td>
        <td class="w t-dim">${escapeHtml(s.message)}</td></tr>`,
    )
    .join("");
  const tick = h.last_tick
    ? `Last tick ${escapeHtml(clock(h.last_tick.finished))}: ${escapeHtml(h.last_tick.message)}. `
    : "No tick recorded. ";
  const notes = [...doc.data_notes, ...doc.data_missing.map((m) => `missing table: ${m}`)];
  const body = `<p>${tick}${h.ticks_24h === 1 ? "One tick" : `${h.ticks_24h} ticks`} in the last 24 hours${
    h.failed_24h ? `, ${h.failed_24h} failed` : ""
  }. The engine runs every 30 minutes from Sunday evening to Friday evening (New York time).</p>
    ${bad ? `<div class="t-scroll"><table class="t-table t-log t-stack"><tbody>${bad}</tbody></table></div>` : ""}
    ${notes.length ? `<p class="warn">${escapeHtml(notes.join(" · "))}</p>` : ""}
    <p class="t-dim">Only a book's own daily prices can stop its new decisions. Any other source, when missing, silences
    what reads it and nothing else.</p>`;
  return section("Status", body, `${h.n_green} of ${h.n_sources} sources fine`, "status");
}

function howTo(): string {
  return `<details class="t-details">
    <summary><b>How to read this</b> <span class="t-dim">rules, costs, limits</span></summary>
    <p><b>One forecast, four books.</b> Seven signals from three sources that weigh a third each - the price of crude
    (trend, acceleration, skew), the futures curve (carry, carry momentum) and other markets (copper and the dollar,
    read at the previous close) - give a number between ${MINUS}20 and +20. Each linear book buys
    <code>forecast / 10 × volatility target / today's volatility</code> times its capital, up to its cap. Leverage is
    not chosen: it falls out of that ratio. When crude is very volatile even the 10x book stays around 1x; leverage rises
    only when the market calms down and the forecast is strong. An <b>inertia band</b> keeps costs down: while the size
    wanted stays within 10% of a normal position nothing is traded, and when it leaves the band the position moves to
    the edge of the band, not to the middle.</p>
    <p><b>Instruments that exist on Robinhood.</b> Brent is bought through the BNO fund, unlevered or up to 2x on margin.
    Real leverage goes through the micro WTI future (/MCL, 100 barrels), because there is no Brent future there: the
    "Aggressive" book therefore also carries the risk that Brent and WTI move apart. A fund cannot be sold short: when
    its forecast is negative the "Dynamic" book buys SCO, a fund that returns ${MINUS}2 times WTI each day, pays for it
    in cash, holds at most the account's worth of it, and the leverage shown is the oil exposure (twice the dollars).</p>
    <p><b>Candidates.</b> The same rules on other funds, each a separate paper account, forecast on its own price only:
    gold, copper and agriculture (phase 12: geopolitics, electricity demand, weather), then Treasuries and equities and
    five themes of the electricity and data-centre build-out - uranium, utilities, lithium, semiconductors, clean energy
    (phase 13). Nearly uncorrelated with the oil books; each tested once before it started (gold 0.50, copper 0.26,
    agriculture 0.34, Treasuries and equities about 0.3-0.4, uranium 0.35, utilities 0.43, lithium 0.67, semiconductors
    0.47, clean energy 0.75; natural gas through its funds failed and was left out). Held together they are
    diversification, not alpha: the thirteen books' Sharpe of about 1.2 comes from markets that do not move together,
    with 9% a year and a 13% worst loss. They are judged on real fills from here on.</p>
    <p><b>The allocator.</b> The desk recalibrates itself "depending on how it goes" by published rules, each measured on
    the replay before it was switched on and shown on this page. The bubble switch (Greenwood, Shleifer and You): after a
    vehicle's price has doubled in two years, its books run at half their volatility target for a year. The strait rule:
    with tanker transits below a tenth of normal, the ceilings before a weekend are halved. The lifecycle: a candidate
    becomes core after twelve months live with a positive realised Sharpe and a drawdown within its backtest's; any book
    is retired, flat, after twenty-four months with a realised Sharpe below ${MINUS}0.5. Drawdown control was measured and
    left off. Nothing in it ever raises a position above a book's own rules.</p>
    <p><b>Prediction markets.</b> Kalshi's contracts on the price of Brent and WTI, the strait and the US-Iran talks are
    read every hour and archived once a day: their prices are the crowd's probabilities, on questions the forecast cannot
    see. No core book trades on them - there is no history to test a rule on, and the archive is being built so that one
    day there will be. Where the question is a price, the page puts the options' own probability beside the crowd's.</p>
    <p><b>The Oracle books (speculative).</b> On the owner's request, candidate books take their direction from Kalshi,
    read the evening before off the desk's own archive, each index's week-over-week change making a sleeve: the
    probability that the war de-escalates within 90 days (the strait back to normal, a US-Iran senior meeting, a nuclear
    deal - three deadline ladders; rising means short oil, ${MINUS}2 per point) trades the micro WTI future at the highest
    volatility target the rules allow (50%, halved while the bubble switch is on); the Fed path (the expected change of
    the target rate over the FOMC meetings within 90 days, read off each meeting's outcome markets; turning dovish is
    positive, ${MINUS}2.5 per basis point) and the odds of a recession within a year (the yearly NBER markets as one
    ladder; rising is positive, +4 per point) are read, with a sign per book, by long-only books on the S&P 500 and
    utilities funds, up to 2x on margin. Every rule was written down before it was measured; the indices were measured
    once on the archive (bonds and gold measured a negative Sharpe and do not start); there is no backtest in the replay;
    the prior is that none has an edge; the books are judged on live fills only, never promoted, and can wipe out their
    accounts.</p>
    <p><b>Execution.</b> An order fills at the open of the first 30-minute bar that starts after the decision, with the
    instrument's real spread and commissions. Never at the price that produced the signal.</p>
    <p><b>What is not here.</b> No intraday strategy: on the hourly bars of BNO, USO and the contracts, intraday momentum
    showed no edge after costs, and only what survived the test is kept. No Sharpe above 1 on one market: the honest
    ceiling for daily rules on a single oil market is about 0.7-0.8, and the lever above it is independent markets.
    <a href="${repoUrl()}/blob/main/docs/RESEARCH.md" target="_blank" rel="noopener">The research, with numbers and sources →</a></p>
  </details>`;
}

// ---------------------------------------------------------------------------------------------- page
/** `#/signals?at=forecast` opens the page at that section (the book pages link to what each book reads). */
function scrollTarget(): string | null {
  const query = window.location.hash.split("?")[1] ?? "";
  return new URLSearchParams(query).get("at");
}

export async function renderSignals(el: HTMLElement): Promise<void> {
  const loaded = await load<DeskDoc>("desk.json");
  const doc = loaded.data;
  if (!doc) {
    el.innerHTML = `<div class="term"><div class="t-empty">Data unavailable: <code>desk.json</code> does not answer${
      loaded.error ? ` (${escapeHtml(loaded.error)})` : ""
    }. The engine publishes it on every tick; on a first start, try again in half an hour.</div></div>`;
    return;
  }
  el.innerHTML = `<div class="term signals">
    ${doc.sample ? '<div class="banner sample">Sample data: this is not the real state of the books.</div>' : ""}
    ${statusLine(doc, loaded.origin)}
    <header class="d-hero s-hero">
      <h1 class="d-headline s-headline">What the books read</h1>
      <p class="d-lede">The market, the forecast behind the oil books, the Kalshi indices behind the speculative ones,
      and the rules that scale them all. What each book holds is on <a href="#/">the desk</a>.</p>
    </header>
    ${market(doc)}
    ${forecastTable(doc)}
    ${oracleTable(doc)}
    ${allocator(doc)}
    ${options(doc)}
    ${predictionMarkets(doc)}
    ${backtest(doc)}
    ${health(doc)}
    ${howTo()}
  </div>`;
  const at = scrollTarget();
  const target = at ? el.querySelector<HTMLElement>(`#${CSS.escape(at)}`) : null;
  if (target && el.isConnected) target.scrollIntoView({ block: "start" });
}
