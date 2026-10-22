/** One book's own page: what it is, what it holds now and why, its equity since the start, and its whole history in
 * this life - every fill, every decision in the book's own words, and the lives before a reset.
 *
 * It reads `desk.json` (the book's current state, the block the desk page shows) and `books/<id>.json` (the history
 * the engine writes for the book on every export). Every figure is the engine's; the page counts rows and pages
 * through them.
 */
import { lineChart } from "../charts/lines";
import { readToken, workflowDispatch, workflowPage } from "../config";
import { load } from "../data";
import {
  rocketMonitor,
  type Book,
  type BookHistory,
  type BookLife,
  type BookPosition,
  type DeskDoc,
  type FillRow,
  type HistoryDecision,
  type OptionStructure,
  type Replicate,
  type RocketCandidate,
  type RocketMonitor,
} from "../desk";
import {
  MINUS,
  allocatorLine,
  analystMarketName,
  analystStatus,
  bookState,
  capText,
  chevron,
  costsLine,
  exposureBar,
  exposureWords,
  groupLabel,
  groupOf,
  isSpeculative,
  orderWords,
  positionWords,
  reasonWords,
  ruleLine,
  section,
  span,
  statusLine,
  stressText,
  structureWords,
  toneSpan,
  tradeWords,
} from "../deskview";
import {
  EMPTY,
  day,
  escapeHtml,
  leverage,
  longDay,
  num,
  percent,
  signed,
  signedPercent,
  signedUsd,
  usd,
  when,
  zoneName,
} from "../format";

/** Rows shown before "show more": a page of history, not the whole of it at once. */
const PAGE = 25;

// ---------------------------------------------------------------------------------------------- head and figures
function head(b: Book): string {
  const st = bookState(b);
  const tags = [groupLabel(groupOf(b))];
  if (b.kind === "lottery") tags.push("Risk rules suspended");
  else if (isSpeculative(b)) tags.push("Speculative: no backtest");
  else if (b.experimental) tags.push("Experimental");
  if (b.role_change) tags.push(`${b.role_change.role} since ${longDay(b.role_change.since)}`);
  return `<header class="b-head">
    <h1 class="b-name">${escapeHtml(b.name)}</h1>
    <p class="b-tags">${tags.map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join("")}<span class="t-dot ${st.cls}">${escapeHtml(st.text)}</span></p>
    <p class="b-sub">${escapeHtml(b.robinhood ?? b.vehicle)}${b.vehicle_name ? `, ${escapeHtml(b.vehicle_name)}` : ""}</p>
    <p class="b-desc">${escapeHtml(b.description)}</p>
  </header>`;
}

