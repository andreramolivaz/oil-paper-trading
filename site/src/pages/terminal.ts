/** The terminal: one screen of text for the desk's paper books.
 *
 * Top to bottom it answers the questions somebody opening the page has: what is oil doing, what does the model
 * think, what does each book hold and what would a gap do to it, what happened lately, is the machine alive.
 * No number is computed here: the page formats what `desk.json` carries and says "n/a" for what it does not.
 */
import { lineChart, lineLegend, type LineSeries } from "../charts/lines";
import { repoUrl } from "../config";
import { load } from "../data";
import {
  bookColor,
  portfolios,
  type Book,
  type DeskDoc,
  type ForecastBlock,
  type OptionUnderlying,
  type PredictionEvent,
  type Quote,
} from "../desk";
import { EMPTY, escapeHtml, leverage, num, percent, relative, signedPercent, signedUsd, tone, usd } from "../format";

const MINUS = "−";

/** dd MMM HH:mm in the reader's own time zone (the header says which). */
function clock(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(t);
}

function zoneName(): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
      .formatToParts(new Date())
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? "local time";
  } catch {
    return "local time";
  }
}

function day(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso.length <= 10 ? `${iso}T12:00:00Z` : iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }).format(t);
}

function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const s = num(Math.abs(value), digits);
  return value > 0 ? `+${s}` : value < 0 ? `${MINUS}${s}` : s;
}

const span = (cls: string, text: string): string => `<span class="${cls}">${text}</span>`;
const toneSpan = (value: number | null | undefined, text: string): string => span(tone(value), text);

function section(title: string, body: string, note = ""): string {
  return `<section class="t-sec">
    <h2 class="t-h"><span>${escapeHtml(title)}</span>${note ? `<small>${note}</small>` : ""}</h2>
    ${body}
  </section>`;
}

// ---------------------------------------------------------------------------------------------- header
function engineState(doc: DeskDoc): { cls: string; text: string } {
  const ageMin = (Date.now() - Date.parse(doc.generated_at)) / 60000;
  if (!Number.isFinite(ageMin)) return { cls: "warn", text: "engine state n/a" };
  const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const dow = ny.getDay();
  const hm = ny.getHours() * 60 + ny.getMinutes();
  const weekend = dow === 6 || (dow === 5 && hm >= 18 * 60 + 30) || (dow === 0 && hm < 17 * 60 + 30);
  if (ageMin <= 50) return { cls: "ok", text: "engine running" };
  if (weekend) return { cls: "dim", text: "markets closed: back Sunday 17:30 New York" };
  if (ageMin <= 180) return { cls: "warn", text: `engine late (last tick ${relative(doc.generated_at)})` };
  return { cls: "bad", text: `engine stopped ${relative(doc.generated_at)}` };
}

function dataState(doc: DeskDoc): { cls: string; text: string } {
  const h = doc.health;
  if (h.overall === "green") return { cls: "ok", text: `data ok (${h.n_green}/${h.n_sources})` };
  if (h.overall === "yellow") return { cls: "ok", text: `data ok, ${h.n_sources - h.n_green} secondary sources partial` };
  if (h.overall === "red") return { cls: "bad", text: "a critical source is down: no new decisions" };
  return { cls: "dim", text: "data state n/a" };
}

function header(doc: DeskDoc, origin: string | null): string {
  const eng = engineState(doc);
  const dat = dataState(doc);
  const from = origin === "live" ? "" : origin === "bundled" ? " · copy bundled with the site" : origin === "sample" ? " · SAMPLE DATA" : "";
  return `<div class="t-status">
    <span>updated ${escapeHtml(clock(doc.generated_at))} ${escapeHtml(zoneName())} · ${escapeHtml(relative(doc.generated_at))}${escapeHtml(from)}</span>
    <span class="t-dot ${eng.cls}">${escapeHtml(eng.text)}</span>
    <span class="t-dot ${dat.cls}">${escapeHtml(dat.text)}</span>
  </div>`;
}

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
    "Yahoo prices, 10-15 minutes late · copper and dollar: daily closes · Hormuz: IMF PortWatch (AIS), weekly",
  );
}

