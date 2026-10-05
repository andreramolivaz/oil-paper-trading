/** Home: price, regime, data status, account, leverage with its reason, equity against buy and hold. */
import { addLine, baseChart, cssVar, fit, toLineData } from "../charts/base";
import { load } from "../data";
import { dateTime, escapeHtml, leverage as fmtLev, num, percent, relative, signedPercent, signedUsd, usd } from "../format";
import { card, empty, healthTable, legend, originBanner, originChip, pageTitle, src, statusChip, tile } from "../components/ui";
import type { EquityDoc, SummaryDoc } from "../types";

export async function renderHome(el: HTMLElement): Promise<void> {
  const [summary, equity] = await Promise.all([load<SummaryDoc>("summary.json"), load<EquityDoc>("equity.json")]);
  const s = summary.data;
  if (!s) {
    el.innerHTML =
      pageTitle("Home") +
      `<div class="banner bad">⚠ Dati non disponibili: il motore non ha ancora pubblicato <code>summary.json</code>${
        summary.error ? ` (${escapeHtml(summary.error)})` : ""
      }.</div>`;
    return;
  }
  const acct = s.account ?? {};
  const brent = s.brent ?? { value: null };
  const lev = acct.leverage ?? {};
  const dead = Boolean(acct.dead);
  const chg = brent.change_1d ?? null;

  const hero = `<div class="hero" style="margin-bottom:12px">
    <div class="label muted">Brent (front month)</div>
    <div class="value">${brent.value === null ? "n/d" : usd(brent.value)}</div>
    <div class="sub">
      <span class="${chg !== null && chg < 0 ? "down" : chg !== null && chg > 0 ? "up" : ""}">${signedPercent(chg)}</span>
      · ${src(brent.source, brent.asof)}
      ${brent.ovx?.value != null ? ` · OVX ${num(brent.ovx.value, 1)} ${src(brent.ovx.source, brent.ovx.asof)}` : ""}
      ${brent.spot?.value != null ? ` · spot ${usd(brent.spot.value)} ${src(brent.spot.source, brent.spot.asof)}` : ""}
    </div>
    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
      ${statusChip(s.data_status?.overall)}
      ${
        s.regime?.label
          ? `<span class="chip info">${escapeHtml(s.regime.label)}${
              s.regime.confidence != null ? ` · ${percent(s.regime.confidence, 0)}` : ""
            }</span>`
          : `<span class="chip">regime n/d</span>`
      }
      ${originChip(summary.origin)}
    </div>
  </div>`;

  const resetHero = dead
    ? `<section class="card" style="border-color:var(--critical)">
        <header><h2 style="color:var(--critical)">Conto azzerato: trading fermo</h2></header>
        <p>L'equity è scesa sotto la soglia minima operativa. Il motore non apre nuove posizioni fino al reset.</p>
        <a class="btn danger block" href="#/reset">Reset a ${usd(acct.initial_capital ?? 10000, 0)}</a>
      </section>`
    : "";

  const tiles = `<div class="tiles" style="margin-bottom:12px">
    ${tile("Equity", usd(acct.equity), `epoca ${acct.epoch ?? "n/d"} · ${src("engine/broker", acct.asof)}`)}
    ${tile("P&L totale", signedUsd(acct.pnl_total), signedPercent(acct.pnl_total_pct), (acct.pnl_total ?? 0) >= 0 ? "up" : "down")}
    ${tile("P&L giorno", signedUsd(acct.pnl_day), "dalla sessione precedente", (acct.pnl_day ?? 0) >= 0 ? "up" : "down")}
    ${tile("Drawdown", percent(acct.drawdown), `picco ${usd(acct.peak_equity)}`)}
  </div>
  <div class="tiles" style="margin-bottom:12px">
    ${tile("Leva", fmtLev(lev.value), escapeHtml(lev.limited_by ?? "n/d"))}
    ${tile("Nozionale lordo", usd(acct.gross_notional, 0), `netto ${usd(acct.net_notional, 0)}`)}
    ${tile("Margine", usd(acct.margin_used, 0), acct.margin_level != null ? `livello ${num(acct.margin_level, 2)}` : "n/d")}
    ${tile("Prezzo di liquidazione", acct.liquidation_price == null ? "n/d" : usd(acct.liquidation_price), "stima al margine 10%")}
  </div>`;

  const levComponents = lev.components
    ? `<details><summary>Come è stata scelta la leva</summary>
        <dl class="kv" style="margin-top:8px">
          ${Object.entries(lev.components)
            .map(([k, v]) => `<dt>${escapeHtml(componentLabel(k))}</dt><dd>${v == null ? "n/d" : fmtLev(v)}</dd>`)
            .join("")}
        </dl>
        <p class="muted" style="margin-top:8px">La leva effettiva è il minimo fra le componenti; oltre 1x solo con il gate superato.</p>
      </details>`
    : "";

  const positions = (acct.positions ?? []).length
    ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>Strumento</th><th>Quantità</th><th>Prezzo medio</th><th>Ultimo</th><th>Stop</th><th>Strategie</th></tr></thead>
        <tbody>${(acct.positions ?? [])
          .map(
            (p) => `<tr>
              <td>${escapeHtml(p.instrument ?? "")}</td>
              <td class="num">${num(p.qty_bbl, 0)} bbl</td>
              <td class="num">${usd(p.avg_price)}</td>
              <td class="num">${usd(p.last_price)}</td>
              <td class="num">${p.stop_price == null ? "—" : usd(p.stop_price)}</td>
              <td>${escapeHtml((p.strategies ?? []).join(", ") || "—")}</td>
            </tr>`,
          )
          .join("")}</tbody></table></div>`
    : empty("Nessuna posizione aperta.");

  const run = s.last_run ?? {};
  const runBlock = `<dl class="kv">
    <dt>Ultimo job</dt><dd>${escapeHtml(run.job ?? "n/d")} · ${escapeHtml(run.status ?? "n/d")}</dd>
    <dt>Eseguito</dt><dd>${run.ts ? `${relative(run.ts)} (${dateTime(run.ts)})` : "n/d"}</dd>
    <dt>Ritardo cron</dt><dd>${run.cron_lag_minutes == null ? "n/d" : `${num(run.cron_lag_minutes, 0)} min`}</dd>
    <dt>Messaggio</dt><dd style="text-align:left">${escapeHtml(run.message ?? "—")}</dd>
  </dl>`;

  el.innerHTML =
    pageTitle("Home", `Aggiornato ${relative(s.generated_at)} · ${dateTime(s.generated_at)}`) +
    originBanner(summary, s.generated_at) +
    resetHero +
    hero +
    tiles +
    card("Equity contro buy & hold", `<div id="equity-chart" class="chart"></div>${
      legend([
        { label: "Master (paper)", color: cssVar("--series-1") },
        { label: "Buy & hold Brent", color: cssVar("--series-2"), dashed: true },
      ])
    }${equity.data?.note ? `<p class="muted">${escapeHtml(equity.data.note)}</p>` : ""}`) +
    card("Posizione e leva", positions + levComponents) +
    card("Stato dei dati", healthTable(s.data_status?.sources) + `<div style="margin-top:10px">${runBlock}</div>`);

  const chartEl = document.getElementById("equity-chart");
  const series = equity.data?.series ?? [];
  if (chartEl && series.length > 1) {
    const chart = baseChart(chartEl);
    addLine(chart, toLineData(series.map((p) => ({ t: p.t, v: p.master }))), cssVar("--series-1"), { title: "Master" });
    const bh = toLineData(series.map((p) => ({ t: p.t, v: p.buy_hold_brent ?? null })));
    if (bh.length > 1) addLine(chart, bh, cssVar("--series-2"), { dashed: true, title: "Buy & hold" });
    fit(chart);
  } else if (chartEl) {
    chartEl.outerHTML = empty("Storico equity non ancora disponibile: serve almeno un ciclo di paper trading.");
  }
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
