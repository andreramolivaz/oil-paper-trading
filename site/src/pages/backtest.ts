/** The backtest page: what the linear books would have done, how fragile that is, and what was rejected.
 *
 * Reads `desk_backtest.json`, the payload the weekly job writes. Everything a reader needs to distrust a
 * backtest is on the page: the t-statistic next to every Sharpe ratio, each calendar year, the same replay at
 * double cost and with the optimistic fill, every sleeve and every source on its own next to the mix read
 * before and the one read now, the books held together, where each day's return came from.
 */
import { lineChart, lineLegend, type LineSeries } from "../charts/lines";
import { repoUrl } from "../config";
import { load } from "../data";
import { bookColor, portfolios, type BacktestDoc, type CurvePoint, type PortfolioStats, type Stats } from "../desk";
import { EMPTY, escapeHtml, leverage, num, percent, relative, signedPercent, tone } from "../format";

const MINUS = "−";
const span = (cls: string, text: string): string => `<span class="${cls}">${text}</span>`;

function section(title: string, body: string, note = ""): string {
  return `<section class="t-sec">
    <h2 class="t-h"><span>${escapeHtml(title)}</span>${note ? `<small>${note}</small>` : ""}</h2>
    ${body}
  </section>`;
}

function points(curve: CurvePoint[]): { t: number; v: number }[] {
  return curve.map((p) => ({ t: Date.parse(`${p.t}T12:00:00Z`), v: p.v })).filter((p) => Number.isFinite(p.t) && p.v > 0);
}

const sharpeCell = (s: Stats | undefined): string =>
  s ? `${num(s.sharpe ?? null, 2)}<small>t ${num(s.t_stat ?? null, 1)}</small>` : EMPTY;

/** A portfolio's Sharpe with the interval it is known to, which is what the reader needs beside a ratio
 *  measured over a handful of years (engine/desk/report.py, `uncertainty`; docs/RESEARCH.md 15 G). */
const sharpeRange = (s: Stats | undefined): string => {
  if (!s) return EMPTY;
  const ci = s.sharpe_ci;
  const tail = ci ? `${num(ci[0], 2)}–${num(ci[1], 2)}` : `t ${num(s.t_stat ?? null, 1)}`;
  return `${num(s.sharpe ?? null, 2)}<small>${tail}</small>`;
};

const years = (a: string, b: string): string => `${escapeHtml(a.slice(0, 4))}–${escapeHtml(b.slice(0, 4))}`;

type Names = Map<string, string>;
type Roles = Map<string, string>;

function bookRow(id: string, b: BacktestDoc["books"][string], names: Names): string {
  const s = b.stats;
  return `<tr>
    <th scope="row"><span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}<small>${escapeHtml(
      b.vehicle,
    )} · ${years(b.start, b.end)}</small></th>
    <td class="n">${signedPercent(s.cagr ?? null, 1)}</td>
    <td class="n">${percent(s.vol ?? null, 0)}</td>
    <td class="n">${sharpeCell(s)}</td>
    <td class="n down">${percent(s.max_drawdown ?? null, 0)}<small>${num((s.longest_drawdown_days ?? 0) / 252, 1)} years under water</small></td>
    <td class="n down">${percent(s.worst_day ?? null, 1)}<small>week ${percent(s.worst_week ?? null, 0)}</small></td>
    <td class="n">${leverage(s.leverage_median ?? null)}<small>95th ${leverage(s.leverage_p95 ?? null)} · max ${leverage(
      s.leverage_max ?? null,
    )}</small></td>
    <td class="n">${percent(s.share_days_invested ?? null, 0)}<small>short ${percent(s.share_days_short ?? null, 0)}</small></td>
    <td class="n">${percent(b.costs.per_year_of_mean_equity ?? null, 2)}<small>${b.n_fills} fills</small></td>
    <td class="n">×${num(s.final_multiple ?? null, 2)}${b.died ? `<small class="down">wiped out on ${escapeHtml(b.died)}</small>` : ""}</td>
  </tr>`;
}

