/** The desk: what the paper books hold and how each group of them is doing, at a glance.
 *
 * One question per section, top to bottom. Is anything open: the sentence at the top. What exactly: the open
 * positions, the option spreads and the orders waiting for the next open. How each portfolio is doing: the
 * groups of books, filtered by a click, each book a link to its own page with its whole history. How the money
 * moved since the start: one line per group. What happened last: the latest fills. The signals the books read
 * are on their own page. Every figure is the engine's (`desk.json`); the page only counts rows.
 */
import { lineChart, lineLegend, type LineSeries } from "../charts/lines";
import { load } from "../data";
import type { Book, BookHistory, BookPosition, DeskDoc, FillRow, OptionStructure, PortfolioGroup } from "../desk";
import {
  GROUP_WORDS,
  bookState,
  chevron,
  exposureBar,
  exposureWords,
  fillWords,
  groupColor,
  groupLabel,
  groupOf,
  holdingShort,
  orderWords,
  positionWords,
  reasonWords,
  section,
  statusLine,
  structureWords,
  toneSpan,
} from "../deskview";
import {
  day,
  escapeHtml,
  longDay,
  num,
  shortDay,
  signed,
  signedPercent,
  signedUsd,
  usd,
  when,
  zoneName,
} from "../format";

const bookHref = (id: string): string => `#/book/${encodeURIComponent(id)}`;

/** A position or an open spread: what the headline counts and the filter "With a position" shows. A book with
 *  only an order in the queue holds nothing yet (its order is listed under the open positions). */
const isHolding = (b: Book): boolean => (b.positions?.length ?? 0) > 0 || (b.structures?.length ?? 0) > 0;

function vehicleShort(b: Book): string {
  const v = b.vehicle === "MCL" ? "/MCL" : b.vehicle;
  const via = b.rules?.short_via;
  return via ? `${v}, short via ${String(via)}` : v;
}

/** The groups as the engine sent them; a `desk.json` written before the groups existed gets them from each
 *  book's own group, without totals (they come with the next export). */
function groupsOf(doc: DeskDoc): PortfolioGroup[] {
  if (doc.portfolios?.length) return doc.portfolios;
  const ids: string[] = [];
  for (const b of doc.books) if (!ids.includes(groupOf(b))) ids.push(groupOf(b));
  return ids.map((id) => {
    const members = doc.books.filter((b) => groupOf(b) === id);
    return {
      id,
      label: groupLabel(id),
      note: "",
      books: members.map((b) => b.id),
      n_books: members.length,
      holding: members.filter(isHolding).map((b) => b.id),
      queued: members.filter((b) => (b.pending?.length ?? 0) > 0).map((b) => b.id),
      equity: null,
      initial_capital: null,
      pnl: null,
      pnl_pct: null,
      pnl_day: null,
      pnl_day_pct: null,
      curve: [],
    };
  });
}

// ---------------------------------------------------------------------------------------------- the headline
function groupPhrase(g: PortfolioGroup): string {
  const w = GROUP_WORDS[g.id] ?? { noun: `the ${escapeHtml(g.label)}`, verb: "are" };
  if (g.pnl === null || g.pnl === undefined) return `${w.noun}: no total yet`;
  if (g.pnl === 0) return `${w.noun} ${w.verb} flat`;
  const up = g.pnl > 0;
  const since = g.curve.length ? ` since ${escapeHtml(longDay(g.curve[0]!.t))}` : "";
  return `${w.noun} ${w.verb} ${up ? "up" : "down"} <b class="${up ? "up" : "down"}">${usd(Math.abs(g.pnl), 2)}</b>
    <span class="d-pct">(${signedPercent(g.pnl_pct, 2)})</span>${since}`;
}

