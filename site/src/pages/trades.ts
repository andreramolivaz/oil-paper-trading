/** Operazioni: log con motivazioni ed export CSV. */
import { load } from "../data";
import { config, rawBaseUrl } from "../config";
import { dateTime, escapeHtml, num, reason, relative, signedUsd, usd } from "../format";
import { card, originBanner, pageTitle } from "../components/ui";
import type { TradeRow, TradesDoc } from "../types";

export async function renderTrades(el: HTMLElement): Promise<void> {
  const doc = await load<TradesDoc>("trades.json");
  const trades = (doc.data?.trades ?? []).slice().reverse();
  if (!trades.length) {
    el.innerHTML =
      pageTitle("Operazioni") +
      originBanner(doc, doc.data?.generated_at) +
      `<div class="banner warn">Nessuna operazione registrata: il conto master non ha ancora eseguito ordini.</div>`;
    return;
  }

  const rows = trades
    .map(
      (t) => `<tr>
        <td>${dateTime(t.ts)}</td>
        <td>${escapeHtml(t.instrument ?? "")}</td>
        <td class="num">${num(t.qty_bbl, 0)}</td>
        <td class="num">${usd(t.price)}</td>
        <td class="num">${t.slippage == null ? "—" : usd(t.slippage, 3)}</td>
        <td class="num">${t.commission == null ? "—" : usd(t.commission)}</td>
        <td class="num ${(t.realized_pnl ?? 0) >= 0 ? "up" : "down"}">${t.realized_pnl ? signedUsd(t.realized_pnl) : "—"}</td>
        <td>${escapeHtml(reason(t.reason))}</td>
        <td>${escapeHtml(t.regime ?? "—")}</td>
        <td class="num">${t.leverage == null ? "—" : `${num(t.leverage, 2)}x`}</td>
        <td>${gateChip(t)}</td>
        <td class="wrap-text">${escapeHtml(t.rationale ?? "—")}</td>
      </tr>`,
    )
    .join("");

  const csvRemote = `${rawBaseUrl()}/trades.csv`;
  el.innerHTML =
    pageTitle(
      "Operazioni",
      `${num(doc.data?.n_total, 0)} operazioni totali · mostrate le ultime ${trades.length} · ${relative(doc.data?.generated_at)}`,
    ) +
    originBanner(doc, doc.data?.generated_at) +
    card(
      "Log delle operazioni",
      `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <button class="btn secondary" id="btn-csv">Scarica CSV (questa pagina)</button>
        <a class="btn secondary" href="${escapeHtml(csvRemote)}" target="_blank" rel="noopener">CSV completo dal branch dati</a>
      </div>
      <div class="table-wrap"><table class="data">
        <thead><tr>
          <th>Data</th><th>Strumento</th><th>Qtà (bbl)</th><th>Prezzo</th><th>Slippage</th><th>Commissioni</th>
          <th>P&L</th><th>Motivo</th><th>Regime</th><th>Leva</th><th>Gate</th><th>Motivazione</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`,
    ) +
    card(
      "Come leggere il log",
      `<p class="muted">Ogni riga è un'esecuzione del broker simulato: il prezzo è il primo disponibile dopo il
       segnale, mai quello che lo ha generato, già corretto per spread e slippage. La colonna Gate dice se la
       leva oltre 1x era consentita; la motivazione riporta le strategie a favore e contro e il limite che ha
       determinato la leva. Il repository è ${escapeHtml(config.owner)}/${escapeHtml(config.repo)}.</p>`,
    );

  document.getElementById("btn-csv")?.addEventListener("click", () => downloadCsv(trades));
}

function gateChip(t: TradeRow): string {
  if (t.gate_passed === true) return '<span class="chip good">superato</span>';
  if (t.gate_passed === false) return '<span class="chip warn">non superato</span>';
  return "—";
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