// ---------------------------------------------------------------------------------------------- forecast
function forecastTable(doc: DeskDoc): string {
  const cols: { key: string; title: string; f: ForecastBlock }[] = [];
  if (doc.forecast.BNO) cols.push({ key: "BNO", title: "Brent · BNO", f: doc.forecast.BNO });
  if (doc.forecast.MCL) cols.push({ key: "MCL", title: "WTI · /MCL", f: doc.forecast.MCL });
  if (!cols.length) return section("Forecast", `<div class="t-empty">No forecast: the daily series are missing.</div>`);
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
  return section("Forecast", body, "the same for every book on that instrument: only the size differs");
}

// ---------------------------------------------------------------------------------------------- books
const STATUS: Record<string, { cls: string; text: string }> = {
  active: { cls: "ok", text: "active" },
  halted_breaker: { cls: "warn", text: "halted until tomorrow (daily loss)" },
  halted_stale: { cls: "warn", text: "halted: stale data" },
  dead: { cls: "bad", text: "wiped out: needs a reset" },
};

function positionText(b: Book): string {
  if (b.kind === "options") {
    const n = b.structures?.length ?? 0;
    if (!n) return span("t-dim", "no open structure");
    return `${n} put spread${n > 1 ? "s" : ""} · max risk ${usd(b.max_loss_open ?? null, 0)} (${percent(b.max_loss_open_pct ?? null, 1)})`;
  }
  const parts: string[] = [];
  for (const p of b.positions ?? []) {
    parts.push(
      `${p.side} ${num(Math.abs(p.units), 0)} ${escapeHtml(p.unit)} ${escapeHtml(p.symbol)} at ${num(p.avg_price, 2)} ${toneSpan(
        p.unrealized,
        signedUsd(p.unrealized, 0),
      )}`,
    );
  }
  for (const o of b.pending ?? []) {
    parts.push(span("warn", `order queued: ${signed(o.units, 0)} ${escapeHtml(o.symbol)}`));
  }
  return parts.length ? parts.join(" · ") : span("t-dim", "in cash");
}

function capText(b: Book): string {
  if (b.kind === "options") return "defined risk";
  const cap = b.cap_today ?? (b.rules.max_leverage as number | undefined) ?? null;
  const risk = b.allocator?.risk;
  const scaled =
    risk === null || risk === undefined || risk >= 1
      ? ""
      : risk <= 0
        ? " <span class='warn'>retired</span>"
        : ` <span class='warn'>risk ×${num(risk, 2).replace(/\.?0+$/, "")}</span>`;
  return `${leverage(cap)}${b.cap_reduced_today ? " <span class='warn'>pre-closure</span>" : ""}${scaled}`;
}

function stressText(b: Book): string {
  if (b.kind === "options") return span("t-dim", "by structure");
  const v = b.stress?.["-20%"];
  if (v === null || v === undefined) return EMPTY;
  return toneSpan(v, signedPercent(v, 1));
}

function bookRow(b: Book): string {
  const st = STATUS[b.status] ?? { cls: "dim", text: b.status };
  return `<tr>
    <th scope="row"><span class="t-key" style="--sw:${bookColor(b.id)}"></span>${escapeHtml(b.name)}${
      b.experimental ? ' <span class="t-dim">experimental</span>' : ""
    }${b.role_change ? ` <span class="warn">${escapeHtml(b.role_change.role)} since ${escapeHtml(day(b.role_change.since))}</span>` : ""}<small>${escapeHtml(
      b.vehicle,
    )}</small></th>
    <td class="n" data-l="Equity $"><b>${num(b.equity, 2)}</b></td>
    <td class="n" data-l="Today">${toneSpan(b.pnl_day, signedPercent(b.pnl_day_pct, 2))}</td>
    <td class="n" data-l="Since start">${toneSpan(b.pnl_total, signedPercent(b.pnl_total_pct, 2))}</td>
    <td class="n" data-l="From peak">${b.drawdown ? `${MINUS}${percent(b.drawdown, 1)}` : "0%"}</td>
    <td class="n" data-l="Leverage">${leverage(b.leverage)}</td>
    <td class="n" data-l="Cap">${capText(b)}</td>
    <td class="n" data-l="If −20% gap">${stressText(b)}</td>
    <td class="w" data-l="Position">${positionText(b)}</td>
    <td class="s"><span class="t-dot ${st.cls}">${escapeHtml(st.text)}</span></td>
  </tr>`;
}

function totalRow(label: string, t: DeskDoc["total"] | undefined, note: string): string {
  if (!t) return "";
  return `<tr class="t-total"><th scope="row">${escapeHtml(label)}</th><td class="n" data-l="Equity $"><b>${num(t.equity, 2)}</b></td><td class="x"></td>
    <td class="n" data-l="Since start">${toneSpan(t.pnl, signedPercent(t.pnl_pct, 2))}</td><td colspan="6" class="t-dim w">${escapeHtml(note)}</td></tr>`;
}