function summary(doc: BacktestDoc, names: Names, roles: Roles): string {
  const order = new Map(doc.books_config.map((b, i) => [b.id, i]));
  const entries = Object.entries(doc.books).sort(([a], [b]) => (order.get(a) ?? 99) - (order.get(b) ?? 99));
  const core = entries.filter(([id]) => roles.get(id) !== "candidate").map(([id, b]) => bookRow(id, b, names));
  const cand = entries.filter(([id]) => roles.get(id) === "candidate").map(([id, b]) => bookRow(id, b, names));
  const bench = Object.entries(doc.benchmarks)
    .map(([id, b]) => {
      const s = b.stats;
      return `<tr class="t-bench"><th scope="row">${escapeHtml(id)} bought and held<small>no leverage, no costs</small></th>
        <td class="n">${signedPercent(s.cagr ?? null, 1)}</td><td class="n">${percent(s.vol ?? null, 0)}</td>
        <td class="n">${sharpeCell(s)}</td>
        <td class="n down">${percent(s.max_drawdown ?? null, 0)}<small>${num((s.longest_drawdown_days ?? 0) / 252, 1)} years under water</small></td>
        <td class="n down">${percent(s.worst_day ?? null, 1)}<small>week ${percent(s.worst_week ?? null, 0)}</small></td>
        <td class="n">1.00x</td><td class="n">100%</td><td></td><td class="n">×${num(s.final_multiple ?? null, 2)}</td></tr>`;
    })
    .join("");
  // the books on file the replay cannot run (the speculative Kalshi and Claude books): a row with the reason, no numbers
  const FAMILY: Record<string, string> = { kalshi: "Kalshi's prediction markets", claude: "the Claude analyst" };
  const excluded = (doc.excluded ?? []).map(
    (x) => `<tr class="t-bench"><th scope="row">${escapeHtml(x.name)}<small>${escapeHtml(x.vehicle)}, speculative: ${escapeHtml(
      FAMILY[x.forecast ?? ""] ?? x.forecast ?? "",
    )}</small></th><td colspan="9" class="t-dim w">No backtest: ${escapeHtml(x.reason)}.</td></tr>`,
  );
  const families = [...new Set((doc.excluded ?? []).map((x) => FAMILY[x.forecast ?? ""]).filter((f): f is string => !!f))];
  const candBlock =
    cand.length || excluded.length
      ? `<tr class="t-break"><th colspan="10" class="t-dim w">Candidates: the prudent book's rules on another market, forecast on its own price${
          families.length
            ? `; the speculative books take their direction from ${escapeHtml(families.join(" or from "))}, which a replay cannot run on`
            : ""
        }</th></tr>${cand.join("")}${excluded.join("")}`
      : "";
  return section(
    "Result",
    `<div class="t-scroll"><table class="t-table t-backtest">
      <thead><tr><th>Book</th><th class="n">Yearly return</th><th class="n">Volatility</th><th class="n">Sharpe</th>
        <th class="n">Max drawdown</th><th class="n">Worst day</th><th class="n">Median leverage</th>
        <th class="n">Days invested</th><th class="n">Costs/year</th><th class="n">Capital</th></tr></thead>
      <tbody>${core.join("")}${candBlock}${bench}</tbody></table></div>
      <p class="t-dim">${escapeHtml(doc.meta.fill_rule.charAt(0).toUpperCase() + doc.meta.fill_rule.slice(1))}; initial capital ${num(doc.meta.initial_capital, 0)} $ per book.
      "t" is the Sharpe times the square root of the years: below 2 the result cannot be told from luck with confidence.
      It is an optimistic t, because it treats the Sharpe as exact; the portfolio table below carries the interval the
      Sharpe is really known to, which is wider.</p>`,
    `computed ${escapeHtml(relative(doc.meta.generated_at))}`,
  );
}

