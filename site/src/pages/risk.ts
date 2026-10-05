/** Rischio: VaR ed ES, storico della leva, margini, rischio di rovina stimato, stress test. */
import { addLine, baseChart, cssVar, fit, toLineData } from "../charts/base";
import { load } from "../data";
import { escapeHtml, num, percent, relative, usd } from "../format";
import { card, empty, legend, originBanner, pageTitle } from "../components/ui";
import type { EquityDoc, RiskDoc, SummaryDoc } from "../types";

export async function renderRisk(el: HTMLElement): Promise<void> {
  const [risk, equity, summary] = await Promise.all([
    load<RiskDoc>("risk.json"),
    load<EquityDoc>("equity.json"),
    load<SummaryDoc>("summary.json"),
  ]);
  const r = risk.data;
  const acct = summary.data?.account ?? {};
  const series = equity.data?.series ?? [];

  const levHistory = series
    .map((p) => ({ t: p.t, v: p.leverage ?? null }))
    .filter((p) => p.v !== null) as { t: string; v: number }[];

  const marginBlock = `<dl class="kv">
    <dt>Margine richiesto</dt><dd>${usd(acct.margin_used, 0)}</dd>
    <dt>Livello di margine (equity / margine)</dt><dd>${acct.margin_level == null ? "n/d" : num(acct.margin_level, 2)}</dd>
    <dt>Soglia di stop-out</dt><dd>0,50</dd>
    <dt>Prezzo di liquidazione stimato</dt><dd>${acct.liquidation_price == null ? "n/d" : usd(acct.liquidation_price)}</dd>
    <dt>Nozionale lordo</dt><dd>${usd(acct.gross_notional, 0)}</dd>
  </dl>
  <p class="muted">Il margine richiesto è il 10% del nozionale, coerente con il tetto di 10x. Sotto un livello di
  margine di 0,50 il broker simulato liquida con slippage peggiorativo.</p>`;

  const varBlock =
    r && (r.var || r.es)
      ? `<dl class="kv">
          ${Object.entries(r.var ?? {})
            .map(([k, v]) => `<dt>VaR ${escapeHtml(k)}</dt><dd>${v == null ? "n/d" : percent(v, 2)}</dd>`)
            .join("")}
          ${Object.entries(r.es ?? {})
            .map(([k, v]) => `<dt>ES ${escapeHtml(k)}</dt><dd>${v == null ? "n/d" : percent(v, 2)}</dd>`)
            .join("")}
        </dl>`
      : `<div class="banner warn">VaR ed Expected Shortfall non ancora pubblicati: li calcola il job settimanale
         (<code>weekly</code>) insieme al rischio di rovina.</div>`;

  const ruinBlock = r?.ruin
    ? `<dl class="kv">${Object.entries(r.ruin as Record<string, unknown>)
        .filter(([, v]) => typeof v === "number" || typeof v === "string")
        .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(formatValue(v))}</dd>`)
        .join("")}</dl>
       <p class="muted">Stima Monte Carlo con bootstrap a blocchi sui rendimenti reali del Brent e scenari sintetici
       di riapertura ed escalation. Il vincolo di calibrazione è una probabilità di rovina sotto il 5%.</p>`
    : `<div class="banner warn">Rischio di rovina non ancora stimato.</div>`;

  const stressBlock = r?.stress
    ? stressTable(r.stress as Record<string, unknown>)
    : `<div class="banner warn">Stress test non ancora pubblicati: confrontano la posizione attuale con la Guerra del
       Golfo 1990-91, Abqaiq 2019, Covid 2020, l'invasione russa del 2022 e scenari sintetici.</div>`;

  el.innerHTML =
    pageTitle("Rischio", `Aggiornato ${relative(r?.generated_at ?? summary.data?.generated_at)}`) +
    originBanner(risk, r?.generated_at) +
    card("Margini e liquidazione", marginBlock) +
    card(
      "Storico della leva",
      levHistory.length > 1
        ? `<div id="lev-chart" class="chart short"></div>${legend([{ label: "Leva effettiva", color: cssVar("--series-4") }])}
           <p class="muted">Il tetto assoluto è 10x e sopra 1x serve il gate alpha: in pratica la leva resta vicina
           a 1x fino a quando la volatilità non scende.</p>`
        : empty("Storico della leva non ancora disponibile."),
    ) +
    card("VaR ed Expected Shortfall", varBlock) +
    card("Rischio di rovina", ruinBlock) +
    card("Stress test", stressBlock);

  const levEl = document.getElementById("lev-chart");
  if (levEl && levHistory.length > 1) {
    const chart = baseChart(levEl);
    addLine(chart, toLineData(levHistory), cssVar("--series-4"));
    fit(chart);
  }
}

function formatValue(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? num(v, 0) : num(v, 4);
  return String(v);
}

function stressTable(stress: Record<string, unknown>): string {
  const rows = Array.isArray(stress["episodes"]) ? (stress["episodes"] as Record<string, unknown>[]) : [];
  const scenarios = Array.isArray(stress["scenarios"]) ? (stress["scenarios"] as Record<string, unknown>[]) : [];
  const all = [...rows, ...scenarios];
  if (!all.length) return empty("Nessuno stress test disponibile.");
  const keys = ["name", "label", "total_return", "max_drawdown", "worst_day", "margin_call", "liquidated", "available"];
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>Scenario</th><th>Rendimento</th><th>Max DD</th><th>Giorno peggiore</th><th>Margin call</th><th>Liquidazione</th></tr></thead>
    <tbody>${all
      .map((e) => {
        const name = String(e["name"] ?? e["label"] ?? e["id"] ?? "—");
        const avail = e["available"];
        if (avail === false) {
          return `<tr><td>${escapeHtml(name)}</td><td colspan="5" class="muted">non disponibile: ${escapeHtml(
            String(e["reason"] ?? "dati insufficienti"),
          )}</td></tr>`;
        }
        return `<tr>
          <td>${escapeHtml(name)}${e["synthetic"] ? ' <span class="chip warn">sintetico</span>' : ""}</td>
          <td class="num">${cell(e["total_return"])}</td>
          <td class="num">${cell(e["max_drawdown"])}</td>
          <td class="num">${cell(e["worst_day"])}</td>
          <td>${e["margin_call"] ? '<span class="chip bad">sì</span>' : "no"}</td>
          <td>${e["liquidated"] ? '<span class="chip bad">sì</span>' : "no"}</td>
        </tr>`;
      })
      .join("")}</tbody></table></div>
    <p class="muted">Gli scenari sintetici sono etichettati come tali: sono ipotesi di stress, non dati di mercato.
    ${keys.length ? "" : ""}</p>`;
}

function cell(v: unknown): string {
  return typeof v === "number" ? percent(v, 1) : "n/d";
}
