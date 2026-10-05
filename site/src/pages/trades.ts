/** Operazioni: il log del broker simulato, con motivazioni, provenienza ed export CSV.
 *
 * The log is EMPTY right now, and that absence IS the page's content: no strategy passed validation, so the
 * master never sent an order and there is nothing to fill. The empty state therefore has to carry the
 * reason — read from the engine's own portfolio explanation, never written here — or the page reads as a
 * failed fetch. Units live in the column headers; the cells carry bare figures so a column stays aligned.
 */
import { load } from "../data";
import { config, rawBaseUrl } from "../config";
import {
  DASH,
  EMPTY,
  barrels,
  contracts,
  dateTime,
  escapeHtml,
  leverage as fmtLev,
  money,
  num,
  reason,
  signedUsd,
  tone,
  withUnit,
} from "../format";
import { card, empty, originBanner, pageTitle, src, stat } from "../components/ui";
import type { Num, PortfolioBlock, SummaryDoc, TradeRow, TradesDoc } from "../types";

export async function renderTrades(el: HTMLElement): Promise<void> {
  // summary.json is already in the loader cache from the Home; it is read here only for the epoch and for
  // the engine's explanation of why the master is flat, which is what the empty log has to say.
  const [doc, summary] = await Promise.all([load<TradesDoc>("trades.json"), load<SummaryDoc>("summary.json")]);
  const d = doc.data;
  if (!d) {
    el.innerHTML = pageTitle("Operazioni") + originBanner(doc, null);
    return;
  }

  const trades = (d.trades ?? []).slice().reverse();
  const nTotal = d.n_total;
  const epoch = summary.data?.account?.epoch;
  const shown = trades.length;
  // The engine exports only the last MAX_TRADES fills: when the log is truncated the totals below are
  // totals OF THE ROWS SHOWN, and they have to say so.
  const partial = typeof nTotal === "number" && nTotal > shown;
  const subtitle = [
    shown
      ? `${contracts(nTotal)} operazioni totali · mostrate le ultime ${contracts(shown)}`
      : `${contracts(nTotal ?? 0)} operazioni registrate`,
    epoch == null ? "" : `epoca ${contracts(epoch)}`,
  ]
    .filter(Boolean)
    .join(" · ");

  const csvRemote = `${rawBaseUrl()}/trades.csv`;
  const toolbar = `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
    <button class="btn secondary sm" id="btn-csv"${shown ? "" : ' disabled title="Nessuna riga da esportare"'}>
      Scarica CSV (questa pagina)
    </button>
    <a class="btn secondary sm" href="${escapeHtml(csvRemote)}" target="_blank" rel="noopener">CSV completo dal branch dati</a>
  </div>`;

  const logBody = shown
    ? totalsStrip(trades, nTotal, partial) + toolbar + table(trades)
    : toolbar +
      empty(
        "Nessuna operazione: il broker simulato non ha ancora registrato un fill.",
        "Non è un errore di caricamento. Senza strategie promosse il master resta in contanti, quindi non esiste nessun ordine da eseguire: il log si apre al primo fill.",
      );

  el.innerHTML =
    pageTitle("Operazioni", subtitle) +
    originBanner(doc, d.generated_at) +
    (shown ? "" : flatNotice(summary.data?.portfolio)) +
    card("Log delle operazioni", logBody, src(d.source, d.generated_at)) +
    card(
      "Come leggere il log",
      `<p class="muted" style="margin:0">Ogni riga è un'esecuzione del broker simulato: il prezzo è il primo disponibile dopo il
       segnale, mai quello che lo ha generato, già corretto per spread e slippage. La colonna Gate dice se la
       leva oltre 1x era consentita; la motivazione riporta le strategie a favore e contro e il limite che ha
       determinato la leva. Il repository è ${escapeHtml(config.owner)}/${escapeHtml(config.repo)}.</p>
      <p class="muted" style="margin:8px 0 0">Le unità stanno nelle intestazioni: quantità in barili, prezzo e slippage in $/bbl,
       commissioni e P&amp;L realizzato in dollari. Lo slippage è il costo per barile applicato al fill. Il CSV del branch dati
       contiene il log completo con gli orari in UTC, separatore punto e virgola, pronto per un foglio di calcolo.</p>`,
    );

  if (shown) document.getElementById("btn-csv")?.addEventListener("click", () => downloadCsv(trades));
}

/** Why there is nothing to show. The text is the engine's own, with its source and its asof. */
function flatNotice(p: PortfolioBlock | null | undefined): string {
  if (!p?.explanation) return "";
  const flat = Boolean(p.master_flat_by_design);
  return `<div class="banner stack ${flat ? "warn" : "info"}">
    <strong>${flat ? "Il master è fermo per costruzione" : `${contracts(p.n_active)} strategie attive nel master`}</strong>
    <p style="margin:6px 0 0">${escapeHtml(p.explanation)}</p>
    <p class="muted" style="margin:8px 0 0">${src(p.source, p.asof)}</p>
  </div>`;
}