function figures(b: Book): string {
  if (b.kind === "lottery") return lotteryFigures(b);
  const options = b.kind === "options";
  const exposure = options
    ? `${b.max_loss_open ? `at most ${usd(b.max_loss_open, 0)} more to lose` : "nothing at risk"}<small>${percent(b.max_loss_open_pct ?? null, 1)} of the account, from today's marks</small>`
    : `${exposureBar(b.exposure)} ${escapeHtml(exposureWords(b.exposure))}<small>cap today ${capText(b)}</small>`;
  const since = b.started_at ? `since ${escapeHtml(longDay(b.started_at))}` : "since the start";
  return `<div class="b-figs">
    <div class="b-eq"><span class="k">Equity</span><span class="v">${usd(b.equity, 2)}</span>
      <span class="s">started with ${usd(b.initial_capital, 0)}</span></div>
    <dl class="b-kv">
      <div><dt>Profit and loss</dt><dd>${toneSpan(b.pnl_total, signedUsd(b.pnl_total, 2))}<small>${signedPercent(b.pnl_total_pct, 2)} ${since}</small></dd></div>
      <div><dt>Today</dt><dd>${toneSpan(b.pnl_day, signedUsd(b.pnl_day, 2))}<small>${signedPercent(b.pnl_day_pct, 2)}</small></dd></div>
      <div><dt>From its peak</dt><dd>${b.drawdown ? `${MINUS}${percent(b.drawdown, 2)}` : "at its peak"}</dd></div>
      <div><dt>Exposure</dt><dd class="b-exp">${exposure}</dd></div>
      ${options ? "" : `<div><dt>On a 20% gap down</dt><dd>${stressText(b)}<small>at today's position, before any stop</small></dd></div>`}
    </dl>
  </div>`;
}

// ---------------------------------------------------------------------------------------------- the lottery
/** The rail of the multiple a life can reach, on a log scale from 0.05x (the life is over) to the 10x it plays for. */
const RAIL_LO = 0.05;
const RAIL_HI = 10;
const railAt = (x: number): string =>
  ((Math.log(Math.min(RAIL_HI, Math.max(RAIL_LO, x)) / RAIL_LO) / Math.log(RAIL_HI / RAIL_LO)) * 100).toFixed(2);

function lotteryFigures(b: Book): string {
  const since = b.started_at ? `since ${escapeHtml(longDay(b.started_at))}` : "since the start";
  return `<div class="b-figs">
    <div class="b-eq"><span class="k">Equity</span><span class="v">${usd(b.equity, 2)}</span>
      <span class="s">life ${b.epoch}, started with ${usd(b.initial_capital, 0)}</span></div>
    <dl class="b-kv">
      <div><dt>Profit and loss</dt><dd>${toneSpan(b.pnl_total, signedUsd(b.pnl_total, 2))}<small>${signedPercent(b.pnl_total_pct, 2)} ${since}</small></dd></div>
      <div><dt>Today</dt><dd>${toneSpan(b.pnl_day, signedUsd(b.pnl_day, 2))}<small>${signedPercent(b.pnl_day_pct, 2)}</small></dd></div>
      <div><dt>From its peak</dt><dd>${b.drawdown ? `${MINUS}${percent(b.drawdown, 2)}` : "at its peak"}</dd></div>
      <div><dt>Exposure</dt><dd class="b-exp">${exposureBar(b.exposure)} ${escapeHtml(exposureWords(b.exposure))}<small>the option's delta, at most ${capText(b)}</small></dd></div>
    </dl>
    ${rail(b, rocketMonitor(b))}
  </div>`;
}

/** Where this life stands between the end of it and the 10x it plays for: the one picture the page is for. */
function rail(b: Book, m: RocketMonitor | null): string {
  const now = b.multiple;
  if (now === null || now === undefined || !Number.isFinite(now)) return "";
  const floor = m?.bet_floor_multiple;
  const zone =
    floor !== null && floor !== undefined && floor > RAIL_LO
      ? `<span class="r-zone" style="width:${railAt(floor)}%"></span>`
      : "";
  const ticks: [number, string][] = [
    [1, "start"],
    [2, "2x"],
    [RAIL_HI, "10x"],
  ];
  const beyond = now > RAIL_HI ? ", past the 10x" : "";
  const zoneWords =
    floor !== null && floor !== undefined && m?.bet_floor
      ? ` Below ${usd(m.bet_floor, 0)} (${num(floor, 2)}x) no option the rule admits fits in half the account.`
      : "";
  return `<figure class="r-rail-box">
    <figcaption>This life on the way to 10x: <b>${num(now, 2)}x</b> of its start${beyond}.${zoneWords}</figcaption>
    <div class="r-rail" aria-hidden="true">
      ${zone}
      ${ticks.map(([x, label]) => `<span class="r-tick${x === RAIL_HI ? " end" : ""}" style="left:${railAt(x)}%"><span>${label}</span></span>`).join("")}
      <span class="r-now" style="left:${railAt(now)}%"><span>${num(now, 2)}x</span></span>
    </div>
    <div class="r-scale" aria-hidden="true"><span>0.05x: the life is over</span>${
      zone ? `<span class="r-zl"><span class="r-zkey"></span>too small to bet</span>` : ""
    }</div>
  </figure>`;
}

function candidateWords(c: RocketCandidate): string {
  return `${num(c.contracts, 0)} USO ${c.right === "C" ? "call" : "put"}${c.contracts === 1 ? "" : "s"}, strike ${num(
    c.strike,
    Number.isInteger(c.strike) ? 0 : 1,
  )}, expiring ${day(c.expiry)} (${c.dte} days, delta ${num(Math.abs(c.delta), 2)}), at ${num(c.fill, 2)} a share: ${usd(
    c.cost,
    2,
  )}, ${percent(c.share_of_equity ?? null, 0)} of the account`;
}

/** The same order on a real account: Alpaca's API (its paper account is free) and Robinhood's form. */
function copyBlock(r: Replicate | null | undefined, what: string): string {
  if (!r) return "";
  return `<details class="t-details r-copy">
    <summary><b>${escapeHtml(what)}</b> <span class="t-dim">the paper book's order, as Alpaca's API and Robinhood's form take it</span></summary>
    <p>Alpaca, options level 2 (its paper account and API are free): <code>POST /v2/orders</code> with</p>
    <pre class="r-json">${escapeHtml(JSON.stringify(r.alpaca, null, 1))}</pre>
    <p>Robinhood, options level 2: ${escapeHtml(r.robinhood)}.</p>
    <p class="t-dim">The limit is where the paper book filled, a quarter of the spread inside a quote that is fifteen minutes late:
    a real order can fill elsewhere or not at all. A study on paper, not advice.</p>
  </details>`;
}

function positionLine(p: BookPosition): string {
  const delta = p.delta === null || p.delta === undefined ? null : Math.abs(p.delta);
  const left = p.days_left === null || p.days_left === undefined ? "" : ` (${p.days_left} day${p.days_left === 1 ? "" : "s"} left)`;
  const moves =
    delta === null || p.exposure === null || p.exposure === undefined
      ? ""
      : ` With a delta of ${num(delta, 2)} it moves like ${leverage(Math.abs(p.exposure))} the account held ${
          p.exposure >= 0 ? "long" : "short"
        } in USO.`;
  return `<li>${escapeHtml(positionWords(p))}${left}: paid ${num(p.avg_price, 2)} a share, ${usd(p.cost, 2)} with the fees; now
    ${num(p.last_price, 2)}${p.mark_stale ? " (a stale mark)" : ""}, worth ${usd(p.notional, 2)}: ${toneSpan(
      p.unrealized,
      signedUsd(p.unrealized, 2),
    )} open.${moves}${p.awaiting_close ? " Expired: it is settled on the fund's official close." : ""}</li>`;
}

function lotteryNow(b: Book, h: BookHistory | null): string {
  const m = rocketMonitor(b);
  const lines: string[] = [];
  const held = b.positions ?? [];
  for (const p of held) lines.push(positionLine(p));
  if (held.length) {
    const side = held[0]!.side === "short" ? "short" : "long";
    lines.push(`<li class="t-dim">It is sold when the Dynamic stops pointing ${side}, or with ${String(
      b.rules.roll_days ?? 7,
    )} days left, then replaced if the signal still holds. There is no profit target: a 10x can only come from a bet left to run.</li>`);
  } else {
    lines.push(`<li>No option open: the account is ${usd(b.equity, 2)} in cash.</li>`);
  }
  if (b.status === "dead") {
    lines.push(`<li class="warn">The life is over: at ${usd(b.equity, 2)} the account is at or below ${percent(
      (b.rules.floor as number | undefined) ?? 0.05,
      0,
    )} of its start, and no bet opens until a new life.</li>`);
  } else if (m) {
    lines.push(`<li>Today's signal: ${escapeHtml(m.signal.text)}</li>`);
    if (!held.length && m.stuck) {
      lines.push(`<li class="warn">The account is too small to bet: the cheapest option the rule admits costs ${usd(
        m.cheapest_cost,
        2,
      )}, more than half of ${usd(b.equity, 2)}. A new life starts it again at ${usd(b.initial_capital, 0)}.</li>`);
    } else if (!held.length && m.candidate) {
      lines.push(
        m.candidate_needs_kalshi
          ? `<li>On the latest quotes the rule would buy ${escapeHtml(candidateWords(m.candidate))}, if Kalshi agreed: today it does not, so no bet opens.</li>`
          : `<li>On the latest quotes the rule would buy ${escapeHtml(candidateWords(m.candidate))}.</li>`,
      );
    } else if (!held.length && m.no_candidate_reason) {
      lines.push(`<li>Nothing to buy: ${escapeHtml(m.no_candidate_reason)}.</li>`);
    }
  }
  const last = b.last_decision
    ? { ts: b.last_decision.ts, text: b.last_decision.rationale ?? "" }
    : h?.decisions[0]
      ? { ts: h.decisions[0].ts, text: h.decisions[0].text }
      : null;
  const quote = last
    ? `<figure class="b-quote"><figcaption>The last decision, ${escapeHtml(when(last.ts))} (${escapeHtml(zoneName())})</figcaption>
        <blockquote>${escapeHtml(last.text)}</blockquote></figure>`
    : `<p class="t-dim">No decision yet.</p>`;
  const timing = `<p class="t-dim">It decides once a day, at the first tick after ${escapeHtml(
    String(b.rules.decision_time_ny ?? "15:00"),
  )} New York, on USO quotes less than ${String(b.rules.max_quote_age_minutes ?? 45)} minutes old${
    m?.quote_ts ? `; the latest are from ${escapeHtml(when(m.quote_ts))}` : ""
  }.</p>`;
  const copy = held.length ? copyBlock(held[0]!.replicate, "Copy this bet on a real account") : m?.candidate ? copyBlock(m.candidate.replicate, "Copy the bet the rule would place") : "";
  return section("Now", `<ul class="b-now">${lines.join("")}</ul>${copy}${quote}${timing}`, "", "now");
}

