/** Home. It answers two questions before anything else: how much money is there, and how is it going.
 *
 * Everything else on this page is context for those two figures, in this order:
 *   conto → mercato → andamento → perché il master è fermo → esposizione → stato dei dati.
 * Brent used to own the hero slot, which answered a question nobody opened the page to ask.
 */
import { type ChartPoint, addArea, addLine, attachReadout, baseChart, cssVar, fit, toLineData } from "../charts/base";
import { load } from "../data";
import {
  escapeHtml,
  leverage as fmtLev,
  money,
  notional,
  num,
  percent,
  price,
  relative,
  signedPercent,
  signedUsd,
  stamp,
  tone,
  withUnit,
} from "../format";
import {
  card,
  chartBlock,
  healthTable,
  originBanner,
  originChip,
  src,
  stat,
  statusChip,
  tile,
} from "../components/ui";
import type { EquityDoc, GateBlock, GateCondition, PortfolioBlock, SummaryDoc } from "../types";

export async function renderHome(el: HTMLElement): Promise<void> {
  const [summary, equity] = await Promise.all([load<SummaryDoc>("summary.json"), load<EquityDoc>("equity.json")]);
  const s = summary.data;
  if (!s) {
    el.innerHTML = `<div class="banner bad">⚠ Dati non disponibili: il motore non ha ancora pubblicato <code>summary.json</code>${
      summary.error ? ` (${escapeHtml(summary.error)})` : ""
    }.</div>`;
    return;
  }
  const acct = s.account ?? {};
  const brent = s.brent ?? { value: null };
  const lev = acct.leverage ?? {};
  const gate = acct.gate ?? {};
  const dead = Boolean(acct.dead);
  const initial = acct.initial_capital ?? 10000;

  // ---------------------------------------------------------------- conto
  const resetHero = dead
    ? `<section class="card" style="border-color:var(--critical)">
        <header><h2 style="color:var(--critical-ink)">Conto azzerato: trading fermo</h2></header>
        <p>L'equity è scesa sotto la soglia minima operativa. Il motore non apre nuove posizioni fino al reset.</p>
        <a class="btn danger block" href="#/reset">Reset a ${notional(initial)}</a>
      </section>`
    : "";

  const accountHero = `<div class="hero" style="margin-bottom:12px">
    <div class="label">Conto · epoca ${acct.epoch ?? "n/d"}</div>
    <span class="value">${withUnit(money(acct.equity))}</span>
    <div class="delta">
      ${stat("P&L totale", `${signedUsd(acct.pnl_total)} <span class="pct">${signedPercent(acct.pnl_total_pct)}</span>`, tone(acct.pnl_total))}
      ${stat("P&L giorno", signedUsd(acct.pnl_day), tone(acct.pnl_day))}
      ${stat("Drawdown", percent(acct.drawdown), acct.drawdown ? "down" : "")}
      ${stat("Capitale iniziale", notional(initial))}
    </div>
    <div class="sub" style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      ${src("engine/broker", acct.asof)}
      ${originChip(summary.origin)}
      ${statusChip(s.data_status?.overall)}
    </div>
  </div>`;

  // ---------------------------------------------------------------- mercato
  const marketStrip = `<div class="strip" style="margin-bottom:12px">
    ${stat("Brent front", withUnit(price(brent.value)))}
    ${stat("Var 1g", signedPercent(brent.change_1d), tone(brent.change_1d))}
    ${brent.ovx?.value != null ? stat("OVX", num(brent.ovx.value, 1)) : ""}
    ${brent.spot?.value != null ? stat("Spot EIA", withUnit(price(brent.spot.value))) : ""}
    ${
      s.regime?.label
        ? stat(
            "Regime",
            `${s.regime.approx ? "≈ " : ""}${escapeHtml(s.regime.label)}${
              s.regime.confidence != null ? ` <span class="unit">${percent(s.regime.confidence, 0)}</span>` : ""
            }`,
            s.regime.approx ? "approx" : "",
          )
        : ""
    }
  </div>`;

  // ---------------------------------------------------------------- andamento
  // The engine ticks every 30 minutes, so an account one day old still has a curve. Plotting by calendar
  // date collapsed it to a single point and the page showed an empty state on a dashboard that had data.
  const series = equity.data?.series ?? [];
  const master = toLineData(
    series.map((p) => ({ t: p.t, v: p.master })),
    { intraday: true },
  );
  const buyHold = toLineData(
    series.map((p) => ({ t: p.t, v: p.buy_hold_brent ?? null })),
    { intraday: true },
  );
  const hasCurve = master.length > 1;
  const equityCard = card(
    "Andamento del conto",
    chartBlock({
      id: "equity-chart",
      hasData: hasCurve,
      emptyMessage: "Il conto non ha ancora una storia da disegnare.",
      emptyHint: "La curva compare dal secondo aggiornamento: il motore registra l'equity ogni 30 minuti.",
      legendItems: [
        { label: "Master (paper)", color: cssVar("--series-1") },
        ...(buyHold.length > 1 ? [{ label: "Buy & hold Brent", color: cssVar("--series-2"), dashed: true }] : []),
      ],
      source: "engine/broker",
      asof: acct.asof,
    }),
    flatCurve(master) ? `<span class="chip">piatto · nessuna operazione</span>` : "",
  );

  // ---------------------------------------------------------------- perché è fermo
  const portfolio = portfolioBlock(s.portfolio ?? null);

  // ---------------------------------------------------------------- esposizione
  const exposure = `<div class="tiles" style="margin-bottom:12px">
    ${tile("Leva", fmtLev(lev.value), escapeHtml(lev.limited_by ?? "n/d"))}
    ${tile("Nozionale lordo", notional(acct.gross_notional), `netto ${notional(acct.net_notional)}`)}
    ${tile("Margine", notional(acct.margin_used), acct.margin_level != null ? `livello ${num(acct.margin_level, 2)}` : "nessuna posizione")}
    ${tile("Prezzo di liquidazione", price(acct.liquidation_price), "stima al margine 10%", "", "richiede una posizione aperta")}
  </div>`;

  const positions = (acct.positions ?? []).length
    ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>Strumento</th><th class="num">Quantità</th><th class="num">Prezzo medio</th><th class="num">Ultimo</th><th class="num">Stop</th><th>Strategie</th></tr></thead>
        <tbody>${(acct.positions ?? [])
          .map(
            (p) => `<tr>
              <td>${escapeHtml(p.instrument ?? "")}</td>
              <td class="num">${num(p.qty_bbl, 0)} bbl</td>
              <td class="num">${price(p.avg_price)}</td>
              <td class="num">${price(p.last_price)}</td>
              <td class="num">${p.stop_price == null ? "—" : price(p.stop_price)}</td>
              <td>${escapeHtml((p.strategies ?? []).join(", ") || "—")}</td>
            </tr>`,
          )
          .join("")}</tbody></table></div>`
    : `<p class="muted" style="margin:0">Nessuna posizione aperta: il master è in contanti.</p>`;

  const levComponents = lev.components
    ? `<details style="margin-top:10px"><summary>Come è stata scelta la leva</summary>
        <dl class="kv" style="margin-top:10px">
          ${Object.entries(lev.components)
            .map(([k, v]) => `<dt>${escapeHtml(componentLabel(k))}</dt><dd>${v == null ? "—" : fmtLev(v)}</dd>`)
            .join("")}
        </dl>
        <p class="muted" style="margin-top:8px">La leva effettiva è il minimo fra le componenti; oltre 1x solo con il gate superato.</p>
      </details>`
    : "";

  const run = s.last_run ?? {};
  const runBlock = `<dl class="kv">
    <dt>Ultimo job</dt><dd class="text">${escapeHtml(run.job ?? "n/d")} · ${escapeHtml(run.status ?? "n/d")}</dd>
    <dt>Eseguito</dt><dd class="text">${run.ts ? `${relative(run.ts)} · ${stamp(run.ts)}` : "n/d"}</dd>
    <dt>Ritardo cron</dt><dd>${run.cron_lag_minutes == null ? "—" : `${num(run.cron_lag_minutes, 0)} min`}</dd>
  </dl>`;

  el.innerHTML =
    originBanner(summary, s.generated_at) +
    resetHero +
    accountHero +
    marketStrip +
    equityCard +
    portfolio +
    card("Esposizione", exposure + gateLine(gate) + positions + levComponents) +
    card(
      "Stato dei dati",
      healthTable(s.data_status?.sources) + `<div style="margin-top:12px">${runBlock}</div>`,
      src("engine/monitoring", s.data_status?.checked_at),
    );

  if (hasCurve) {
    const chartEl = document.getElementById("equity-chart");
    const strip = document.getElementById("equity-chart-readout");
    if (chartEl) {
      const chart = baseChart(chartEl, { timeVisible: !spansDays(series), priceFormatter: (v) => num(v, 0) });
      const masterSeries = addArea(chart, master as ChartPoint[], cssVar("--series-1"), 0.14, { precision: 0 });
      const readout: Parameters<typeof attachReadout>[2] = [
        { api: masterSeries, label: "Master", color: cssVar("--series-1"), fmt: (v) => money(v) },
      ];
      if (buyHold.length > 1) {
        const bh = addLine(chart, buyHold, cssVar("--series-2"), { dashed: true, precision: 0 });
        readout.push({ api: bh, label: "Buy & hold", color: cssVar("--series-2"), fmt: (v) => money(v) });
      }
      if (strip) attachReadout(chart, strip, readout);
      fit(chart);
    }
  }
}

/** A curve that never moves is a result, not a rendering failure: label it so nobody reads it as a bug. */
function flatCurve(points: { value: number }[]): boolean {
  if (points.length < 2) return false;
  const values = points.map((p) => p.value);
  return Math.max(...values) === Math.min(...values);
}

/** True when the equity curve spans more than one calendar day: only then does a date axis say anything. */
function spansDays(series: { t: string }[]): boolean {
  return new Set(series.map((p) => String(p.t).slice(0, 10))).size > 1;
}

/** Why the master holds what it holds: with nothing promoted, "flat" is the designed outcome, not a gap. */
function portfolioBlock(p: PortfolioBlock | null): string {
  if (!p || !p.explanation) return "";
  const flat = Boolean(p.master_flat_by_design);
  const counts = Object.entries(p.lifecycle_counts ?? {})
    .map(([k, v]) => `${v} ${escapeHtml(lifecycleLabel(k))}`)
    .join(" · ");
  return `<div class="banner stack ${flat ? "warn" : "info"}" style="margin-bottom:12px">
    <strong>${flat ? "Nessuna strategia attiva: il master resta fermo" : `${num(p.n_active, 0)} strategie attive nel master`}</strong>
    <p style="margin:6px 0 0">${escapeHtml(p.explanation)}</p>
    <p class="muted" style="margin:8px 0 0">${counts ? `${counts} · ` : ""}${src(p.source, p.asof)}</p>
  </div>`;
}

/** The gate decides whether leverage may exceed 1x. "Limitata dal Kelly" is not an answer on its own. */
function gateLine(gate: GateBlock): string {
  if (gate.passed === undefined || gate.passed === null) return "";
  const conditions = Array.isArray(gate.conditions)
    ? gate.conditions
    : Object.values((gate.conditions ?? {}) as Record<string, GateCondition>);
  const met = conditions.filter((c) => c?.ok).length;
  const total = conditions.length;
  const chip = gate.passed
    ? `<span class="chip good">gate superato</span>`
    : `<span class="chip warn">gate non superato${total ? ` · ${met}/${total}` : ""}</span>`;
  const detail = gate.reason
    ? `<details style="margin-top:8px"><summary>Perché</summary><p class="muted" style="margin-top:6px">${escapeHtml(gate.reason)}</p></details>`
    : "";
  return `<div style="margin:12px 0 10px">${chip} <span class="muted">oltre 1x solo con tutte le condizioni soddisfatte</span>${detail}</div>`;
}

function lifecycleLabel(key: string): string {
  const map: Record<string, string> = {
    research: "in ricerca",
    incubation: "in incubazione",
    active: "attive",
    retired: "ritirate",
  };
  return map[key] ?? key;
}

function componentLabel(key: string): string {
  const map: Record<string, string> = {
    kelly: "Kelly frazionario",
    vol: "Budget di volatilità (ES 99%)",
    drawdown: "Drawdown",
    event: "Eventi in calendario",
    cap: "Tetto assoluto",
    default: "Default senza gate",
    gate: "Gate alpha",
  };
  return map[key] ?? key;
}