function booksTable(doc: DeskDoc): string {
  const core = doc.books.filter((b) => (b.role ?? "core") === "core");
  const candidates = doc.books.filter((b) => (b.role ?? "core") !== "core");
  const capital = num(doc.books[0]?.initial_capital ?? 10000, 0);
  const candidateRows = candidates.length
    ? `<tr class="t-break"><th scope="row" colspan="10" class="t-dim w">Candidates · paper books beside the four, the prudent book's rules on their own price (gold, copper, agriculture, Treasuries, equities, uranium, utilities, lithium, semiconductors, clean energy), judged on real fills before anybody trusts them</th></tr>
      ${candidates.map(bookRow).join("")}
      ${totalRow("Candidates", doc.total_candidates, `${candidates.length} separate accounts of ${capital} $ each`)}`
    : "";
  const body = `<div class="t-scroll"><table class="t-table t-books">
    <thead><tr>
      <th>Book</th><th class="n">Equity $</th><th class="n">Today</th><th class="n">Since start</th>
      <th class="n">From peak</th><th class="n">Leverage</th><th class="n">Cap</th><th class="n">If ${MINUS}20% gap</th><th>Position</th><th>State</th>
    </tr></thead>
    <tbody>${core.map(bookRow).join("")}
    ${totalRow("Total", doc.total, `${core.length} separate accounts of ${capital} $ each: the total is only a sum`)}
    ${candidateRows}</tbody>
  </table></div>
  <div class="t-chart" id="t-equity"></div><div id="t-equity-legend"></div>`;
  return section(
    "Books",
    body,
    `paper trading: no real orders, no broker · "if ${MINUS}20% gap": the book's loss on a 20% gap of its instrument at today's position, before any stop`,
  );
}

function ruleLine(b: Book): string {
  const r = b.rules;
  if (b.kind === "options") {
    const dte = (r.dte as number[] | undefined) ?? [];
    return `Sells one put spread only with a forecast ≥ +${num(r.forecast_threshold as number, 0)}; put sold at delta ${num(
      r.short_delta as number,
      2,
    )}, about ${dte[1] ?? "30"} days to expiry; maximum loss ${percent(r.risk_per_structure as number, 0)} of the account per structure,
    at most ${r.max_open ?? 2} structures; legs quoted within ${percent(r.max_leg_spread as number, 0)} of their price, filled a quarter
    of the spread from the bad side.`;
  }
  const weekend = r.weekend_max_leverage as number | null | undefined;
  return `Volatility target ${percent(r.vol_target as number, 0)} · leverage cap ${leverage(r.max_leverage as number)}${
    weekend !== null && weekend !== undefined ? ` (${leverage(weekend)} before a weekend or a holiday)` : ""
  } · ${r.long_only ? "long only" : r.short_via ? `long and short (short by buying ${escapeHtml(String(r.short_via))})` : "long and short"} · margin ${percent(r.margin_rate as number, 0)} · decision at ${escapeHtml(
    String(r.decision_time_ny ?? ""),
  )} New York · daily stop at ${MINUS}${percent(r.daily_loss_breaker as number, 0)}.`;
}