/** The button that starts a new life (the rocket-reset workflow). One press when the life is over; two otherwise,
 *  so a stray tap does not archive a life that is still running. */
function newLifeSection(b: Book): string {
  const m = rocketMonitor(b);
  const over = b.status === "dead" || !!m?.stuck;
  const lead =
    b.status === "dead"
      ? `This life is over: ${usd(b.equity, 2)} is at or below ${percent((b.rules.floor as number | undefined) ?? 0.05, 0)} of its start.`
      : m?.stuck
        ? `This life cannot bet any more: it is at ${num(b.multiple ?? null, 2)}x of its start.`
        : `This life is at ${num(b.multiple ?? null, 2)}x of its start. A new one archives it as it stands${
            (b.positions?.length ?? 0) > 0 ? " (the option it holds is abandoned, as in any reset)" : ""
          } and starts again.`;
  const file = b.reset_workflow ?? "rocket-reset.yml";
  return section(
    "A new life",
    `<p class="r-lead">${lead} Every life before stays in the history below.</p>
    <div class="r-reset">
      <button type="button" class="btn${over ? "" : " secondary"}" data-new-life data-over="${over ? 1 : 0}">Start a new life at ${usd(
        b.initial_capital,
        0,
      )}</button>
      <a class="btn secondary sm" href="${escapeHtml(workflowPage(file))}" target="_blank" rel="noopener noreferrer">The workflow on GitHub</a>
    </div>
    <p class="r-result" role="status" aria-live="polite"></p>
    <p class="t-dim">With the token saved on the <a href="#/reset">Reset page</a>, the button starts the workflow from here.
    Without one it opens the workflow on GitHub, where "Run workflow" starts it. The new life shows here a few minutes later.</p>`,
    "",
    "newlife",
  );
}

function wireNewLife(root: HTMLElement, b: Book): void {
  const button = root.querySelector<HTMLButtonElement>("[data-new-life]");
  const result = root.querySelector<HTMLElement>(".r-result");
  if (!button || !result) return;
  const file = b.reset_workflow ?? "rocket-reset.yml";
  const label = button.textContent ?? "";
  let armed = button.dataset.over === "1";
  button.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      button.textContent = `Press again: archive this life at ${usd(b.equity, 2)}`;
      result.textContent = "A second press starts the new life.";
      return;
    }
    const token = readToken();
    if (!token) {
      window.open(workflowPage(file), "_blank", "noopener");
      result.textContent =
        'No token is saved in this browser: the workflow is open on GitHub, where "Run workflow" starts the new life.';
      return;
    }
    button.disabled = true;
    result.textContent = "Starting the workflow…";
    try {
      const res = await fetch(workflowDispatch(file), {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main" }),
      });
      if (res.status === 204) {
        button.textContent = "New life started";
        result.textContent = "Started. The workflow archives this life and pushes the new one: it shows here in a few minutes.";
        return;
      }
      const text = await res.text();
      result.textContent = `GitHub answered ${res.status}: ${text.slice(0, 200)}`;
    } catch (err) {
      result.textContent = `The request did not go through: ${err instanceof Error ? err.message : String(err)}`;
    }
    button.disabled = false;
    armed = button.dataset.over === "1";
    button.textContent = label;
  });
}

