/** The archive: one page on the first system, which still runs every day and never traded.
 *
 * Twenty-one strategies on shadow accounts behind a seven-condition validation gate; nothing passed, so the
 * master account is flat by construction. The page says that in English, shows the gate's verdict for every
 * strategy from `validation.json`, the master account from `summary.json`, and links the documents. It does
 * not redraw the six Italian pages the first system used to have: the research they showed is in the
 * documents, and the numbers that matter are the ones below.
 */
import { repoUrl } from "../config";
import { load } from "../data";
import { EMPTY, escapeHtml, family, num, percent, relative, signedPercent, stamp, tone } from "../format";
import type { StrategiesDoc, SummaryDoc, ValidationDoc } from "../types";

const MINUS = "−";

function section(title: string, body: string, note = ""): string {
  return `<section class="t-sec">
    <h2 class="t-h"><span>${escapeHtml(title)}</span>${note ? `<small>${note}</small>` : ""}</h2>
    ${body}
  </section>`;
}

/** The engine writes its strategy names and regime labels in Italian; the page shows them in English. */
const STRATEGY_NAMES: Record<string, string> = {
  S1: "Multi-horizon momentum filtered by carry",
  S2: "Compression breakout",
  S3: "Adaptive trend (Kalman)",
  S4: "Carry and roll yield",
  S5: "Calendar spread M1-M3",
  S6: "Curve butterfly 1-3-6",
  S7: "Physical confirmation",
  S8: "Brent-WTI (cointegration)",
  S9: "Crack spread 3-2-1",
  S10: "Macro fair value",
  S11: "EIA inventory surprise",
  S12: "OPEC+ playbook",
  S13: "Geopolitical premium",
  S14: "Seasonality",
  S15: "COT positioning",
  S16: "Volatility regime",
  S17: "Short-term reversal",
  S18: "Synthetic options (approximation)",
  S21: "Insider conviction (Form 4)",
};

const REGIME_LABELS: Record<string, string> = {
  "Backwardation + vol alta + rischio geopolitico": "Backwardation, high volatility, geopolitical risk",
  "Contango + eccesso d'offerta + trend ribassista": "Contango, oversupply, downtrend",
  "Range a bassa volatilità": "Low-volatility range",
  "Shock/crash": "Shock / crash",
  "Squeeze rialzista": "Bull squeeze",
  Transizione: "Transition",
};

interface ValidationRow {
  id: string;
  name?: string;
  metrics?: { sharpe?: number | null; n_trades?: number | null; max_drawdown?: number | null; cagr?: number | null };
  costs?: { x2?: { sharpe?: number | null } };
  dsr?: { dsr_prob?: number | null };
  pbo?: number | null;
}

function signedNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return EMPTY;
  const s = num(Math.abs(v), digits);
  return v > 0 ? `+${s}` : v < 0 ? `${MINUS}${s}` : s;
}

function master(summary: SummaryDoc | null, validation: ValidationDoc | null): string {
  const acct = summary?.account ?? {};
  const m = (validation?.master as ValidationRow | undefined)?.metrics ?? {};
  const window = (validation?.window as { start?: string; end?: string; years?: number } | undefined) ?? {};
  const live = `<p>Live, epoch ${num(acct.epoch ?? null, 0)}: equity <b>${num(acct.equity ?? null, 2)} $</b>,
    leverage ${num(acct.leverage?.value ?? null, 2)}x, ${acct.positions?.length ? `${acct.positions.length} position(s)` : "no position"}${
      acct.dead ? ' <span class="down">· wiped out, waiting for a reset</span>' : ""
    }. Every strategy has weight zero, so the account holds cash. <span class="t-dim">${acct.asof ? `As of ${escapeHtml(stamp(acct.asof))}.` : ""}</span></p>`;
  const bt =
    m.sharpe === undefined
      ? ""
      : `<p>Walk-forward backtest of the same portfolio, ${escapeHtml(window.start ?? "")} to ${escapeHtml(window.end ?? "")} (${num(
          window.years ?? null,
          1,
        )} years, 1x or less): Sharpe <b>${signedNum(m.sharpe)}</b>, yearly return ${signedPercent(m.cagr ?? null, 1)}, max drawdown
      ${percent(m.max_drawdown ?? null, 0)}, ${num(m.n_trades ?? null, 0)} trades. As it stands, the portfolio loses money in
      the replay; that is why nothing was promoted.</p>`;
  return section("Master account", live + bt, "the account the gate protects: flat by construction");
}