/** Totals of the rows on screen. A column of nulls must not become a confident 0,00 $. */
function totalsStrip(trades: TradeRow[], nTotal: Num | undefined, partial: boolean): string {
  const suffix = partial ? " (mostrate)" : "";
  const anyPnl = trades.some((t) => t.realized_pnl != null);
  const anyComm = trades.some((t) => t.commission != null);
  const anySlip = trades.some((t) => t.slippage != null);
  const pnl = trades.reduce((s, t) => s + val(t.realized_pnl), 0);
  const comm = trades.reduce((s, t) => s + val(t.commission), 0);
  // The broker charges slippage per barrel (fill.slippage = cost_per_bbl), so the dollars paid are
  // cost_per_bbl * |qty| — the same accumulation the broker keeps as total_slippage_usd.
  const slip = trades.reduce((s, t) => s + val(t.slippage) * Math.abs(val(t.qty_bbl)), 0);
  const volume = trades.reduce((s, t) => s + Math.abs(val(t.qty_bbl)), 0);
  return `<div class="strip" style="margin-bottom:10px">
    ${stat("Operazioni totali", contracts(nTotal))}
    ${stat(`P&L realizzato${suffix}`, anyPnl ? withUnit(signedUsd(pnl)) : DASH, tone(anyPnl ? pnl : null))}
    ${stat(`Commissioni${suffix}`, anyComm ? withUnit(money(comm)) : DASH)}
    ${stat(`Slippage pagato${suffix}`, anySlip ? withUnit(money(slip)) : DASH)}
    ${stat(`Volume${suffix}`, withUnit(barrels(volume)))}
  </div>`;
}

function table(trades: TradeRow[]): string {
  const rows = trades
    .map(
      (t) => `<tr>
        <td>${dateTime(t.ts)}</td>
        <td>${escapeHtml(t.instrument ?? DASH)}</td>
        <td class="num">${cell(contracts(t.qty_bbl))}</td>
        <td class="num">${cell(num(t.price))}</td>
        <td class="num">${cell(num(t.slippage, 3))}</td>
        <td class="num">${cell(num(t.commission))}</td>
        <td class="num ${tone(t.realized_pnl)}">${signedNum(t.realized_pnl)}</td>
        <td>${escapeHtml(reason(t.reason))}</td>
        <td>${escapeHtml(t.regime ?? DASH)}</td>
        <td class="num">${cell(fmtLev(t.leverage))}</td>
        <td>${gateChip(t)}</td>
        <td class="wrap-text">${escapeHtml(t.rationale ?? DASH)}</td>
      </tr>`,
    )
    .join("");
  return `<div class="table-wrap tall"><table class="data">
    <thead><tr>
      <th>Data e ora <span class="unit">Europa/Roma</span></th>
      <th>Strumento</th>
      <th class="num">Qtà <span class="unit">bbl</span></th>
      <th class="num">Prezzo <span class="unit">$/bbl</span></th>
      <th class="num">Slippage <span class="unit">$/bbl</span></th>
      <th class="num">Commissioni <span class="unit">$</span></th>
      <th class="num">P&amp;L realizzato <span class="unit">$</span></th>
      <th>Motivo</th>
      <th>Regime</th>
      <th class="num">Leva</th>
      <th>Gate</th>
      <th class="wrap-text">Motivazione</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

/** A figure whose unit is already in the column header; num() renders the real minus sign, so only the
 *  plus has to be added. A realized P&L of exactly zero is a result and stays "0,00", uncoloured. */
function signedNum(value: Num | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return value > 0 ? `+${num(value)}` : num(value);
}

/** In a table a missing figure is a dash, not the "n/d" a tile uses. */
function cell(formatted: string): string {
  return formatted === EMPTY ? DASH : formatted;
}

function val(value: Num | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function gateChip(t: TradeRow): string {
  if (t.gate_passed === true) return '<span class="chip good">superato</span>';
  if (t.gate_passed === false) return '<span class="chip warn">non superato</span>';
  return DASH;
}

function downloadCsv(trades: TradeRow[]): void {
  const headers: [keyof TradeRow, string][] = [
    ["ts", "Data e ora (UTC)"],
    ["instrument", "Strumento"],
    ["qty_bbl", "Quantità (barili)"],
    ["price", "Prezzo eseguito"],
    ["reference_price", "Prezzo di riferimento"],
    ["slippage", "Slippage"],
    ["commission", "Commissioni"],
    ["realized_pnl", "P&L realizzato"],
    ["reason", "Motivo"],
    ["regime", "Regime"],
    ["leverage", "Leva"],
    ["leverage_limited_by", "Leva limitata da"],
    ["gate_passed", "Gate superato"],
    ["rationale", "Motivazione"],
  ];
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[";\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  const lines = [headers.map(([, label]) => label).join(";")];
  for (const t of trades) lines.push(headers.map(([key]) => esc(t[key])).join(";"));
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "operazioni.csv";
  a.click();
  URL.revokeObjectURL(url);
}