/** What a model of the rule gives a life: how 811 three-month windows since 2011 ended, as one bar. */
function oddsSection(b: Book): string {
  const o = b.odds;
  if (!o || !o.outcomes?.length) return "";
  const segs = o.outcomes
    .map((x) => `<span class="r-seg r-${escapeHtml(x.id)}" style="flex-grow:${Number(x.share) || 0}"></span>`)
    .join("");
  const legend = o.outcomes
    .map(
      (x) =>
        `<li><span class="r-key r-${escapeHtml(x.id)}" aria-hidden="true"></span><b>${percent(x.share, 1)}</b> ${escapeHtml(x.label)}</li>`,
    )
    .join("");
  const label = o.outcomes.map((x) => `${percent(x.share, 1)} ${x.label}`).join(", ");
  return section(
    "The odds",
    `<figure class="r-odds">
      <figcaption>How ${num(o.windows, 0)} three-month lives ended in a model of this rule, ${escapeHtml(o.span)} (≈)</figcaption>
      <div class="r-bar" role="img" aria-label="${escapeHtml(label)}">${segs}</div>
      <ul class="r-legend">${legend}</ul>
    </figure>
    <p>Every 10x began in an oil crash bought with puts: ${escapeHtml(o.ten_x_episodes.join(", "))}. The typical life ended at
    ${num(o.median_multiple, 2)}x of its start, the best at ${num(o.best_multiple, 0)}x. In 2026, none of the ${num(
      o.windows_2026,
      0,
    )} windows doubled (median ${num(o.median_multiple_2026, 2)}x): the options cost about as much as the moves that came.</p>
    <p class="t-dim">≈ A model, not a record: ${escapeHtml(o.source)}. The real book buys whole contracts, so its bets are
    lumpier than the model's and a small account stops betting before it is wiped out.</p>`,
    "a model of the rule, approximate",
    "odds",
  );
}

// ---------------------------------------------------------------------------------------------- now
function structureLine(s: OptionStructure): string {
  return `<li>${escapeHtml(structureWords(s))}, ${s.contracts} contract${s.contracts === 1 ? "" : "s"}: sold for ${num(
    s.credit,
    2,
  )} a share, now worth ${num(s.mark, 2)}${s.mark_stale ? " (a stale mark)" : ""}, so ${toneSpan(
    s.unrealized ?? null,
    signedUsd(s.unrealized ?? null, 2),
  )} open. From the open to expiry it can lose at most ${usd(s.max_loss, 0)} and gain at most ${usd(s.max_gain, 0)};
    breakeven ${num(s.breakeven, 2)}${s.awaiting_close ? ". Expired: waiting for the official close" : ""}.</li>`;
}

/** For a Claude book: what the analyst's reading says of the book's market - the forecast, its confidence, the
 *  reasons and the main risk in the model's words - or why there is no reading. */
function analystView(b: Book, doc: DeskDoc): string {
  const a = doc.analyst;
  if (b.forecast_family !== "claude" || !a) return "";
  const r = a.reading;
  const link = `<a href="#/signals?at=claude">${r ? "The whole reading, its brief and its sources" : "How the analyst works"}</a>`;
  if (a.error) return `<p class="warn">The analyst's archive could not be read on this export. ${link}.</p>`;
  if (!r || a.state !== "on" || !a.today || a.today.stale) {
    return `<p class="b-analyst">The Claude analyst: ${analystStatus(a)} ${link}.</p>`;
  }
  const parts = Object.keys(b.sleeves ?? {}).map((m) => {
    const x = r.markets[m];
    const name = analystMarketName(m);
    if (!x) return `<p class="b-analyst">The reading of ${escapeHtml(longDay(r.day))} gave no forecast for ${escapeHtml(name)}: the book holds what it has.</p>`;
    const why = x.reasons.length ? `<ul class="a-reasons">${x.reasons.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul>` : "";
    return `<p class="b-analyst">The Claude analyst's reading of ${escapeHtml(longDay(r.day))}: ${escapeHtml(name)} <b>${toneSpan(
      x.forecast,
      signed(x.forecast, 0),
    )}</b> out of 20${x.confidence ? `, confidence ${escapeHtml(x.confidence)}` : ""}. Why, in the model's words:</p>${why}${
      x.risk ? `<p class="b-analyst">The main risk to the view: ${escapeHtml(x.risk)}</p>` : ""
    }`;
  });
  return `${parts.join("")}<p class="t-dim">${link}. A language model's reading of the news for a paper-trading study: not advice.</p>`;
}

function nowSection(b: Book, h: BookHistory | null, doc: DeskDoc): string {
  if (b.kind === "lottery") return lotteryNow(b, h);
  const lines: string[] = [];
  for (const p of b.positions ?? []) {
    lines.push(`<li>${escapeHtml(positionWords(p))}, bought at ${num(p.avg_price, 2)} on average${
      p.opened_at ? ` from ${escapeHtml(longDay(p.opened_at))}` : ""
    }, last ${num(p.last_price, 2)}: worth ${usd(p.notional, 0)}, ${escapeHtml(exposureWords(p.exposure))} of the account;
      ${toneSpan(p.unrealized, signedUsd(p.unrealized, 2))} open.</li>`);
  }
  for (const s of b.structures ?? []) lines.push(structureLine(s));
  for (const o of b.pending ?? []) {
    lines.push(`<li class="warn">Order queued: ${escapeHtml(orderWords(o, b.unit))}, decided ${escapeHtml(when(o.decided_at))}. It fills
      at the open of the next half-hour bar.</li>`);
  }
  if (!lines.length) lines.push(`<li>In cash: no position and no order queued.</li>`);
  const last = b.last_decision
    ? { ts: b.last_decision.ts, text: b.last_decision.rationale ?? "" }
    : h?.decisions[0]
      ? { ts: h.decisions[0].ts, text: h.decisions[0].text }
      : null;
  const quote = last
    ? `<figure class="b-quote"><figcaption>The last decision, ${escapeHtml(when(last.ts))} (${escapeHtml(zoneName())})</figcaption>
        <blockquote>${escapeHtml(last.text)}</blockquote></figure>`
    : `<p class="t-dim">No decision yet.</p>`;
  const owed =
    b.decision_owed && b.kind === "linear"
      ? `<p class="t-dim">The next decision is due at ${escapeHtml(String(b.rules.decision_time_ny ?? ""))} New York, on the first tick after it.</p>`
      : "";
  return section("Now", `<ul class="b-now">${lines.join("")}</ul>${analystView(b, doc)}${quote}${owed}`, "", "now");
}