function listWords(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function hero(doc: DeskDoc, groups: PortfolioGroup[]): string {
  const n = doc.books.length;
  const open = doc.books.filter(isHolding).length;
  const queued = doc.books.reduce((k, b) => k + (b.pending?.length ?? 0), 0);
  const headline =
    open === 0
      ? `All ${n} books are in cash.`
      : open === n
        ? `All ${n} books hold a position.`
        : `${open} of ${n} books hold a position.`;
  const lede = listWords(groups.map(groupPhrase));
  const waiting = queued
    ? ` ${queued === 1 ? "One order waits" : `${queued} orders wait`} for the next half-hour bar to fill.`
    : "";
  const capital = doc.books[0]?.initial_capital;
  return `<header class="d-hero">
    <h1 class="d-headline">${escapeHtml(headline)}</h1>
    <p class="d-lede">${lede.charAt(0).toUpperCase()}${lede.slice(1)}.${waiting}</p>
    <p class="d-fine">Each book is its own paper account${
      capital ? `, started with ${usd(capital, 0)}` : ""
    }, trading real prices with real costs. No real money, no broker.</p>
  </header>`;
}

// ---------------------------------------------------------------------------------------------- open positions
function bookCell(b: Book): string {
  return `<th scope="row"><a href="${bookHref(b.id)}">${escapeHtml(b.name)}</a><small>${escapeHtml(groupLabel(groupOf(b)))}</small></th>`;
}

function positionRow(b: Book, p: BookPosition): string {
  return `<tr data-href="${bookHref(b.id)}">
    ${bookCell(b)}
    <td class="w" data-l="Position">${escapeHtml(positionWords(p))}<small>average ${num(p.avg_price, 2)}, last ${num(p.last_price, 2)}</small></td>
    <td class="n d-exp" data-l="Exposure"><span class="xwrap">${exposureBar(p.exposure)}<span>${escapeHtml(exposureWords(p.exposure))}</span></span></td>
    <td class="n" data-l="Value">${usd(p.notional, 0)}</td>
    <td class="n" data-l="Open P&amp;L">${toneSpan(p.unrealized, signedUsd(p.unrealized, 2))}</td>
    <td class="n" data-l="Since">${escapeHtml(shortDay(p.opened_at))}</td>
    <td class="d-go" aria-hidden="true">${chevron("right")}</td>
  </tr>`;
}

function structureRow(b: Book, s: OptionStructure): string {
  const contracts = `${s.contracts} contract${s.contracts === 1 ? "" : "s"}`;
  return `<tr data-href="${bookHref(b.id)}">
    ${bookCell(b)}
    <td class="w" data-l="Position">${escapeHtml(structureWords(s))}<small>${contracts}, sold for ${num(s.credit, 2)}, now ${num(s.mark, 2)}${
      s.mark_stale ? ", a stale mark" : ""
    }${s.awaiting_close ? ", expired: waiting for the official close" : ""}</small></td>
    <td class="n d-exp" data-l="Exposure"><span class="xwrap">${
      s.max_loss_now === null || s.max_loss_now === undefined ? `at most ${usd(s.max_loss, 0)} to lose` : `at most ${usd(s.max_loss_now, 0)} more to lose`
    }</span></td>
    <td class="n d-novalue" data-l="Value"></td>
    <td class="n" data-l="Open P&amp;L">${toneSpan(s.unrealized ?? null, signedUsd(s.unrealized ?? null, 2))}</td>
    <td class="n" data-l="Since">${escapeHtml(shortDay(s.opened_at))}</td>
    <td class="d-go" aria-hidden="true">${chevron("right")}</td>
  </tr>`;
}

function queuedLine(b: Book, o: NonNullable<Book["pending"]>[number]): string {
  return `<li><a href="${bookHref(b.id)}">${escapeHtml(b.name)}</a>: ${escapeHtml(orderWords(o, b.unit))}, decided ${escapeHtml(
    when(o.decided_at),
  )}. It fills at the open of the next half-hour bar.</li>`;
}

function openPositions(doc: DeskDoc): string {
  const rows: string[] = [];
  for (const b of doc.books) {
    for (const p of b.positions ?? []) rows.push(positionRow(b, p));
    for (const s of b.structures ?? []) rows.push(structureRow(b, s));
  }
  const queued = doc.books.flatMap((b) => (b.pending ?? []).map((o) => queuedLine(b, o)));
  const table = rows.length
    ? `<div class="t-scroll"><table class="t-table d-positions">
        <thead><tr><th>Book</th><th>Position</th><th class="n">Exposure</th><th class="n">Value</th><th class="n">Open P&amp;L</th><th class="n">Since</th><th class="d-go"></th></tr></thead>
        <tbody>${rows.join("")}</tbody></table></div>`
    : `<div class="t-empty">No book holds a position: all ${doc.books.length} are in cash.</div>`;
  const orders = queued.length
    ? `<h3 class="t-sub">Orders waiting</h3><ul class="d-orders">${queued.join("")}</ul>`
    : "";
  return section(
    "Open positions",
    `${table}${orders}`,
    "exposure: what the position is worth as a multiple of its book's account (the bar is full at 1x), an inverse fund counted twice",
    "positions",
  );
}

// ---------------------------------------------------------------------------------------------- portfolios
const FILTER_KEY = "show";

function bookRow(b: Book): string {
  const st = bookState(b);
  const options = b.kind === "options";
  const exposure = options
    ? `<span>${b.max_loss_open ? `at most ${usd(b.max_loss_open, 0)} more to lose` : "nothing at risk"}</span>`
    : `${exposureBar(b.exposure)}<span>${escapeHtml(exposureWords(b.exposure))}</span>`;
  const holding = `${escapeHtml(holdingShort(b))}${st.quiet ? "" : ` <span class="t-dot ${st.cls}">${escapeHtml(st.text)}</span>`}`;
  const quiet = isHolding(b) || (b.pending?.length ?? 0) > 0 ? "" : " quiet";
  const label = `${b.name}: ${holdingShort(b)}; equity ${usd(b.equity, 2)}, ${signedUsd(b.pnl_total, 2)} since the start`;
  return `<li data-holding="${isHolding(b) ? 1 : 0}">
    <a class="d-row" href="${bookHref(b.id)}" aria-label="${escapeHtml(label)}">
      <span class="c-name"><b>${escapeHtml(b.name)}</b><small>${escapeHtml(vehicleShort(b))}</small></span>
      <span class="c-hold${quiet}">${holding}</span>
      <span class="c-exp${quiet}">${exposure}</span>
      <span class="c-eq"><span class="m-l">Equity</span>${usd(b.equity, 2)}</span>
      <span class="c-pnl"><span class="m-l">Since the start</span>${toneSpan(b.pnl_total, signedUsd(b.pnl_total, 2))}<small>${signedPercent(
        b.pnl_total_pct,
        2,
      )}</small></span>
      <span class="c-day"><span class="m-l">Today</span>${toneSpan(b.pnl_day_pct, signedPercent(b.pnl_day_pct, 2))}</span>
      <span class="c-go">${chevron("right")}</span>
    </a>
  </li>`;
}

function groupBlock(doc: DeskDoc, g: PortfolioGroup, ids: string[]): string {
  const byId = new Map(doc.books.map((b) => [b.id, b]));
  const members = g.books.map((id) => byId.get(id)).filter((b): b is Book => !!b);
  const figures =
    g.equity === null || g.equity === undefined
      ? `<span class="t-dim">Totals come with the next export.</span>`
      : `<span><span class="k">Equity</span> <b>${usd(g.equity, 2)}</b></span>
        <span><span class="k">Since the start</span> ${toneSpan(g.pnl, `<b>${signedUsd(g.pnl, 2)}</b>`)} <span class="t-dim">${signedPercent(
          g.pnl_pct,
          2,
        )}</span></span>
        <span><span class="k">Today</span> ${toneSpan(g.pnl_day, signedUsd(g.pnl_day, 2))}</span>`;
  const holding = g.holding.length;
  return `<section class="d-group" data-group="${escapeHtml(g.id)}" aria-labelledby="g-${escapeHtml(g.id)}">
    <header class="d-ghead">
      <h3 id="g-${escapeHtml(g.id)}"><span class="t-key" style="--sw:${groupColor(g.id, ids)}"></span>${escapeHtml(g.label)}
        <span class="d-count">${g.n_books} book${g.n_books === 1 ? "" : "s"}, ${holding ? `${holding} with a position` : "all in cash"}</span></h3>
      <div class="d-gfig">${figures}</div>
      ${g.note ? `<p class="d-gnote">${escapeHtml(g.note.charAt(0).toUpperCase() + g.note.slice(1))}.</p>` : ""}
      ${g.id === "kalshi" ? watchLine(doc) : ""}
    </header>
    <div class="d-colhead" aria-hidden="true"><span>Book</span><span>Holding</span><span>Exposure</span><span>Equity</span><span>Since the start</span><span>Today</span><span></span></div>
    <ul class="d-list">${members.map(bookRow).join("")}</ul>
  </section>`;
}

/** The unusual-activity watch on Kalshi's event markets, in one line under the Kalshi books: how many alerts the
 *  last week raised and on what, with the way to the alerts themselves. No book trades on it. */
function watchLine(doc: DeskDoc): string {
  const pm = doc.prediction_markets;
  if (!pm?.watch || !Object.keys(pm.watch).length) return "";
  const alerts = pm.alerts ?? [];
  const link = `<a href="#/signals?at=alerts">${alerts.length ? "See them" : "The rule"}</a>`;
  if (!alerts.length) return `<p class="d-watch">No unusual activity on Kalshi's event markets in the last week. ${link}.</p>`;
  const topics = [...new Set(alerts.map((a) => a.group))].join(", ");
  return `<p class="d-watch"><span class="t-dot warn">Unusual activity on Kalshi</span> ${alerts.length} alert${
    alerts.length === 1 ? "" : "s"
  } in the last week (${escapeHtml(topics)}): a large move on unusual volume, a footprint and never a verdict. ${link}.</p>`;
}

function filterBar(doc: DeskDoc, groups: PortfolioGroup[], current: string): string {
  const options: [string, string, number][] = [
    ["all", "All", doc.books.length],
    ["holding", "With a position", doc.books.filter(isHolding).length],
    ...groups.map((g): [string, string, number] => [g.id, g.label, g.n_books]),
  ];
  return `<div class="seg" role="radiogroup" aria-label="Show the books">${options
    .map(
      ([id, label, n]) =>
        `<button type="button" role="radio" data-show="${escapeHtml(id)}" aria-checked="${id === current}" tabindex="${
          id === current ? 0 : -1
        }">${escapeHtml(label)} <span class="seg-n">${n}</span></button>`,
    )
    .join("")}</div>`;
}

function portfolios(doc: DeskDoc, groups: PortfolioGroup[], current: string): string {
  const ids = groups.map((g) => g.id);
  return section(
    "Portfolios",
    `${filterBar(doc, groups, current)}
    <div class="d-groups">${groups.map((g) => groupBlock(doc, g, ids)).join("")}</div>
    <div class="t-empty d-none" hidden>No book holds a position: every book is in cash.</div>`,
    "open a book for its whole paper-trading history",
    "portfolios",
  );
}

function readFilter(groups: PortfolioGroup[]): string {
  const query = window.location.hash.split("?")[1] ?? "";
  const asked = new URLSearchParams(query).get(FILTER_KEY) ?? "all";
  return ["all", "holding", ...groups.map((g) => g.id)].includes(asked) ? asked : "all";
}

/** Show the books the filter asks for, and remember it in the address (replaceState: no navigation, no reload). */
function applyFilter(root: HTMLElement, show: string): void {
  let visible = 0;
  for (const group of root.querySelectorAll<HTMLElement>(".d-group")) {
    let inGroup = 0;
    for (const li of group.querySelectorAll<HTMLElement>(".d-list > li")) {
      const on = show === "all" || (show === "holding" ? li.dataset.holding === "1" : group.dataset.group === show);
      li.hidden = !on;
      if (on) inGroup += 1;
    }
    group.hidden = inGroup === 0;
    visible += inGroup;
  }
  const none = root.querySelector<HTMLElement>(".d-none");
  if (none) none.hidden = visible > 0;
  for (const b of root.querySelectorAll<HTMLButtonElement>(".seg [role=radio]")) {
    const on = b.dataset.show === show;
    b.setAttribute("aria-checked", String(on));
    b.tabIndex = on ? 0 : -1;
  }
  const base = window.location.hash.split("?")[0] || "#/";
  const next = show === "all" ? base : `${base}?${FILTER_KEY}=${encodeURIComponent(show)}`;
  if (window.location.hash !== next) history.replaceState(null, "", next);
}

function wireFilter(root: HTMLElement, start: string): void {
  const bar = root.querySelector<HTMLElement>(".seg");
  if (!bar) return;
  const buttons = [...bar.querySelectorAll<HTMLButtonElement>("[role=radio]")];
  bar.addEventListener("click", (ev) => {
    const b = (ev.target as HTMLElement).closest<HTMLButtonElement>("[role=radio]");
    if (b?.dataset.show) applyFilter(root, b.dataset.show);
  });
  // a radio group moves with the arrow keys, and the choice follows the focus
  bar.addEventListener("keydown", (ev: KeyboardEvent) => {
    const i = buttons.findIndex((b) => b === document.activeElement);
    if (i < 0) return;
    const step = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : ev.key === "ArrowLeft" || ev.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    ev.preventDefault();
    const next = buttons[(i + step + buttons.length) % buttons.length]!;
    next.focus();
    if (next.dataset.show) applyFilter(root, next.dataset.show);
  });
  applyFilter(root, start);
}

// ---------------------------------------------------------------------------------------------- P&L chart
function pnlSection(groups: PortfolioGroup[]): string {
  if (!groups.some((g) => g.curve.length >= 2)) return "";
  return section(
    "Profit and loss since the start",
    `<div class="t-chart" id="d-pnl"></div><div id="d-pnl-legend"></div>`,
    "dollars, each group's books added up; a book that starts later joins at zero",
  );
}

function drawPnl(root: HTMLElement, groups: PortfolioGroup[]): void {
  const el = root.querySelector<HTMLElement>("#d-pnl");
  const legend = root.querySelector<HTMLElement>("#d-pnl-legend");
  if (!el || !legend) return;
  const ids = groups.map((g) => g.id);
  const series: LineSeries[] = groups.map((g) => ({
    id: g.id,
    label: g.label,
    color: groupColor(g.id, ids),
    points: g.curve.map((p) => ({ t: Date.parse(`${p.t.slice(0, 10)}T12:00:00Z`), v: p.v })).filter((p) => Number.isFinite(p.t)),
  }));
  const dateFmt = (t: number): string => day(new Date(t).toISOString());
  lineChart(el, series, {
    ariaLabel: `Profit and loss of each group of books since the start, in dollars: ${groups
      .map((g) => `${g.label} ${signedUsd(g.pnl, 2)}`)
      .join(", ")}`,
    height: 210,
    baseline: 0,
    fmt: (v) => signed(v, 0),
    minStep: 1,
    readoutFmt: (v) => signedUsd(v, 2),
    dateFmt,
    readoutDate: (t) => longDay(new Date(t).toISOString().slice(0, 10)),
    endLabels: series.filter((s) => s.points.length >= 2).length <= 4,
  });
  legend.innerHTML = lineLegend(series, (v) => signedUsd(v, 2));
}

// ---------------------------------------------------------------------------------------------- activity
const FILLS_SHOWN = 8;
const FILLS_PAGE = 50;

function fillRow(f: FillRow, names: Map<string, string>, hidden = false): string {
  return `<tr data-href="${bookHref(f.book)}"${hidden ? " hidden" : ""}>
    <td class="t-nowrap f-when">${escapeHtml(when(f.ts))}</td>
    <th scope="row" class="f-book"><a href="${bookHref(f.book)}">${escapeHtml(names.get(f.book) ?? f.book)}</a></th>
    <td class="w f-what">${escapeHtml(fillWords(f))}<small>${escapeHtml(reasonWords(f.reason))}${
      f.cost_bps === null || f.cost_bps === undefined ? "" : `, cost ${num(f.cost_bps, 1)} bp`
    }</small></td>
    <td class="n f-pnl">${f.realized_pnl ? `${toneSpan(f.realized_pnl, signedUsd(f.realized_pnl, 2))}<small class="m-l">closed</small>` : ""}</td>
    <td class="d-go" aria-hidden="true">${chevron("right")}</td>
  </tr>`;
}

function activity(doc: DeskDoc): string {
  if (!doc.fills.length) {
    return section(
      "Latest fills",
      `<div class="t-empty">No fill yet. An order fills at the open of the first half-hour bar after its decision.</div>`,
    );
  }
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  const rows = doc.fills
    .slice(0, FILLS_SHOWN)
    .map((f) => fillRow(f, names))
    .join("");
  return section(
    "Latest fills",
    `<div class="t-scroll"><table class="t-table d-fills">
      <thead><tr><th>When</th><th>Book</th><th>What</th><th class="n">Closed P&amp;L</th><th class="d-go"></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="d-allfills"><button type="button" class="btn secondary sm" data-all-fills>Every fill since the start</button>
      <span class="t-dim" data-all-note></span></div>
    <p class="t-dim">Every decision, with the reasons the book gave for it, is on the book's own page.</p>`,
    `your time zone (${escapeHtml(zoneName())}); the price after spread and slippage`,
    "fills",
  );
}

