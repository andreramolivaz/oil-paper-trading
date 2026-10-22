/** What the desk pages share (`pages/desk.ts`, `pages/book.ts`, `pages/signals.ts`): the status line, a book's
 * state and group, its position and its orders in words, its rules, the zero-centred exposure bar.
 *
 * Everything here formats what `desk.json` and `books/<id>.json` carry. The only arithmetic is counting rows:
 * every figure - an exposure, a P&L, a group's total - comes from the engine with its own rounding.
 */
import { rocketMonitor, type AnalystBlock, type Book, type BookPosition, type DeskDoc, type FillRow, type OptionStructure } from "./desk";
import {
  EMPTY,
  clock,
  day,
  escapeHtml,
  leverage,
  num,
  percent,
  relative,
  signed,
  signedPercent,
  tone,
  usd,
  zoneName,
} from "./format";

export const MINUS = "−";

export const span = (cls: string, text: string): string => `<span class="${cls}">${text}</span>`;
export const toneSpan = (value: number | null | undefined, text: string): string => span(tone(value), text);

export function section(title: string, body: string, note = "", id = ""): string {
  return `<section class="t-sec"${id ? ` id="${escapeHtml(id)}"` : ""}>
    <h2 class="t-h"><span>${escapeHtml(title)}</span>${note ? `<small>${note}</small>` : ""}</h2>
    ${body}
  </section>`;
}