// ---------------------------------------------------------------------------------------------- equity chart
function chartSection(b: Book, h: BookHistory | null): string {
  const curve = h?.curve?.length ? h.curve : b.curve;
  if (curve.length < 2) return "";
  if (curve.every((p) => p.v === curve[0]!.v)) {
    return section(
      "Equity",
      `<p class="t-dim">The equity has not moved since the start: ${usd(curve[0]!.v, 2)} at every mark, ${escapeHtml(
        longDay(curve[0]!.t),
      )} to ${escapeHtml(longDay(curve[curve.length - 1]!.t))}.</p>`,
    );
  }
  return section(
    "Equity",
    `<div class="t-chart" id="b-equity"></div>`,
    `the dashed line is the ${usd(b.initial_capital, 0)} it started with; every half hour for the last five days, one point a day before`,
  );
}

function drawEquity(root: HTMLElement, b: Book, h: BookHistory | null): void {
  const el = root.querySelector<HTMLElement>("#b-equity");
  if (!el) return;
  const curve = h?.curve?.length ? h.curve : b.curve;
  const points = curve.map((p) => ({ t: Date.parse(p.t), v: p.v })).filter((p) => Number.isFinite(p.t));
  lineChart(el, [{ id: b.id, label: b.name, color: "var(--ink-line)", points }], {
    ariaLabel: `Equity of ${b.name}: ${usd(b.equity, 2)} now, against ${usd(b.initial_capital, 0)} at the start`,
    height: 230,
    baseline: b.initial_capital,
    area: true,
    fmt: (v) => num(v, 0),
    minStep: 1,
    readoutFmt: (v) => usd(v, 2),
    dateFmt: (t) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" }).format(t),
    readoutDate: (t) => when(new Date(t).toISOString()),
  });
}

// ---------------------------------------------------------------------------------------------- history
function more(id: string, total: number): string {
  return total > PAGE
    ? `<button type="button" class="btn secondary sm b-more" data-more="${id}">Show ${Math.min(PAGE, total - PAGE)} more of ${total - PAGE}</button>`
    : "";
}

function fillsPanel(fills: FillRow[], partial: boolean): string {
  if (!fills.length) {
    return `<div class="t-empty">No trade yet in this life of the book.</div>`;
  }
  const rows = fills
    .map(
      (f, i) => `<tr${i >= PAGE ? " hidden" : ""}>
        <td class="t-nowrap h-when">${escapeHtml(when(f.ts))}</td>
        <td class="w h-what">${escapeHtml(tradeWords(f))}</td>
        <td class="n h-a" data-l="Price">${num(f.price, 2)}</td>
        <td class="n h-b" data-l="Cost">${f.cost_bps === null || f.cost_bps === undefined ? "" : `${num(f.cost_bps, 1)} bp`}</td>
        <td class="h-c" data-l="Why">${escapeHtml(reasonWords(f.reason))}</td>
        <td class="n h-right">${f.realized_pnl ? `${toneSpan(f.realized_pnl, signedUsd(f.realized_pnl, 2))}<small class="m-l">closed</small>` : ""}</td>
        <td class="n h-d" data-l="Equity after">${
          "equity_after" in f && f.equity_after !== null && f.equity_after !== undefined ? usd(f.equity_after as number, 2) : ""
        }</td>
      </tr>`,
    )
    .join("");
  return `<div class="t-scroll"><table class="t-table b-fills" data-rows="fills">
      <thead><tr><th>When</th><th>Trade</th><th class="n">Price</th><th class="n">Cost</th><th>Why</th><th class="n">Closed P&amp;L</th><th class="n">Equity after</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${more("fills", fills.length)}
    <p class="t-dim">The price is after the spread and the slippage; the cost is how far that is from the reference price.
    A trade is shown at the time its bar opened, when it was executed.${partial ? " Only the desk's latest trades: the book's full history is not published yet." : ""}</p>`;
}

/** What the decision held and wanted, instrument by instrument: "BNO 26 to 19", "SCO 0 to 69" (a fund book's short
 *  side is shares of an inverse fund, so a decision can move two instruments at once). */
function positionChange(d: HistoryDecision, unit: string | undefined): string {
  const u = unit === "barrels" ? " bbl" : "";
  const held = (d.held ?? []).filter((x) => x.from !== null && x.to !== null);
  if (held.length) {
    const moving = held.filter((x) => x.from !== 0 || x.to !== 0);
    const shown = moving.length ? moving : held.slice(0, 1);
    return shown
      .map((x) => `${x.symbol ?? ""} ${num(x.from, 0)}${x.from === x.to ? "" : ` to ${num(x.to, 0)}`}${u}`)
      .join(", ");
  }
  const cur = d.current_units;
  const tgt = d.target_units;
  if (cur === null || cur === undefined || tgt === null || tgt === undefined) return EMPTY;
  return cur === tgt ? `${num(cur, 0)}${u}` : `${num(cur, 0)} to ${num(tgt, 0)}${u}`;
}