/** Every fill of every book's current life, newest first: read from the books' own history files when asked for. */
async function showAllFills(root: HTMLElement, doc: DeskDoc, button: HTMLButtonElement): Promise<void> {
  const note = root.querySelector<HTMLElement>("[data-all-note]");
  const body = root.querySelector<HTMLElement>(".d-fills tbody");
  if (!body) return;
  button.disabled = true;
  if (note) note.textContent = "Reading the books' histories…";
  const loaded = await Promise.all(doc.books.map((b) => load<BookHistory>(`books/${encodeURIComponent(b.id)}.json`)));
  // a history of another life (a reset since, or a copy bundled at the last deploy) is not this book's: left out
  const usable = doc.books.map((b, i) => {
    const h = loaded[i]!.data;
    return h && h.id === b.id && h.epoch === b.epoch ? h : null;
  });
  const missing = doc.books.filter((_, i) => !usable[i]).map((b) => b.name);
  const older = doc.books
    .filter((_, i) => {
      const h = usable[i];
      return h ? (Date.parse(doc.generated_at) - Date.parse(h.generated_at)) / 60000 > 90 : false;
    })
    .map((b) => b.name);
  const fills = usable
    .flatMap((h) => h?.fills ?? [])
    .sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  if (!fills.length) {
    if (note) note.textContent = missing.length ? "The books' histories are not published yet." : "No fill yet.";
    button.disabled = false;
    return;
  }
  body.innerHTML = fills.map((f, i) => fillRow(f, names, i >= FILLS_PAGE)).join("");
  const rest = (): number => body.querySelectorAll("tr[hidden]").length;
  const say = (): void => {
    const hidden = rest();
    if (note) {
      note.textContent = `${fills.length} fill${fills.length === 1 ? "" : "s"} in all${
        missing.length ? `; not read: ${missing.join(", ")}` : ""
      }${older.length ? `; published before the rest of the page: ${older.join(", ")}` : ""}.`;
    }
    button.textContent = `Show ${Math.min(FILLS_PAGE, hidden)} more`;
    button.hidden = hidden === 0;
    button.disabled = false;
  };
  button.onclick = () => {
    [...body.querySelectorAll<HTMLTableRowElement>("tr[hidden]")].slice(0, FILLS_PAGE).forEach((r) => {
      r.hidden = false;
    });
    say();
  };
  say();
}