function together(doc: BacktestDoc): string {
  const port = portfolios(doc.portfolio);
  if (!port.length) return "";
  const rows = port
    .map(([, label, p]) => {
      const s = p.stats;
      return `<tr><th scope="row">${escapeHtml(label)}<small>${p.books.length} books · ${years(p.start, p.end)}</small></th>
        <td class="n">${signedPercent(s.cagr ?? null, 1)}</td>
        <td class="n">${percent(s.vol ?? null, 0)}</td>
        <td class="n"><b>${sharpeRange(s)}</b></td>
        <td class="n down">${percent(s.max_drawdown ?? null, 0)}<small>${num((s.longest_drawdown_days ?? 0) / 252, 1)} years under water</small></td>
        <td class="n down">${percent(s.worst_day ?? null, 1)}<small>week ${percent(s.worst_week ?? null, 0)}</small></td></tr>`;
    })
    .join("");
  const yearsSeen = [...new Set(port.flatMap(([, , p]) => Object.keys(p.yearly ?? {})))].sort().reverse().slice(0, 12);
  const yearly = yearsSeen.length
    ? `<div class="t-scroll"><table class="t-table t-years">
        <thead><tr><th>Year</th>${port.map(([, label]) => `<th class="n">${escapeHtml(label)}</th>`).join("")}</tr></thead>
        <tbody>${yearsSeen
          .map(
            (y) =>
              `<tr><th scope="row">${escapeHtml(y)}</th>${port
                .map(([, , p]) => {
                  const v = p.yearly?.[y];
                  return `<td class="n">${v === undefined ? "" : span(tone(v), signedPercent(v, 1))}</td>`;
                })
                .join("")}</tr>`,
          )
          .join("")}</tbody></table></div>`
    : "";
  return section(
    "The books together",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Portfolio</th><th class="n">Yearly return</th><th class="n">Volatility</th><th class="n">Sharpe<small>95% interval</small></th>
        <th class="n">Max drawdown</th><th class="n">Worst day</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      ${yearly}
      <p class="t-dim">Equal capital in every book, rebalanced daily, over the days all of them have. The Sharpe of the sum
      is not the average of the Sharpes: books that move independently add up to less risk than return. This is the
      number the candidates were added for; it says nothing about any book alone. The interval under each Sharpe is where
      the true ratio lies with 95% confidence: a ratio measured over a dozen years never has two meaningful digits.</p>
      ${instead(port)}`,
    "the same backtests, held side by side",
  );
}

/** What a reader could buy instead, over the SAME window as the portfolio: the comparison the reader is
 *  actually making, with the intervals that say whether the difference can be told apart at all (15 G). */
function instead(port: [string, string, PortfolioStats][]): string {
  // the widest portfolio first: "all" is the number the page is read for, and its window is the common one
  const chosen = port.find(([key, , p]) => key === "all" && p.benchmarks) ?? port.find(([, , p]) => p.benchmarks) ?? null;
  if (!chosen) return "";
  const [, label, p] = chosen;
  const bench = Object.values(p.benchmarks ?? {});
  if (!bench.length) return "";
  const row = (name: string, s: Stats, own: boolean): string =>
    `<tr${own ? ' class="t-total"' : ""}><th scope="row">${escapeHtml(name)}</th>
      <td class="n">${signedPercent(s.cagr ?? null, 1)}</td>
      <td class="n">${percent(s.vol ?? null, 0)}</td>
      <td class="n">${own ? `<b>${sharpeRange(s)}</b>` : sharpeRange(s)}</td>
      <td class="n down">${percent(s.max_drawdown ?? null, 0)}</td></tr>`;
  return `<h3 class="t-sub">What a reader could buy instead</h3>
    <div class="t-scroll"><table class="t-table">
      <thead><tr><th>Over ${years(p.start, p.end)}</th><th class="n">Yearly return</th><th class="n">Volatility</th>
        <th class="n">Sharpe<small>95% interval</small></th><th class="n">Max drawdown</th></tr></thead>
      <tbody>${row(label, p.stats, true)}${bench.map((b) => row(b.label, b.stats, false)).join("")}</tbody></table></div>
    <p class="t-dim">${p.benchmark_note ? `${escapeHtml(p.benchmark_note)} ` : ""}Bought and held over the same days as the
    portfolio above, with the same formula the desk uses for its own books (no risk-free rate subtracted on either side).
    A paper desk that cannot be told apart from an index fund is the honest state of this research, not a failure of it.</p>`;
}

function withAndWithout(doc: BacktestDoc, names: Names, roles: Roles): string {
  const wo = doc.without_allocator;
  const rules = doc.allocator?.rules;
  if (!wo || !rules) return "";
  const order = new Map(doc.books_config.map((b, i) => [b.id, i]));
  const ids = Object.keys(doc.books)
    .filter((id) => id in wo.books)
    .sort((a, b) => (order.get(a) ?? 99) - (order.get(b) ?? 99));
  const delta = (a: number | null | undefined, b: number | null | undefined, digits: number, pctFmt = false): string => {
    if (a === null || a === undefined || b === null || b === undefined) return EMPTY;
    const d = a - b;
    const text = pctFmt ? signedPercent(d, digits) : `${d > 0 ? "+" : d < 0 ? MINUS : ""}${num(Math.abs(d), digits)}`;
    return span(tone(d), text);
  };
  const rowOf = (label: string, withS: Stats | undefined, without: Stats | undefined, cls = ""): string =>
    `<tr${cls ? ` class="${cls}"` : ""}><th scope="row">${label}</th>
      <td class="n">${sharpeCell(without)}</td><td class="n"><b>${sharpeCell(withS)}</b></td>
      <td class="n">${delta(withS?.sharpe ?? null, without?.sharpe ?? null, 2)}</td>
      <td class="n down">${percent(without?.max_drawdown ?? null, 0)}</td><td class="n down">${percent(withS?.max_drawdown ?? null, 0)}</td>
      <td class="n">${signedPercent(without?.cagr ?? null, 1)}</td><td class="n">${signedPercent(withS?.cagr ?? null, 1)}</td>
      <td class="n">${percent(without?.vol ?? null, 0)}</td><td class="n">${percent(withS?.vol ?? null, 0)}</td></tr>`;
  const bookRows = ids
    .map((id) =>
      rowOf(
        `<span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}${
          roles.get(id) === "candidate" ? '<small>candidate</small>' : ""
        }`,
        doc.books[id]?.stats,
        wo.books[id]?.stats,
      ),
    )
    .join("");
  const portRows = portfolios(doc.portfolio)
    .map(([key, label, p]) => rowOf(escapeHtml(label), p.stats, wo.portfolio?.[key]?.stats, "t-total"))
    .join("");
  const ruleRows = Object.entries(rules)
    .map(([, r]) => `<li>${r.enabled ? "<b>on</b>" : '<span class="t-dim">off</span>'} · ${escapeHtml(r.rule)}</li>`)
    .join("");
  return section(
    "With and without the allocator",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Book</th><th class="n">Sharpe without</th><th class="n">with</th><th class="n">Δ</th>
        <th class="n">Max drawdown without</th><th class="n">with</th><th class="n">Return without</th><th class="n">with</th>
        <th class="n">Volatility without</th><th class="n">with</th></tr></thead>
      <tbody>${bookRows}${portRows}</tbody></table></div>
      <ul class="t-dim">${ruleRows}</ul>
      <p class="t-dim">The same replay twice: the books under their own rules alone, and under the allocator's rules as they
      are switched on today (the bubble switch, the strait's weekend rule, the lifecycle). The replay reads the bubble
      switch; the strait's rule only where the transit data exists (since 2019) and the lifecycle never fires in a replay
      that starts every book as it is configured. The rules were measured before being switched on, and this table is
      recomputed with the backtest: if a rule stops paying, it shows here first.</p>`,
    "every number the main table shows is WITH the allocator",
  );
}