/** Every order the decision queued, with its instrument: "+69 SCO", "-40 BNO, +69 SCO". */
function orderText(d: HistoryDecision): string {
  const orders = (d.orders ?? []).filter((o) => o.units);
  if (orders.length) return orders.map((o) => `${signed(o.units, 0)} ${o.symbol ?? ""}`.trim()).join(", ");
  return signed(d.order_units, 0);
}

function kalshiWords(d: HistoryDecision): string {
  if (d.kalshi_change === null || d.kalshi_change === undefined) return EMPTY;
  const move = `${signed(d.kalshi_change * 100, 1)} pt`;
  return d.agree ? `${move}, agrees` : move;
}

function decisionsPanel(decisions: HistoryDecision[], unit: string | undefined, partial: boolean, lottery = false): string {
  if (!decisions.length) return `<div class="t-empty">No decision yet in this life of the book.</div>`;
  const orders = decisions.filter((d) => d.order_units).length;
  const rows = decisions
    .map((d, i) => {
      const acted = Boolean(d.order_units);
      const refused = typeof d.status === "string" && d.status.includes("refused");
      const order = acted ? `${escapeHtml(orderText(d))}${refused ? ` ${span("warn", "refused")}` : ""}` : span("t-dim", "none");
      return `<tr data-order="${acted ? 1 : 0}"${i >= PAGE ? " hidden" : ""}>
        <td class="t-nowrap h-when">${escapeHtml(when(d.ts))}</td>
        <td class="n h-a" data-l="Forecast">${toneSpan(d.forecast, signed(d.forecast, 1))}</td>
        ${
          lottery
            ? `<td class="n h-b" data-l="Kalshi">${escapeHtml(kalshiWords(d))}</td>`
            : `<td class="n h-b" data-l="Wanted">${escapeHtml(exposureWords(d.exposure))}${
                d.risk_scale !== null && d.risk_scale !== undefined && d.risk_scale < 1
                  ? `<small>risk ×${num(d.risk_scale, 2).replace(/\.?0+$/, "")}</small>`
                  : ""
              }</td>`
        }
        <td class="n h-c" data-l="Position">${lottery ? escapeHtml(d.status === "none" ? "held" : (d.status ?? "")) : escapeHtml(positionChange(d, unit))}</td>
        <td class="n h-right"><span class="m-l">order</span>${order}</td>
        <td class="w h-what">${escapeHtml(d.text ?? "")}</td>
      </tr>`;
    })
    .join("");
  return `<label class="b-check"><input type="checkbox" data-only-orders /> Only the ${orders} decision${orders === 1 ? "" : "s"} that sent an order</label>
    <div class="t-scroll"><table class="t-table t-log b-decisions" data-rows="decisions">
      <thead><tr><th>When</th><th class="n">Forecast</th><th class="n">${lottery ? "Kalshi" : "Wanted"}</th><th class="n">${
        lottery ? "Action" : "Position"
      }</th><th class="n">Order</th><th>In the book's words</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${more("decisions", decisions.length)}
    <p class="t-dim">One decision a day, written whether or not an order follows. The forecast runs from ${MINUS}20 to +20
    (+10 is a normal conviction); ${
      lottery
        ? `it is the Dynamic's, and "Kalshi" is the week's change of the de-escalation index in points, with whether it agreed.`
        : `"wanted" is the exposure the book's rule asks for, as a multiple of the account.`
    }${partial ? " Only the desk's latest decisions: the book's full history is not published yet." : ""}</p>`;
}

const END_WORDS: Record<string, string> = {
  reset_manual: "reset by the owner",
  dead: "wiped out",
  reset: "reset",
};

function livesPanel(b: Book, h: BookHistory | null): string {
  const lives: BookLife[] = h?.epochs ?? [];
  const current = h?.epoch ?? b.epoch;
  const ended = lives.filter((l) => l.ended_at);
  const nowRow = ended.some((l) => l.epoch === current)
    ? ""
    : `<tr><th scope="row">Life ${current}, this one</th><td data-l="From">${escapeHtml(longDay(h?.started_at ?? b.started_at))}</td>
        <td data-l="To">running</td><td class="n" data-l="Start">${usd(b.initial_capital, 2)}</td><td class="n" data-l="End">${usd(
          b.equity,
          2,
        )} so far</td><td class="n" data-l="Trades">${num(h?.summary.n_fills ?? b.n_fills ?? null, 0)}</td><td data-l="Ended">${EMPTY}</td></tr>`;
  const rows = ended
    .slice()
    .reverse()
    .map(
      (l) => `<tr><th scope="row">Life ${l.epoch ?? EMPTY}</th>
        <td data-l="From">${escapeHtml(longDay(l.started_at))}</td><td data-l="To">${escapeHtml(longDay(l.ended_at))}</td>
        <td class="n" data-l="Start">${usd(l.start_equity, 2)}</td><td class="n" data-l="End">${usd(l.end_equity, 2)}</td>
        <td class="n" data-l="Trades">${num(l.n_trades, 0)}</td><td data-l="Ended">${escapeHtml(END_WORDS[l.end_reason ?? ""] ?? l.end_reason ?? EMPTY)}</td></tr>`,
    )
    .join("");
  const note = ended.length
    ? "A reset closes what the book holds and starts a new life at the starting capital; the history above is this life's."
    : "This is the book's first life: it has never been reset or wiped out.";
  return `<div class="t-scroll"><table class="t-table b-lives">
      <thead><tr><th>Life</th><th>From</th><th>To</th><th class="n">Start</th><th class="n">End</th><th class="n">Trades</th><th>Ended</th></tr></thead>
      <tbody>${nowRow}${rows}</tbody></table></div>
    <p class="t-dim">${note}</p>`;
}

function historySection(b: Book, h: BookHistory | null, doc: DeskDoc, warning: string): string {
  const partial = h === null;
  const fills = h ? h.fills : doc.fills.filter((f) => f.book === b.id);
  const decisions: HistoryDecision[] = h ? h.decisions : doc.decisions.filter((d) => d.book === b.id);
  // the lives the panel lists: the ended ones, and this one unless it has ended too (a book wiped out, not yet reset)
  const ended = (h?.epochs ?? []).filter((l) => l.ended_at);
  const current = h?.epoch ?? b.epoch;
  const lives = ended.length + (ended.some((l) => l.epoch === current) ? 0 : 1);
  const tabs: [string, string, number][] = [
    ["fills", "Trades", fills.length],
    ["decisions", "Decisions", decisions.length],
    ["lives", "Lives", lives],
  ];
  const first = fills.length ? "fills" : "decisions";
  const unit = h?.unit ?? b.unit;
  const since = h?.started_at ?? b.started_at;
  const panels: Record<string, string> = {
    fills: fillsPanel(fills, partial),
    decisions: decisionsPanel(decisions, unit, partial, b.kind === "lottery"),
    lives: livesPanel(b, h),
  };
  const body = `${warning ? `<p class="warn">${escapeHtml(warning)}</p>` : ""}<div class="tabs" role="tablist" aria-label="History">${tabs
    .map(
      ([id, label, n]) =>
        `<button type="button" role="tab" id="tab-${id}" aria-controls="panel-${id}" aria-selected="${id === first}" tabindex="${
          id === first ? 0 : -1
        }">${label} <span class="seg-n">${n}</span></button>`,
    )
    .join("")}</div>
    ${tabs
      .map(
        ([id]) =>
          `<div class="tabpanel" role="tabpanel" id="panel-${id}" aria-labelledby="tab-${id}" tabindex="0"${id === first ? "" : " hidden"}>${panels[id]}</div>`,
      )
      .join("")}`;
  return section(
    "History",
    body,
    `${since ? `this life of the book, since ${escapeHtml(longDay(since))}; ` : ""}newest first, in your time zone (${escapeHtml(zoneName())})`,
    "history",
  );
}

function wireHistory(root: HTMLElement): void {
  const list = root.querySelector<HTMLElement>(".tabs");
  if (list) {
    const tabs = [...list.querySelectorAll<HTMLButtonElement>("[role=tab]")];
    const select = (tab: HTMLButtonElement, focus: boolean): void => {
      for (const t of tabs) {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = root.querySelector<HTMLElement>(`#${t.getAttribute("aria-controls")}`);
        if (panel) panel.hidden = !on;
      }
      if (focus) tab.focus();
    };
    list.addEventListener("click", (ev) => {
      const tab = (ev.target as HTMLElement).closest<HTMLButtonElement>("[role=tab]");
      if (tab) select(tab, false);
    });
    list.addEventListener("keydown", (ev: KeyboardEvent) => {
      const i = tabs.findIndex((t) => t === document.activeElement);
      if (i < 0) return;
      const step = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
      if (ev.key === "Home") select(tabs[0]!, true);
      else if (ev.key === "End") select(tabs[tabs.length - 1]!, true);
      else if (step) select(tabs[(i + step + tabs.length) % tabs.length]!, true);
      else return;
      ev.preventDefault();
    });
  }
  // "show more": the next page of rows of that table, counting only the rows the filter lets through
  const visibleRows = (table: HTMLElement): HTMLTableRowElement[] => {
    const only = table.dataset.rows === "decisions" && root.querySelector<HTMLInputElement>("[data-only-orders]")?.checked;
    return [...table.querySelectorAll<HTMLTableRowElement>("tbody tr")].filter((r) => !only || r.dataset.order === "1");
  };
  const refreshMore = (name: string): void => {
    const table = root.querySelector<HTMLElement>(`[data-rows="${name}"]`);
    const button = root.querySelector<HTMLButtonElement>(`[data-more="${name}"]`);
    if (!table || !button) return;
    const rows = visibleRows(table);
    const hidden = rows.filter((r) => r.hidden).length;
    button.hidden = hidden === 0;
    button.textContent = `Show ${Math.min(PAGE, hidden)} more of ${hidden}`;
  };
  root.addEventListener("click", (ev) => {
    const button = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-more]");
    if (!button) return;
    const name = button.dataset.more!;
    const table = root.querySelector<HTMLElement>(`[data-rows="${name}"]`);
    if (!table) return;
    visibleRows(table)
      .filter((r) => r.hidden)
      .slice(0, PAGE)
      .forEach((r) => {
        r.hidden = false;
      });
    refreshMore(name);
  });
  const only = root.querySelector<HTMLInputElement>("[data-only-orders]");
  only?.addEventListener("change", () => {
    const table = root.querySelector<HTMLElement>(`[data-rows="decisions"]`);
    if (!table) return;
    let shown = 0;
    for (const r of table.querySelectorAll<HTMLTableRowElement>("tbody tr")) {
      const passes = !only.checked || r.dataset.order === "1";
      r.hidden = !passes || shown >= PAGE;
      if (passes && shown < PAGE) shown += 1;
    }
    refreshMore("decisions");
  });
  refreshMore("fills");
  refreshMore("decisions");
}