/** A small chevron drawn in SVG (the face has no arrows), for a link that opens or goes back. */
export function chevron(direction: "left" | "right" = "right"): string {
  const d = direction === "right" ? "M6 3.5 10.5 8 6 12.5" : "M10 3.5 5.5 8 10 12.5";
  return `<svg class="chev" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

// ---------------------------------------------------------------------------------------------- status line
export function engineState(doc: DeskDoc): { cls: string; text: string } {
  const ageMin = (Date.now() - Date.parse(doc.generated_at)) / 60000;
  if (!Number.isFinite(ageMin)) return { cls: "warn", text: "Engine state unknown" };
  const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const dow = ny.getDay();
  const hm = ny.getHours() * 60 + ny.getMinutes();
  const weekend = dow === 6 || (dow === 5 && hm >= 18 * 60 + 30) || (dow === 0 && hm < 17 * 60 + 30);
  if (ageMin <= 50) return { cls: "ok", text: "Engine running" };
  if (weekend) return { cls: "dim", text: "Markets closed: back Sunday 17:30 New York" };
  if (ageMin <= 180) return { cls: "warn", text: `Engine late: last tick ${relative(doc.generated_at)}` };
  return { cls: "bad", text: `Engine stopped ${relative(doc.generated_at)}` };
}

export function dataState(doc: DeskDoc): { cls: string; text: string } {
  const h = doc.health;
  if (h.overall === "green") return { cls: "ok", text: `Data ok (${h.n_green} of ${h.n_sources} sources)` };
  if (h.overall === "yellow") {
    const n = h.n_sources - h.n_green;
    return { cls: "ok", text: `Data ok, ${n} secondary source${n === 1 ? "" : "s"} partial` };
  }
  if (h.overall === "red") return { cls: "bad", text: "A critical source is down: no new decisions" };
  return { cls: "dim", text: "Data state unknown" };
}

/** When the numbers are from, whether the engine is alive, whether the data is sound: the first line of a page. */
export function statusLine(doc: DeskDoc, origin: string | null): string {
  const eng = engineState(doc);
  const dat = dataState(doc);
  // sample data has its own banner above this line; a bundled copy is said here
  const from =
    origin === "bundled" ? `<span class="warn">A copy bundled with the site: the data branch did not answer</span>` : "";
  return `<div class="t-status">
    <span>Updated ${escapeHtml(clock(doc.generated_at))} ${escapeHtml(zoneName())}, ${escapeHtml(relative(doc.generated_at))}</span>
    ${from}
    <span class="t-dot ${eng.cls}">${escapeHtml(eng.text)}</span>
    <span class="t-dot ${dat.cls}">${escapeHtml(dat.text)}</span>
  </div>`;
}

// ---------------------------------------------------------------------------------------------- books
const STATUS: Record<string, { cls: string; text: string }> = {
  active: { cls: "ok", text: "Active" },
  halted_breaker: { cls: "warn", text: "Halted until tomorrow: the daily loss limit was hit" },
  halted_stale: { cls: "warn", text: "Halted: stale data" },
  dead: { cls: "bad", text: "Wiped out: needs a reset" },
  target: { cls: "ok", text: "Target reached: 10x. A new life starts it again" },
};

/** A book's state in words. `quiet` is true for the ordinary state, which a row does not need to repeat. */
export function bookState(b: Book): { cls: string; text: string; quiet: boolean } {
  if (b.role === "retired") return { cls: "dim", text: "Retired by the lifecycle rule", quiet: false };
  if (b.status === "active" && rocketMonitor(b)?.stuck) {
    return { cls: "warn", text: "Too small to bet: a new life starts it again", quiet: false };
  }
  const s = STATUS[b.status];
  if (!s) return { cls: "dim", text: b.status, quiet: false };
  return { ...s, quiet: b.status === "active" };
}

/** The groups the desk page shows (engine: `export.GROUPS`), with the words a sentence needs for each. */
export const GROUP_WORDS: Record<string, { label: string; noun: string; verb: string }> = {
  core: { label: "Core desk", noun: "the core desk", verb: "is" },
  candidates: { label: "Candidates", noun: "the candidates", verb: "are" },
  kalshi: { label: "Kalshi books", noun: "the Kalshi books", verb: "are" },
  claude: { label: "Claude books", noun: "the Claude books", verb: "are" },
  lottery: { label: "Lottery", noun: "the lottery", verb: "is" },
};
/** Each group's series slot (styles.css `--series-N`), in the engine's fixed order. The lottery skips slot 5, a pink
 *  too close to the magenta that marks what can be clicked; the five were validated on both themes. */
const GROUP_SLOTS: Record<string, number> = { core: 1, candidates: 2, kalshi: 3, claude: 4, lottery: 6 };

export function groupOf(b: Book): string {
  if (b.group) return b.group;
  if (b.forecast_family === "kalshi" || b.forecast_family === "claude") return b.forecast_family;
  return (b.configured_role ?? b.role ?? "core") === "core" ? "core" : "candidates";
}

/** True for a book whose direction comes from somewhere the replay cannot go: Kalshi's archive, the Claude analyst. */
export const isSpeculative = (b: Book): boolean => !!b.forecast_family && b.forecast_family !== "desk";

/** The four markets the Claude analyst reads (engine: `analyst.MARKETS`), in its order: id, name, the name inside a
 *  sentence, what it means. */
export const ANALYST_MARKETS: [string, string, string, string][] = [
  ["oil", "Crude oil", "crude oil", "Brent and WTI"],
  ["gold", "Gold", "gold", "the metal, through GLD"],
  ["equities", "US equities", "US equities", "the S&P 500"],
  ["bonds", "Long Treasuries", "long Treasuries", "positive: prices up, yields down"],
];
/** A market's name inside a sentence ("long Treasuries"). */
export const analystMarketName = (m: string): string => ANALYST_MARKETS.find(([id]) => id === m)?.[2] ?? m;

const ANALYST_STATE: Record<string, { cls: string; word: string }> = {
  on: { cls: "ok", word: "Reading the news" },
  waiting: { cls: "dim", word: "Waiting for its first reading" },
  off: { cls: "dim", word: "Off" },
  stale: { cls: "warn", word: "No recent reading" },
  budget: { cls: "warn", word: "Budget spent" },
  disabled: { cls: "dim", word: "Turned off" },
};

/** The Claude analyst's state as the pages show it: a dot and a word, then the engine's sentence. */
export function analystStatus(a: AnalystBlock): string {
  const st = ANALYST_STATE[a.state ?? "off"] ?? { cls: "dim", word: a.state ?? "Off" };
  return `<span class="t-dot ${st.cls}">${escapeHtml(st.word)}</span> ${escapeHtml(a.state_text ?? "")}`;
}

export function groupLabel(id: string): string {
  return GROUP_WORDS[id]?.label ?? id.charAt(0).toUpperCase() + id.slice(1);
}

/** One colour per group in the engine's fixed order (core, candidates, Kalshi, Claude, the lottery, then any group
 *  added later), so the colour follows the group: a filter that hides one never repaints another. */
export function groupColor(id: string, all: string[]): string {
  const known = GROUP_SLOTS[id];
  if (known) return `var(--series-${known})`;
  const extra = all.filter((g) => !GROUP_SLOTS[g]).indexOf(id);
  return `var(--series-${Math.min(7, 7 + Math.max(0, extra))})`;
}

export const strike = (x: number): string => num(x, Number.isInteger(x) ? 0 : 1);

/** "19 BNO shares", "100 barrels of MCLZ26", "2 spreads of BNO put 57/55", "1 USO call 175 2026-11-13". */
export function quantity(n: number, unit: string | undefined, symbol: string): string {
  const q = num(Math.abs(n), 0);
  const one = Math.abs(n) === 1;
  if (unit === "shares") return `${q} ${symbol} share${one ? "" : "s"}`;
  if (unit === "barrels") return `${q} barrel${one ? "" : "s"} of ${symbol}`;
  if (unit === "spread") return `${q} spread${one ? "" : "s"}, ${symbol}`;
  if (unit === "contract") return `${q} contract${one ? "" : "s"}, ${symbol}`;
  return `${q} ${unit ?? "units"} of ${symbol}`;
}

/** The Rocket's option in words: "1 USO call, strike 175, expiring 13 Nov 2026". */
export function optionWords(p: BookPosition): string {
  const n = Math.abs(p.units);
  const what = `${p.underlying ?? ""} ${p.kind ?? "option"}${n === 1 ? "" : "s"}`.trim();
  return `${num(n, 0)} ${what}, strike ${p.strike === null || p.strike === undefined ? EMPTY : strike(p.strike)}, expiring ${day(p.expiry ?? null)}`;
}

/** A position in words: the side of the OIL (or fund) exposure first. An inverse fund is held long and is short oil. */
export function positionWords(p: BookPosition): string {
  if (p.unit === "contract") return `Bought ${optionWords(p)}`;
  const what = quantity(p.units, p.unit, p.symbol);
  if ((p.multiplier ?? 1) < 0) return `Short through ${what}`;
  return `${p.side === "short" ? "Short" : "Long"} ${what}`;
}

export function structureWords(s: OptionStructure): string {
  return `Put spread ${s.underlying} ${strike(s.short.strike)}/${strike(s.long.strike)}, expiring ${day(s.expiry)}`;
}

/** A queued order in words: "Buy 7 BNO shares". */
export function orderWords(o: { symbol: string; units: number; unit?: string }, fallbackUnit?: string): string {
  return `${o.units > 0 ? "Buy" : "Sell"} ${quantity(o.units, o.unit ?? fallbackUnit, o.symbol)}`;
}

/** What a book holds, in a few words, for its row on the desk page. */
export function holdingShort(b: Book): string {
  if (b.kind === "options") {
    const n = b.structures?.length ?? 0;
    return n ? `${n} put spread${n > 1 ? "s" : ""} open` : "No open spread";
  }
  const ps = b.positions ?? [];
  if (ps.length) return ps.map(positionWords).join(", ");
  if (b.pending?.length) return "In cash, an order queued";
  return "In cash";
}

/** A zero-centred bar of an exposure (a multiple of the book's equity): long to the right of the centre line, short
 *  to the left, full length at `scale` - 1x, a position worth the whole account; beyond it, on leverage, the bar ends
 *  in a notch. Decorative: the figure beside it is the text a reader (or a screen reader) gets. */
export function exposureBar(x: number | null | undefined, scale = 1): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "";
  const frac = Math.min(1, Math.abs(x) / scale);
  const width = (frac * 50).toFixed(2);
  const left = x >= 0 ? "50" : (50 - frac * 50).toFixed(2);
  const fill = x === 0 ? "" : `<span class="xbar-fill${Math.abs(x) > scale ? " over" : ""}${x < 0 ? " neg" : ""}" style="left:${left}%;width:${width}%"></span>`;
  return `<span class="xbar" aria-hidden="true">${fill}</span>`;
}

/** The exposure as text: "long 0.12x", "short 0.40x", "flat". */
export function exposureWords(x: number | null | undefined): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return EMPTY;
  if (x === 0) return "flat";
  return `${x > 0 ? "long" : "short"} ${leverage(Math.abs(x))}`;
}

const REASON: Record<string, string> = {
  signal: "entry",
  rebalance: "rebalance",
  roll: "roll",
  margin_call: "margin call",
  liquidation: "liquidation",
  circuit_breaker: "daily stop",
  stop_loss: "stop",
  reset: "reset",
  open: "opened",
  sell: "exit",
  expiry: "expiry",
  apertura: "opened",
  scadenza: "expiry",
  distribution: "distribution",
};

export const reasonWords = (r: string | null | undefined): string => (r ? (REASON[r] ?? r.replaceAll("_", " ")) : EMPTY);

/** A fill's trade without its price: "Bought 7 BNO shares", "Sold 2 spreads, BNO put 57/55 2026-11-20". */
export function tradeWords(f: FillRow): string {
  if (f.unit === "contract") {
    const what = quantity(f.units, "contract", f.symbol);
    return f.reason === "open" ? `Bought ${what}` : f.reason === "expiry" ? `Expired ${what}` : `Sold ${what}`;
  }
  if (f.unit === "spread") {
    const opened = f.reason === "open" || f.reason === "apertura";
    return `${opened ? "Sold" : "Settled"} ${quantity(f.units, "spread", f.symbol)}`;
  }
  return `${f.units > 0 ? "Bought" : "Sold"} ${quantity(f.units, f.unit, f.symbol)}`;
}

/** A fill in words: "Bought 7 BNO shares at 65.08", "Sold 2 spreads, BNO put 57/55 2026-11-20, for 0.25". */
export function fillWords(f: FillRow): string {
  if (f.unit === "contract") return `${tradeWords(f)} at ${num(f.price, 2)} a share`;
  if (f.unit === "spread") {
    const what = quantity(f.units, "spread", f.symbol);
    return f.reason === "open" || f.reason === "apertura"
      ? `Sold ${what}, for ${num(f.price, 2)} a share`
      : `Settled ${what}, at ${num(f.price, 2)} a share`;
  }
  return `${f.units > 0 ? "Bought" : "Sold"} ${quantity(f.units, f.unit, f.symbol)} at ${num(f.price, 2)}`;
}

// ---------------------------------------------------------------------------------------------- rules
export function capText(b: Book): string {
  if (b.kind === "options") return "defined risk";
  if (b.kind === "lottery") return `${leverage((b.rules.max_delta_leverage as number | undefined) ?? 10)} on the delta`;
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

export function stressText(b: Book): string {
  if (b.kind === "options") return span("t-dim", "by structure");
  if (b.kind === "lottery") return span("t-dim", "at most the premium");
  const v = b.stress?.["-20%"];
  if (v === null || v === undefined) return EMPTY;
  return toneSpan(v, signedPercent(v, 1));
}

/** What a Kalshi book's forecast is, from the signed sleeves on its row (the de-escalation index for the oil
 *  book; the Fed path and the recession odds for the fund books). */
const SLEEVE_WORDS: Record<string, string> = {
  deescalation: `the de-escalation index (${MINUS}2 per point: rising = short oil)`,
  fedpath: `the Fed path (${MINUS}2.5 per basis point: turning dovish = positive)`,
  recession: "the odds of a recession within a year (+4 per point: rising = positive)",
};

export function kalshiSleevesText(sleeves: Record<string, 1 | -1> | null | undefined): string {
  const names = Object.keys(sleeves ?? {});
  if (!names.length) {
    return "Forecast: the week's change of a Kalshi index, read the evening before on the desk's own archive; no backtest exists for it.";
  }
  const parts = names.map((n) => `${SLEEVE_WORDS[n] ?? n}${(sleeves ?? {})[n] < 0 ? ", read with a minus" : ""}`);
  const how = names.length > 1 ? "the mean, with those signs, of the week's changes of" : "the week's change of";
  return `Forecast: ${how} ${parts.join(" and ")}, read the evening before on the desk's own archive; no backtest exists for it.`;
}