function ruin(doc: BacktestDoc, names: Names): string {
  const rows = Object.entries(doc.books)
    .map(([id, b]) => {
      const r = b.ruin ?? {};
      return `<tr><th scope="row"><span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}</th>
        <td class="n">${percent(r.p_lose_quarter ?? null, 1)}</td>
        <td class="n">${percent(r.p_lose_half ?? null, 1)}</td>
        <td class="n">${percent(r.p_dead ?? null, 1)}</td>
        <td class="n">×${num(r.p5_final ?? null, 2)}</td><td class="n">×${num(r.median_final ?? null, 2)}</td>
        <td class="n">×${num(r.p95_final ?? null, 2)}</td></tr>`;
    })
    .join("");
  return section(
    "Risk of ruin in one year",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Book</th><th class="n">P(lose a quarter)</th><th class="n">P(lose half)</th>
        <th class="n">P(wiped out)</th><th class="n">5th percentile</th><th class="n">Median</th><th class="n">95th percentile</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="t-dim">Block bootstrap of the book's own daily returns, 4,000 one-year paths. No path can hold a day worse
      than the worst already seen: for a leveraged book this number is a floor on the risk, not a ceiling.</p>`,
  );
}

function yearly(doc: BacktestDoc, names: Names): string {
  const cols: { id: string; label: string; data: Record<string, number> }[] = [
    ...Object.entries(doc.books).map(([id, b]) => ({ id, label: names.get(id) ?? id, data: b.yearly })),
    ...Object.entries(doc.benchmarks).map(([id, b]) => ({ id: `bh-${id}`, label: `${id} held`, data: b.yearly })),
  ];
  const allYears = [...new Set(cols.flatMap((c) => Object.keys(c.data)))].sort().reverse();
  const rows = allYears
    .map(
      (y) =>
        `<tr><th scope="row">${escapeHtml(y)}</th>${cols
          .map((c) => {
            const v = c.data[y];
            return `<td class="n">${v === undefined ? "" : span(tone(v), signedPercent(v, 1))}</td>`;
          })
          .join("")}</tr>`,
    )
    .join("");
  return section(
    "Year by year",
    `<div class="t-scroll t-tall"><table class="t-table t-years">
      <thead><tr><th>Year</th>${cols.map((c) => `<th class="n">${escapeHtml(c.label)}</th>`).join("")}</tr></thead>
      <tbody>${rows}</tbody></table></div>`,
    "three negative years in a row show here, not in an average",
  );
}

function sensitivity(doc: BacktestDoc, names: Names): string {
  const rows = Object.entries(doc.books)
    .map(([id, b]) => {
      const dc = doc.double_cost[id]?.stats;
      const sc = doc.same_close[id]?.stats;
      return `<tr><th scope="row"><span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}</th>
        <td class="n">${sharpeCell(b.stats)}</td>
        <td class="n">${sharpeCell(dc)}</td>
        <td class="n">${sharpeCell(sc)}</td>
        <td class="n">${signedPercent(b.stats.cagr ?? null, 1)}</td>
        <td class="n">${signedPercent(dc?.cagr ?? null, 1)}</td>
        <td class="n">${signedPercent(sc?.cagr ?? null, 1)}</td></tr>`;
    })
    .join("");
  return section(
    "How much depends on costs and execution",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Book</th><th class="n">Sharpe</th><th class="n">at double costs</th><th class="n">filled at the close</th>
        <th class="n">Yearly return</th><th class="n">at double costs</th><th class="n">filled at the close</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="t-dim">Live, a book decides in the last hour of the session and fills on the next bar: the true result sits
      between "next day's open" (the main column, the harshest for the funds) and "same close".</p>`,
  );
}