function bookDetails(doc: DeskDoc): string {
  return doc.books
    .map((b) => {
      const d = b.last_decision;
      const costs = b.costs
        ? `spread and slippage ${usd(b.costs.spread_and_slippage, 2)} · commissions ${usd(b.costs.commission, 2)} · interest ${usd(
            b.costs.financing,
            2,
          )}`
        : "";
      const liq =
        b.liquidation_price !== null && b.liquidation_price !== undefined
          ? `<p>Forced liquidation price: <b>${num(b.liquidation_price, 2)}</b> (the broker closes the position when the margin is no longer enough).</p>`
          : "";
      const stress = b.stress
        ? `<p class="t-dim">On a gap of the instrument, at today's position and before any stop: ${Object.entries(b.stress)
            .map(([move, v]) => `${escapeHtml(move)} → ${signedPercent(v, 1)}`)
            .join(" · ")}.</p>`
        : "";
      const structures = (b.structures ?? [])
        .map(
          (s) =>
            `<p>${escapeHtml(s.underlying)} put ${num(s.short.strike, 0)}/${num(s.long.strike, 0)} expiring ${escapeHtml(
              day(s.expiry),
            )} × ${s.contracts}: credit ${num(s.credit, 2)}, now worth ${num(s.mark, 2)}${s.mark_stale ? " (stale mark)" : ""};
            max gain ${usd(s.max_gain, 0)}, max loss ${usd(s.max_loss, 0)}, breakeven ${num(s.breakeven, 2)}${
              s.awaiting_close ? " · expired, waiting for the official close" : ""
            }.</p>`,
        )
        .join("");
      return `<details class="t-details">
        <summary><span class="t-key" style="--sw:${bookColor(b.id)}"></span><b>${escapeHtml(b.name)}</b>
          <span class="t-dim">${escapeHtml(b.robinhood ?? b.vehicle)}${b.role === "candidate" ? " · candidate" : ""}</span></summary>
        <p>${escapeHtml(b.description)}</p>
        <p class="t-dim">${ruleLine(b)}</p>
        ${structures}
        ${d ? `<p><span class="t-dim">Last decision, ${escapeHtml(clock(d.ts))}:</span> ${escapeHtml(d.rationale ?? "")}</p>` : ""}
        ${allocatorLine(b)}
        ${stress}
        ${liq}
        ${b.last_roll ? `<p class="t-dim">Last roll: ${escapeHtml(b.last_roll)}</p>` : ""}
        ${costs ? `<p class="t-dim">Costs paid so far: ${costs}. Fills: ${b.n_fills ?? 0}.${
          b.distributions ? ` Distributions received: ${usd(b.distributions, 2)}.` : ""
        }</p>` : ""}
        ${b.note ? `<p class="t-dim">${escapeHtml(b.note)}</p>` : ""}
        ${b.rules.short_note ? `<p class="t-dim">${escapeHtml(String(b.rules.short_note))}</p>` : ""}
      </details>`;
    })
    .join("");
}