// ---------------------------------------------------------------------------------------------- rules
function rulesSection(b: Book): string {
  const stress = b.stress
    ? `<p class="t-dim">On a gap of the instrument, at today's position and before any stop: ${Object.entries(b.stress)
        .map(([move, v]) => `${escapeHtml(move.replace("-", MINUS))} gives ${signedPercent(v, 1)}`)
        .join(", ")}.</p>`
    : "";
  const liq =
    b.liquidation_price !== null && b.liquidation_price !== undefined
      ? `<p>Forced liquidation price: <b>${num(b.liquidation_price, 2)}</b>, where the broker would close the position because the margin is no longer enough.</p>`
      : "";
  const forecast =
    b.forecast_family === "kalshi"
      ? `<a href="#/signals?at=kalshi">The Kalshi indices it reads</a>`
      : b.forecast_family === "claude"
        ? `<a href="#/signals?at=claude">The Claude analyst it reads</a>`
        : b.kind === "options"
          ? `<a href="#/signals?at=options">The option quotes it reads</a>`
          : b.kind === "lottery"
            ? `<a href="#/signals?at=forecast">The Dynamic's forecast</a> and <a href="#/signals?at=kalshi">the Kalshi index</a> it reads`
            : `<a href="#/signals?at=forecast">The forecast it reads</a>`;
  const backtest =
    b.kind === "lottery"
      ? `It has no backtest of real quotes, and none can exist: no free history of option quotes does. The odds on this page are a model's (≈). It is out of every total and is never promoted. Fees so far: ${usd(
          b.fees ?? null,
          2,
        )}.`
      : b.forecast_family === "claude"
      ? "It has no backtest, and none can exist: a language model knows the prices a replay of the past would ask it to forecast."
      : b.backtested === false || isSpeculative(b)
        ? "It has no backtest: its signal has no history the replay could run on."
        : `<a href="#/backtest">Its backtest</a> replays these rules on decades of real prices.`;
  return `<details class="t-details b-rules">
    <summary><b>Rules and costs</b> <span class="t-dim">what the book is allowed to do, and what trading cost it</span></summary>
    <p>${ruleLine(b)}</p>
    ${allocatorLine(b)}
    ${b.kind === "linear" ? `<p class="t-dim">${costsLine(b)} Fills in this life: ${num(b.n_fills ?? null, 0)}.</p>` : ""}
    ${stress}
    ${liq}
    ${b.last_roll ? `<p class="t-dim">Last roll: ${escapeHtml(b.last_roll)}.</p>` : ""}
    ${b.note ? `<p class="t-dim">${escapeHtml(b.note)}</p>` : ""}
    ${b.rules.short_note ? `<p class="t-dim">${escapeHtml(String(b.rules.short_note))}</p>` : ""}
    <p>${forecast}. ${backtest}</p>
  </details>`;
}