function sleeves(doc: BacktestDoc): string {
  const blocks = Object.entries(doc.sleeves)
    .map(([vehicle, block]) => {
      // three groups, a rule between them: the sleeves, the sources, then before against now
      const firstOf = (kind: string): number => block.rows.findIndex((r) => r.kind === kind);
      const breaks = new Set([firstOf("source"), firstOf("before"), firstOf("all")].filter((i) => i > 0));
      const seen = new Set<string>();
      const rows = block.rows
        .map((r, i) => {
          const brk = breaks.has(i) && !seen.has(r.kind ?? "");
          seen.add(r.kind ?? "");
          return `<tr class="${brk ? "t-break" : ""}${r.kind === "all" ? " t-all" : ""}"><th scope="row">${escapeHtml(r.name)}</th>
            <td class="n">${sharpeCell(r.stats)}</td>
            <td class="n">${sharpeCell(r.stats_same_close)}</td>
            <td class="n">${signedPercent(r.stats.cagr ?? null, 1)}</td>
            <td class="n down">${percent(r.stats.max_drawdown ?? null, 0)}</td>
            <td class="n">${percent(r.stats.share_days_invested ?? null, 0)}</td></tr>`;
        })
        .join("");
      const side = block.long_only ? "long only" : block.short_via ? `long and short, short by buying ${escapeHtml(block.short_via)}` : "long and short";
      return `<h3 class="t-sub">${escapeHtml(vehicle)} · ${side} · volatility target ${percent(block.vol_target, 0)}</h3>
      <div class="t-scroll"><table class="t-table">
        <thead><tr><th>Component</th><th class="n">Sharpe</th><th class="n">filled at the close</th><th class="n">Yearly return</th>
          <th class="n">Max drawdown</th><th class="n">Days invested</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
    })
    .join("");
  return section(
    "The signals one at a time, then by source, then together",
    `${blocks}<p class="t-dim">Reference account a thousand times larger, to remove the effect of the minimum lot. Published
    parameters, a third of the weight to each source. "Before" is the forecast the oil books read until 8 October 2026
    (trend, carry, carry momentum); "Now" the one they read today. The four signals added are those that stood among the
    sixteen tried; the other twelve, with their numbers, are in
    <a href="${repoUrl()}/blob/main/docs/RESEARCH.md" target="_blank" rel="noopener">docs/RESEARCH.md</a>. Part of the
    improvement is selection: putting everything that was tried together at equal weights, choosing nothing, gives about
    0.1 of Sharpe less. The candidate funds read the price source alone.</p>`,
  );
}

function optionsReplay(doc: BacktestDoc): string {
  const o = doc.options;
  if (!o || !o.available || !o.cases) {
    return section("≈ Options", `<div class="t-empty">Simulation unavailable${o?.reason ? `: ${escapeHtml(o.reason)}` : ""}.</div>`);
  }
  const SMILE: Record<string, string> = {
    piatto: "same volatility at every strike",
    ottobre_2026: "smile read on the chain of 8 October 2026",
    put_ripido: "far puts dearer (the usual shape)",
  };
  const rows = o.cases
    .map(
      (c) => `<tr><th scope="row">${escapeHtml(SMILE[c.smile] ?? c.smile)}<small>costs ×${num(c.cost_multiplier, 0)}</small></th>
        <td class="n">${signedPercent(c.stats.cagr ?? null, 1)}</td>
        <td class="n">${percent(c.stats.vol ?? null, 1)}</td>
        <td class="n">${sharpeCell(c.stats)}</td>
        <td class="n down">${percent(c.stats.max_drawdown ?? null, 0)}</td>
        <td class="n">${num(c.stats.trades_per_year ?? null, 1)}</td>
        <td class="n">${percent(c.stats.win_rate ?? null, 0)}</td>
        <td class="n">${num(c.stats.avg_r ?? null, 3)}</td></tr>`,
    )
    .join("");
  return section(
    "≈ Options: a model replay",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Assumption</th><th class="n">Yearly return</th><th class="n">Volatility</th><th class="n">Sharpe</th>
        <th class="n">Max drawdown</th><th class="n">Trades/year</th><th class="n">Closed in profit</th>
        <th class="n">Mean result / risk</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="t-dim">${escapeHtml(o.method ?? "")} No free history of option quotes exists: these numbers give the order of
      magnitude, not the second decimal. Nine trades in ten close in profit and one now and then loses ten times as much:
      that is the shape of this strategy, not a flaw of the account. The paper book exists to replace the model with real
      prices.</p>`,
    "declared approximation",
  );
}

function provenance(doc: BacktestDoc): string {
  const LABEL: Record<string, string> = {
    fund: "the fund's price",
    contract: "the contract held (exact)",
    eia: "EIA settlements of contracts 1 and 2 (exact)",
    front: "front contract, on the days it is the one held",
    "fund (approx)": "≈ the USO fund on roll days",
    // the labels the payload carried before 13 October 2026
    fondo: "the fund's price",
    contratto: "the contract held (exact)",
    "fondo (approx)": "≈ the USO fund on roll days",
  };
  const rows = Object.entries(doc.data)
    .map(([vehicle, d]) => {
      const total = Object.values(d.return_sources).reduce((a, b) => a + b, 0) || 1;
      const mix = Object.entries(d.return_sources)
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `${escapeHtml(LABEL[k] ?? k)} ${percent(v / total, 1)}`)
        .join(" · ");
      return `<tr><th scope="row">${escapeHtml(vehicle)}</th><td class="t-nowrap">${escapeHtml(d.start)} → ${escapeHtml(d.end)}</td>
        <td class="n">${num(d.days, 0)}</td><td class="w">${mix}</td>
        <td class="n">${percent(d.slope_approx_share, 0)}</td></tr>`;
    })
    .join("");
  return section(
    "Where the data comes from",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Instrument</th><th>Period</th><th class="n">Sessions</th><th>Daily return</th>
        <th class="n">≈ curve by proxy</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="t-dim">For BNO the Brent curve by contract exists only since the engine archives it: before that, the slope is
      WTI's (contracts 2 and 4, EIA) and is marked as an approximation. The candidate funds have no curve and no other
      market: their forecast is the price source alone.</p>`,
  );
}

function rejected(): string {
  const rows: [string, string][] = [
    ["Intraday momentum (noise-area breakout) on BNO, USO, UCO, XLE", `gross Sharpe between ${MINUS}1.5 and ${MINUS}2.1 on hourly bars`],
    ["First and last hour, the day after the EIA Wednesday, the settlement window", "nothing distinguishable from chance (|t| under 1.7)"],
    ["Mean reversion of the BNO–USO spread", `Sharpe between ${MINUS}0.2 and ${MINUS}0.5 after costs`],
    ["Selling UCO and SCO together to collect the decay", `0.96 on the whole sample but ${MINUS}0.36 since 2022: does not hold`],
    ["Buying a call and a put together (straddle) every month", `loses: the options cost more than the move that follows, t beyond ${MINUS}2.5`],
    ["Selling premium with condors or covered butterflies", "zero or below, once four legs and the smile are paid"],
    ["Channel breakout at 40-320 days", "0.29 alone, but correlated 0.79 with the trend already there: the same bet"],
    [
      "Continuous carry, five-year value, trend only where the curve agrees",
      `0.34 (and ${MINUS}0.21 on the fund), ${MINUS}0.09, 0.40 but correlated 0.88: none adds anything`,
    ],
    ["Fund flows and positioning (COT), hedging pressure", `0.15 and ${MINUS}0.26: on crude alone they say nothing`],
    ["Open-interest growth, EIA inventories", "0.41 and 0.19 at the published window, zero or less at the one beside it"],
    ["Refining margin (crack spread) above its mean", "0.31, but 0.05 with a day of delay: it was a timing artefact"],
    ["Volatility premium (OVX minus realised), insider buying", `${MINUS}0.35 and 0.07`],
    // phase 12, 13 October 2026
    [
      "Natural gas through its funds (UNG, short by buying KOLD), the dynamic book's rules",
      `${MINUS}0.12 over 18 years: UNG loses the contango every month (${MINUS}99.9% held since 2007), KOLD the daily reset; the 8% daily stop fired eleven times`,
    ],
    [
      "Basis momentum: the twelve-month return of the curve's slope (Boons & Prado)",
      `0.02 on WTI, 0.27 and ${MINUS}0.23 on the two halves; the continuous form 0.25, with nothing after 2005`,
    ],
    ["Geopolitical risk index (Caldara & Iacoviello), continuous, by sign and inverted", `${MINUS}0.07, 0.04 and 0.05: the news say nothing about the next day's direction`],
    [
      "Calendar spread: long the contract held, short the one two months out, by the sign of the basis",
      `0.62 gross (t 3.9) but ${MINUS}0.64 net: 4.4x the notional per leg, two /MCL legs rolled monthly, 34% a year in costs`,
    ],
  ];
  return section(
    "Tried and rejected",
    `<div class="t-scroll"><table class="t-table t-log"><tbody>${rows
      .map(([a, b]) => `<tr><td class="w">${escapeHtml(a)}</td><td class="w t-dim">${escapeHtml(b)}</td></tr>`)
      .join("")}</tbody></table></div>
    <p class="t-dim">Measured in October 2026 on real data; for the daily signals, Sharpe on WTI 1985-2026 with the 15%
    volatility target, and for the candidates on each fund's own history. Every candidate was written down before its result was
    looked at. <a href="${repoUrl()}/blob/main/docs/RESEARCH.md" target="_blank" rel="noopener">Method, numbers and sources →</a></p>`,
    "what did not hold stays out of the books",
  );
}