function allocatorLine(b: Book): string {
  const a = b.allocator;
  const l = b.lifecycle;
  const parts: string[] = [];
  if (a && a.reasons.length) parts.push(`Allocator, ${escapeHtml(day(a.day))}: ${escapeHtml(a.reasons.join("; "))}.`);
  else if (a) parts.push(`Allocator, ${escapeHtml(day(a.day))}: the book's own rules, nothing scaled.`);
  if (l && l.months_live !== null && l.months_live !== undefined) {
    parts.push(
      `Live record: ${num(l.months_live, 1)} months${
        l.sharpe_live === null || l.sharpe_live === undefined ? "" : `, realised Sharpe ${signed(l.sharpe_live, 2)}`
      }${
        l.max_drawdown_live === null || l.max_drawdown_live === undefined ? "" : `, drawdown ${MINUS}${percent(l.max_drawdown_live, 1)}`
      }.`,
    );
  }
  if (b.role_change) parts.push(`${escapeHtml(b.role_change.role)} since ${escapeHtml(day(b.role_change.since))}: ${escapeHtml(b.role_change.reason)}.`);
  return parts.length ? `<p class="t-dim">${parts.join(" ")}</p>` : "";
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
    .filter((b) => b.lifecycle && b.lifecycle.months_live !== null && b.lifecycle.months_live !== undefined)
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
  return section("Allocator", body, "how the desk recalibrates itself, by published rules");
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
  const rows = e.markets
    .filter((m) => m.mid !== null && m.mid !== undefined)
    .map(
      (m) => `<tr><td>${escapeHtml(m.label)}</td>
        <td class="n"><b>${pct(m.mid)}</b><small>${num(m.yes_bid, 2)}–${num(m.yes_ask, 2)}</small></td>
        ${withOptions ? `<td class="n">${m.options_p_above === undefined ? "" : `≈ ${pct(m.options_p_above)}`}</td>` : ""}
        <td class="n t-dim">${m.volume === null || m.volume === undefined ? "" : num(m.volume, 0)}</td></tr>`,
    )
    .join("");
  return `<div class="t-scroll"><table class="t-table t-log">
    <thead><tr><th>Market</th><th class="n">Crowd</th>${withOptions ? '<th class="n">Options ≈</th>' : ""}<th class="n">Volume</th></tr></thead>
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
      parts.push(
        `${escapeHtml(ref.symbol ?? "")} now ${num(ref.price, 2)} $${
          im?.p_above_reference === null || im?.p_above_reference === undefined ? "" : ` → P(above it) <b>${pct(im.p_above_reference)}</b>`
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

function predictionMarkets(doc: DeskDoc): string {
  const pm = doc.prediction_markets;
  if (!pm) return "";
  if (!pm.asof) {
    return section(
      "Prediction markets",
      `<div class="t-empty">No reading from Kalshi yet: the tick asks for it once a day with the other slow tables.</div>`,
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
  const body = `${events.length ? blocks : `<div class="t-empty">The reading holds no open market in the series asked for.</div>`}
    <p class="t-dim">${escapeHtml(pm.note)} A price ladder's "crowd median" is where the ladder crosses one half; "≈" marks the
    options' twin, a driftless lognormal at the ATM implied volatility of the fund's options with no smile and no basis
    between the fund and the future.${foot.length ? ` ${foot.join(". ")}.` : ""}</p>`;
  return section(
    "Prediction markets",
    body,
    `Kalshi, read ${escapeHtml(clock(pm.asof))}${pm.stale ? ' <span class="warn">stale</span>' : ""} · ${pm.markets ?? 0} open markets · the price of a contract paying 1 $ is the crowd's probability`,
  );
}

// ---------------------------------------------------------------------------------------------- activity
function decisions(doc: DeskDoc): string {
  if (!doc.decisions.length) {
    return section("Decisions", `<div class="t-empty">No decision yet: the first comes on the first tick after 15:00 New York.</div>`);
  }
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  const rows = doc.decisions
    .slice(0, 14)
    .map((d) => {
      const acted = Boolean(d.order_units);
      return `<tr>
        <td class="t-nowrap">${escapeHtml(clock(d.ts))}</td>
        <td class="t-nowrap"><span class="t-key" style="--sw:${bookColor(d.book)}"></span>${escapeHtml(names.get(d.book) ?? d.book)}</td>
        <td class="w">${acted ? "" : '<span class="t-dim">no order · </span>'}${escapeHtml(d.text ?? "")}</td>
      </tr>`;
    })
    .join("");
  return section(
    "Decisions",
    `<div class="t-scroll"><table class="t-table t-log t-stack"><tbody>${rows}</tbody></table></div>`,
    "one a day per book, written even when no order follows",
  );
}

function fills(doc: DeskDoc): string {
  if (!doc.fills.length) {
    return section("Fills", `<div class="t-empty">No fill yet. An order fills on the first 30-minute bar that starts after the decision.</div>`);
  }
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  const REASON: Record<string, string> = {
    signal: "entry",
    rebalance: "rebalance",
    roll: "roll",
    margin_call: "margin",
    liquidation: "liquidation",
    circuit_breaker: "daily stop",
    reset: "reset",
    open: "open",
    expiry: "expiry",
    apertura: "open",
    scadenza: "expiry",
  };
  const rows = doc.fills
    .slice(0, 16)
    .map(
      (f) => `<tr>
        <td class="t-nowrap">${escapeHtml(clock(f.ts))}</td>
        <td class="t-nowrap"><span class="t-key" style="--sw:${bookColor(f.book)}"></span>${escapeHtml(names.get(f.book) ?? f.book)}</td>
        <td class="n t-nowrap">${toneSpan(f.units, signed(f.units, 0))} ${escapeHtml(f.unit)}</td>
        <td class="t-nowrap">${escapeHtml(f.symbol)}</td>
        <td class="n">${num(f.price, 2)}</td>
        <td class="n">${f.cost_bps === null ? "" : `${num(f.cost_bps, 1)} bp`}</td>
        <td>${escapeHtml(REASON[f.reason] ?? f.reason)}</td>
        <td class="n">${f.realized_pnl ? toneSpan(f.realized_pnl, signedUsd(f.realized_pnl, 2)) : ""}</td>
      </tr>`,
    )
    .join("");
  return section(
    "Fills",
    `<div class="t-scroll"><table class="t-table t-log">
      <thead><tr><th>When</th><th>Book</th><th class="n">Quantity</th><th>Instrument</th><th class="n">Price</th>
      <th class="n">Cost</th><th>Reason</th><th class="n">Closed P&amp;L</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`,
    "price after spread and slippage; the cost is the distance from the reference price",
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
  return section("Options", body, `Cboe quotes, 15 minutes late · read ${escapeHtml(clock(mon.generated_at))}`);
}

// ---------------------------------------------------------------------------------------------- backtest
function backtest(doc: DeskDoc): string {
  const bt = doc.backtest;
  if (!bt) {
    return section("Backtest", `<div class="t-empty">The backtest is computed on the first tick that has the data, then weekly: not published yet.</div>`);
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
  const body = `<div class="t-scroll"><table class="t-table t-backtest t-summary">
    <thead><tr><th>Book</th><th class="n">Yearly return</th><th class="n">Volatility</th><th class="n">Sharpe</th>
      <th class="n">Max drawdown</th><th class="n">Leverage 95th</th><th class="n">P(${MINUS}50% in a year)</th>
      <th class="n">Sharpe at 2× costs</th></tr></thead>
    <tbody>${coreRows}${opt}${candRows ? `<tr class="t-break"><th colspan="8" class="t-dim w">Candidates</th></tr>${candRows}` : ""}${together}${bench}</tbody></table></div>
    <p class="t-dim">Real costs, ${escapeHtml(bt.fill_rule ?? "")}. The "t" under each Sharpe is how many standard errors it sits
    from zero: above 2 is serious evidence, not certainty. Part of the result is selection among the signals tried, and
    2023, 2024 and 2025 were all negative for the oil books. <a href="#/backtest">Year by year, components and sensitivity →</a></p>`;
  return section("Backtest", body, `computed ${escapeHtml(relative(bt.generated_at))}`);
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
  return section("Status", body, `${h.n_green} of ${h.n_sources} sources fine`);
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
    read once a day and archived: their prices are the crowd's probabilities, on questions the forecast cannot see.
    No book trades on them - there is no history to test a rule on, and the archive is being built so that one day there
    will be. Where the question is a price, the page puts the options' own probability beside the crowd's.</p>
    <p><b>Execution.</b> An order fills at the open of the first 30-minute bar that starts after the decision, with the
    instrument's real spread and commissions. Never at the price that produced the signal.</p>
    <p><b>What is not here.</b> No intraday strategy: on the hourly bars of BNO, USO and the contracts, intraday momentum
    showed no edge after costs, and only what survived the test is kept. No Sharpe above 1 on one market: the honest
    ceiling for daily rules on a single oil market is about 0.7-0.8, and the lever above it is independent markets.
    <a href="${repoUrl()}/blob/main/docs/RESEARCH.md" target="_blank" rel="noopener">The research, with numbers and sources →</a></p>
  </details>`;
}

// ---------------------------------------------------------------------------------------------- page
function equitySeries(doc: DeskDoc): LineSeries[] {
  return doc.books.map((b) => ({
    id: b.id,
    label: b.name,
    color: bookColor(b.id),
    points: b.curve.map((p) => ({ t: Date.parse(p.t), v: p.v })).filter((p) => Number.isFinite(p.t)),
  }));
}

export async function renderTerminal(el: HTMLElement): Promise<void> {
  const loaded = await load<DeskDoc>("desk.json");
  const doc = loaded.data;
  if (!doc) {
    el.innerHTML = `<div class="term"><div class="t-empty">Data unavailable: <code>desk.json</code> does not answer${
      loaded.error ? ` (${escapeHtml(loaded.error)})` : ""
    }. The engine publishes it on every tick; on a first start, try again in half an hour.</div></div>`;
    return;
  }
  el.innerHTML = `<div class="term">
    ${doc.sample ? '<div class="banner sample">SAMPLE DATA: this is not the real state of the books.</div>' : ""}
    ${header(doc, loaded.origin)}
    ${market(doc)}
    ${forecastTable(doc)}
    ${allocator(doc)}
    ${booksTable(doc)}
    ${bookDetails(doc)}
    ${decisions(doc)}
    ${fills(doc)}
    ${options(doc)}
    ${predictionMarkets(doc)}
    ${backtest(doc)}
    ${health(doc)}
    ${howTo()}
  </div>`;
  const chart = document.getElementById("t-equity");
  const legend = document.getElementById("t-equity-legend");
  if (chart && legend) {
    const series = equitySeries(doc);
    const dateFmt = (t: number): string => clock(new Date(t).toISOString());
    lineChart(chart, series, {
      ariaLabel: "Equity of the books since the start",
      height: 170,
      baseline: doc.books[0]?.initial_capital ?? 10000,
      fmt: (v) => num(v, 0),
      dateFmt,
    });
    legend.innerHTML = series.some((s) => s.points.length >= 2) ? lineLegend(series, (v) => num(v, 0)) : "";
  }
}