function morePages(): string {
  return `<nav class="d-more" aria-label="More pages">
    <a href="#/signals"><b>Signals</b><span>The market, the forecast every oil book reads, the Kalshi indices, the allocator and the prediction markets.</span></a>
    <a href="#/backtest"><b>Backtest</b><span>The books replayed on decades of real prices with real costs, year by year.</span></a>
  </nav>`;
}

/** A row of a table that leads to a book opens it on a click anywhere in the row; the link in it stays the way
 *  a keyboard or a screen reader gets there. */
function wireRowLinks(root: HTMLElement): void {
  root.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    if (target.closest("a, button")) return;
    // the row's own link does the navigating, so a row behaves exactly like its link (and opens its page at the top)
    target.closest<HTMLElement>("tr[data-href]")?.querySelector<HTMLAnchorElement>("a[href]")?.click();
  });
}

export async function renderDesk(el: HTMLElement): Promise<void> {
  const loaded = await load<DeskDoc>("desk.json");
  const doc = loaded.data;
  if (!doc) {
    el.innerHTML = `<div class="term"><div class="t-empty">The books are unavailable: <code>desk.json</code> does not answer${
      loaded.error ? ` (${escapeHtml(loaded.error)})` : ""
    }. The engine publishes it every half hour; on a first start, try again in half an hour.</div></div>`;
    return;
  }
  if (!el.isConnected) return; // the reader went elsewhere while the data came: nothing to draw
  const groups = groupsOf(doc);
  const start = readFilter(groups);
  el.innerHTML = `<div class="term desk">
    ${doc.sample ? '<div class="banner sample">Sample data: this is not the real state of the books.</div>' : ""}
    ${statusLine(doc, loaded.origin)}
    ${hero(doc, groups)}
    ${openPositions(doc)}
    ${portfolios(doc, groups, start)}
    ${pnlSection(groups)}
    ${activity(doc)}
    ${morePages()}
  </div>`;
  const root = el.querySelector<HTMLElement>(".desk")!;
  wireFilter(root, start);
  wireRowLinks(root);
  drawPnl(root, groups);
  const all = root.querySelector<HTMLButtonElement>("[data-all-fills]");
  if (all) all.onclick = () => void showAllFills(root, doc, all);
}