/** The book's history file, when it belongs to the life desk.json shows (a reset since, or a copy bundled with the
 *  site at its last deploy, would be another life), with a warning when it is older than the rest of the page. */
function historyFor(b: Book, doc: DeskDoc, loaded: BookHistory | null): { h: BookHistory | null; warning: string } {
  if (!loaded || loaded.id !== b.id) {
    return { h: null, warning: "" };
  }
  if (loaded.epoch !== b.epoch) {
    return {
      h: null,
      warning: `The book's history file is of its life ${loaded.epoch}, and the book is in life ${b.epoch}: it is not shown until the next export rewrites it. The rows below are the desk's latest.`,
    };
  }
  const lag = (Date.parse(doc.generated_at) - Date.parse(loaded.generated_at)) / 60000;
  if (Number.isFinite(lag) && lag > 90) {
    return {
      h: loaded,
      warning: `This history was published ${when(loaded.generated_at)}, before the rest of the page: the latest trades and decisions may be missing.`,
    };
  }
  return { h: loaded, warning: "" };
}

export async function renderBook(el: HTMLElement, id: string): Promise<void> {
  const [loaded, hist] = await Promise.all([load<DeskDoc>("desk.json"), load<BookHistory>(`books/${encodeURIComponent(id)}.json`)]);
  if (!el.isConnected) return; // the reader went elsewhere while the data came: nothing to draw
  const back = `<a class="d-back" href="#/" data-back>${chevron("left")}<span>Desk</span></a>`;
  const doc = loaded.data;
  if (!doc) {
    el.innerHTML = `<div class="term">${back}<div class="t-empty">The books are unavailable: <code>desk.json</code> does not answer${
      loaded.error ? ` (${escapeHtml(loaded.error)})` : ""
    }.</div></div>`;
    return;
  }
  const b = doc.books.find((x) => x.id === id);
  if (!b) {
    el.innerHTML = `<div class="term">${back}<div class="t-empty">There is no book called “${escapeHtml(id)}” on the desk.
      <a href="#/">See every book</a>.</div></div>`;
    return;
  }
  document.title = `${b.name} · Brent Desk`;
  const { h, warning } = historyFor(b, doc, hist.data);
  el.innerHTML = `<div class="term bookpage">
    ${doc.sample ? '<div class="banner sample">Sample data: this is not the real state of the books.</div>' : ""}
    ${statusLine(doc, loaded.origin)}
    ${back}
    ${head(b)}
    ${figures(b)}
    ${nowSection(b, h, doc)}
    ${b.kind === "lottery" ? `${newLifeSection(b)}${oddsSection(b)}` : ""}
    ${chartSection(b, h)}
    ${historySection(b, h, doc, warning)}
    ${rulesSection(b)}
  </div>`;
  const root = el.querySelector<HTMLElement>(".bookpage")!;
  wireHistory(root);
  if (b.kind === "lottery") wireNewLife(root, b);
  drawEquity(root, b, h);
}