function regime(summary: SummaryDoc | null): string {
  const r = summary?.regime;
  if (!r || !r.label) return "";
  const probs = Object.entries(r.probabilities ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([k, v]) => `${escapeHtml(REGIME_LABELS[k] ?? k)} ${percent(v, 0)}`)
    .join(" · ");
  return section(
    "Regime label",
    `<p><b>${escapeHtml(REGIME_LABELS[r.label] ?? r.label)}</b> at ${percent(r.confidence ?? null, 0)}${r.approx ? " ≈" : ""}
      <span class="t-dim">· change-point probability ${percent(r.change_point_prob ?? null, 0)} · ${probs}</span></p>
    <p class="t-dim">Known limit (diagnosed 9 October 2026, not fixed: it is a research decision): the hidden Markov model's
    curve feature drops out of every fit as soon as a few rows of the real Brent curve arrive, and a label names the
    long-run mean of a state, not the day. The label has read "contango" in a 10% backwardation and never "geopolitical"
    during a war. Nothing on the desk reads it. <a href="${repoUrl()}/blob/main/docs/PLAN.md" target="_blank" rel="noopener">The diagnosis →</a></p>`,
    `${escapeHtml(r.source ?? "")}${r.asof ? ` · ${escapeHtml(stamp(r.asof))}` : ""}`,
  );
}

function verdicts(validation: ValidationDoc | null, strategies: StrategiesDoc | null): string {
  const rows = (validation?.strategies as ValidationRow[] | undefined) ?? [];
  if (!rows.length) {
    return section("The gate's verdict", `<div class="t-empty">No validation published yet: the weekly job writes it on Saturday.</div>`);
  }
  const families = new Map((strategies?.strategies ?? []).map((s) => [s.id, s.family ?? null]));
  const thresholds = (validation?.thresholds as Record<string, number> | undefined) ?? {};
  const pbo = (validation?.pbo as { pbo?: number } | undefined)?.pbo ?? null;
  const trials = (validation?.trials as { n_trials?: number } | undefined)?.n_trials ?? null;
  const untested = rows.filter((s) => !s.metrics?.n_trades).length;
  const body = rows
    .map((s) => {
      const sh = s.metrics?.sharpe ?? null;
      const x2 = s.costs?.x2?.sharpe ?? null;
      const dsr = s.dsr?.dsr_prob ?? null;
      const trades = s.metrics?.n_trades ?? null;
      const never = !trades;
      return `<tr>
        <th scope="row">${escapeHtml(s.id)}<small>${escapeHtml(STRATEGY_NAMES[s.id] ?? s.name ?? "")}</small></th>
        <td>${escapeHtml(family(families.get(s.id) ?? null))}</td>
        <td class="n">${never ? span("t-dim", "never traded") : span(tone(sh), signedNum(sh))}</td>
        <td class="n">${never ? "" : span(tone(x2), signedNum(x2))}</td>
        <td class="n">${dsr === null ? EMPTY : percent(dsr, 0)}</td>
        <td class="n">${num(trades, 0)}</td>
        <td class="w t-dim">${
          never
            ? "not tested: the features it needs are not available over the window"
            : `weight zero: DSR under 95%, PBO above 0.5${x2 !== null && x2 < 0 ? ", negative at double costs" : ""}`
        }</td>
      </tr>`;
    })
    .join("");
  return section(
    "The gate's verdict",
    `<div class="t-scroll"><table class="t-table t-summary">
      <thead><tr><th>Strategy</th><th>Family</th><th class="n">Sharpe, out of sample</th><th class="n">at 2× costs</th>
        <th class="n">DSR</th><th class="n">Trades</th><th>Verdict</th></tr></thead>
      <tbody>${body}</tbody></table></div>
    <p class="t-dim">Walk-forward, out of sample, on the shadow account of each strategy. To get a weight a strategy needs a
    deflated Sharpe ratio (DSR) above ${percent(thresholds.dsr_min ?? 0.95, 0)} given the ${num(trials, 0)} variants tried, a
    probability of backtest overfitting (PBO) below ${num(thresholds.pbo_max ?? 0.5, 1)}, a positive out-of-sample Sharpe at
    double costs, at least ${num(thresholds.min_trades ?? 100, 0)} trades and ${num(thresholds.min_years ?? 3, 0)} years.
    The PBO of the whole set is ${num(pbo, 2)}: picking the best of these in sample would have lost out of sample
    ${percent((validation?.pbo as { prob_oos_loss?: number } | undefined)?.prob_oos_loss ?? null, 0)} of the time.
    None passed.${
      untested
        ? ` ${untested} of them never traded because the features they need (far curve months, inventories, macro
    series, the OPEC+ calendar, seasonality, options) are not available over the window: their theses were not
    refuted, they were not tested.`
        : ""
    } The verdict is a result, not a fault: the gate is not loosened to make a flat
    account trade.</p>`,
    `${rows.length} strategies · ${escapeHtml(relative(validation?.generated_at ?? null))}`,
  );
}