export async function renderBacktest(el: HTMLElement): Promise<void> {
  const loaded = await load<BacktestDoc>("desk_backtest.json");
  const doc = loaded.data;
  if (!doc || !doc.books) {
    el.innerHTML = `<div class="term"><div class="t-empty">Backtest not published yet: the first tick that has the data computes it,
      then the weekly job (or <code>python -m engine.cli desk-backtest</code>).</div></div>`;
    return;
  }
  const names: Names = new Map(doc.books_config.map((b) => [b.id, b.name]));
  const roles: Roles = new Map(doc.books_config.map((b) => [b.id, b.role ?? "core"]));
  const vehicles = [...new Set(Object.values(doc.books).map((b) => b.vehicle))];
  const charts = vehicles
    .map(
      (v) => `<h3 class="t-sub">${escapeHtml(v)}: capital from ${num(doc.meta.initial_capital, 0)} $, log scale</h3>
        <div class="t-chart" id="bt-${escapeHtml(v)}"></div><div id="bt-${escapeHtml(v)}-legend"></div>`,
    )
    .join("");
  el.innerHTML = `<div class="term">
    ${summary(doc, names, roles)}
    ${together(doc)}
    ${withAndWithout(doc, names, roles)}
    ${section("Curves", charts, "each book against its own instrument bought and held")}
    ${ruin(doc, names)}
    ${sensitivity(doc, names)}
    ${sleeves(doc)}
    ${yearly(doc, names)}
    ${optionsReplay(doc)}
    ${provenance(doc)}
    ${rejected()}
  </div>`;
  for (const v of vehicles) {
    const target = el.querySelector<HTMLElement>(`#bt-${v}`);
    const legend = el.querySelector<HTMLElement>(`#bt-${v}-legend`);
    if (!target || !legend) continue;
    const series: LineSeries[] = Object.entries(doc.books)
      .filter(([, b]) => b.vehicle === v)
      .map(([id, b]) => ({ id, label: names.get(id) ?? id, color: bookColor(id), points: points(b.curve) }));
    const bench = doc.benchmarks[v];
    if (bench) {
      series.push({ id: `bh-${v}`, label: `${v} bought and held`, color: "var(--text-muted)", points: points(bench.curve), dashed: true });
    }
    const yearFmt = (t: number): string => String(new Date(t).getUTCFullYear());
    lineChart(target, series, {
      ariaLabel: `Capital of the books on ${v} against the instrument bought and held`,
      height: 240,
      log: true,
      fmt: (x) => num(x, 0),
      dateFmt: yearFmt,
    });
    legend.innerHTML = lineLegend(series, (x) => `${num(x, 0)} $`);
  }
}