/** What a Claude book's forecast is, from the market on its row. */
export function analystSleevesText(sleeves: Record<string, 1 | -1> | null | undefined): string {
  const names = Object.keys(sleeves ?? {}).map(analystMarketName);
  const what = names.length ? names.join(" and ") : "its market";
  return `Forecast: the Claude analyst's reading of the day's news for ${escapeHtml(what)}, from ${MINUS}20 to +20 for the next five sessions, taken once a day in the New York morning; a reading more than three days old is no view, and the book closes. No backtest can exist for it.`;
}

export function ruleLine(b: Book): string {
  const r = b.rules;
  if (b.kind === "lottery") {
    const raw = (r.dte as unknown[] | undefined) ?? [];
    const dte = [num(raw[0] as number, 0), num(raw[1] as number, 0), num(raw[2] as number, 0)];
    return `Each life starts at ${usd(r.initial_capital as number, 0)}. Once a day at ${escapeHtml(String(r.decision_time_ny ?? "15:00"))} New York:
    with the Dynamic at +${num(r.forecast_threshold as number, 0)} or more it buys calls on USO, at ${MINUS}${num(
      r.forecast_threshold as number,
      0,
    )} or less puts, and only when Kalshi's odds of a de-escalation moved over the week the way the bet needs (down
    for calls, up for puts). The option: the listed expiry closest to ${dte[1]} days (between ${dte[0]} and ${
      dte[2]
    }), delta nearest ${num(r.target_delta as number, 2)} if it fits in ${percent(r.stake as number, 0)} of the account, else further
    from the price down to delta ${num(r.min_delta as number, 2)}; whole contracts, never more than ${leverage(
      r.max_delta_leverage as number,
    )} the account in delta; quoted within ${percent(r.max_leg_spread as number, 0)} of its price, bought a quarter of the
    spread from the ask, ${usd(r.fee_per_contract as number, 2)} a contract. One bet at a time, sold when the Dynamic stops
    pointing its way or with ${num(r.roll_days as number, 0)} days left (then replaced if the signal holds). The life ends when
    the account reaches ten times its start (the target: the option is sold and nothing more is bought), or at ${percent(
      r.floor as number,
      0,
    )} of its start. The options book's limits and the volatility targets are suspended for this book alone.`;
  }
  if (b.kind === "options") {
    const dte = (r.dte as number[] | undefined) ?? [];
    return `Sells one put spread only with a forecast of at least +${num(r.forecast_threshold as number, 0)}; the put sold at
    delta ${num(r.short_delta as number, 2)}, about ${dte[1] ?? "30"} days to expiry; maximum loss ${percent(
      r.risk_per_structure as number,
      0,
    )} of the account per structure, at most ${r.max_open ?? 2} structures; legs quoted within ${percent(
      r.max_leg_spread as number,
      0,
    )} of their price, filled a quarter of the spread from the bad side.`;
  }
  const weekend = r.weekend_max_leverage as number | null | undefined;
  const source =
    b.forecast_family === "kalshi"
      ? `${kalshiSleevesText(b.sleeves)} `
      : b.forecast_family === "claude"
        ? `${analystSleevesText(b.sleeves)} `
        : "";
  const sides = r.long_only
    ? "long only"
    : r.short_via
      ? `long and short (short by buying ${escapeHtml(String(r.short_via))})`
      : "long and short";
  return `${source}Volatility target ${percent(r.vol_target as number, 0)}, leverage cap ${leverage(r.max_leverage as number)}${
    weekend !== null && weekend !== undefined ? ` (${leverage(weekend)} before a weekend or a holiday)` : ""
  }, ${sides}, margin ${percent(r.margin_rate as number, 0)}, decision at ${escapeHtml(
    String(r.decision_time_ny ?? ""),
  )} New York, daily stop at ${MINUS}${percent(r.daily_loss_breaker as number, 0)}.`;
}

export function allocatorLine(b: Book): string {
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
        l.max_drawdown_live === null || l.max_drawdown_live === undefined
          ? ""
          : `, drawdown ${MINUS}${percent(l.max_drawdown_live, 1)}`
      }.`,
    );
  }
  if (b.role_change) {
    parts.push(
      `${escapeHtml(b.role_change.role)} since ${escapeHtml(day(b.role_change.since))}: ${escapeHtml(b.role_change.reason)}.`,
    );
  }
  return parts.length ? `<p class="t-dim">${parts.join(" ")}</p>` : "";
}

/** The money a book spent to trade, in one sentence. */
export function costsLine(b: Book): string {
  if (!b.costs) return "";
  return `Costs so far: spread and slippage ${usd(b.costs.spread_and_slippage, 2)}, commissions ${usd(
    b.costs.commission,
    2,
  )}, interest ${usd(b.costs.financing, 2)}.${b.distributions ? ` Distributions received: ${usd(b.distributions, 2)}.` : ""}`;
}