const span = (cls: string, text: string): string => `<span class="${cls}">${text}</span>`;

function documents(): string {
  const docs: [string, string, string][] = [
    ["docs/BRIEF.md", "The brief", "the requirements the first system was built to (Italian)"],
    ["docs/STRATEGIES.md", "Strategy theses", "each of the 21 strategies and each desk book, with its rule"],
    ["docs/RESEARCH.md", "The research", "every measurement, what was kept, what was rejected, every source"],
    ["docs/VALIDATION.md", "Validation", "the seven conditions and how each rule was checked by breaking it"],
    ["docs/DATA_SOURCES.md", "Data sources", "what each table is, where it comes from, how it was verified"],
  ];
  const files = ["summary.json", "validation.json", "strategies.json", "equity.json", "trades.json", "risk.json", "market.json", "forecasts.json"];
  return section(
    "Documents and data",
    `<div class="t-scroll"><table class="t-table t-log"><tbody>
      ${docs
        .map(
          ([path, title, note]) =>
            `<tr><td class="t-nowrap"><a href="${repoUrl()}/blob/main/${path}" target="_blank" rel="noopener">${escapeHtml(title)}</a></td><td class="w t-dim">${escapeHtml(note)}</td></tr>`,
        )
        .join("")}
    </tbody></table></div>
    <p class="t-dim">The first system's JSON is still written every day on the <code>data</code> branch:
    ${files
      .map((f) => `<a href="${repoUrl()}/blob/data/site-data/${f}" target="_blank" rel="noopener">${escapeHtml(f)}</a>`)
      .join(", ")}. The documents are in Italian; the engine, its comments and its tests are in English.</p>`,
  );
}

export async function renderArchive(el: HTMLElement): Promise<void> {
  const [summary, validation, strategies] = await Promise.all([
    load<SummaryDoc>("summary.json"),
    load<ValidationDoc>("validation.json"),
    load<StrategiesDoc>("strategies.json"),
  ]);
  const n = summary.data?.portfolio?.n_strategies ?? null;
  el.innerHTML = `<div class="term">
    ${summary.origin === "sample" ? '<div class="banner sample">SAMPLE DATA: this is not the real state of the account.</div>' : ""}
    <section class="t-sec">
      <h2 class="t-h"><span>The first system</span><small>still running every day, never traded</small></h2>
      <p>Before the desk, this repository ran ${n === null ? "its" : num(n, 0)} strategies on shadow accounts and promoted one to the
      master account only after seven validation conditions. Over fifteen years of walk-forward history none passed, so
      the master account stayed flat: by construction, not by failure. The desk's books replaced it on 8 October 2026;
      its end of day still runs after every London settlement, and its verdict stands as a result.</p>
    </section>
    ${master(summary.data, validation.data)}
    ${verdicts(validation.data, strategies.data)}
    ${regime(summary.data)}
    ${documents()}
  </div>`;
}
