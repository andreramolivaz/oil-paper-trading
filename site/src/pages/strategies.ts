/** Strategie: classifica dei conti ombra e scheda di dettaglio con la tesi. */
import { load } from "../data";
import { dateTime, escapeHtml, family, lifecycle, num, percent, relative } from "../format";
import { card, empty, originBanner, pageTitle } from "../components/ui";
import type { StrategiesDoc, StrategyRow } from "../types";

export async function renderStrategies(el: HTMLElement): Promise<void> {
  const doc = await load<StrategiesDoc>("strategies.json");
  const rows = doc.data?.strategies ?? [];
  const hash = window.location.hash.replace(/^#/, "");
  const detailId = hash.startsWith("/strategie/") ? hash.slice("/strategie/".length) : null;

  if (!rows.length) {
    el.innerHTML =
      pageTitle("Strategie") +
      originBanner(doc, doc.data?.generated_at) +
      `<div class="banner warn">Nessuna strategia pubblicata: il job di fine giornata scrive la classifica dei conti ombra.</div>`;
    return;
  }

  if (detailId) {
    const row = rows.find((r) => r.id === detailId);
    if (row) {
      el.innerHTML = detail(row);
      return;
    }
  }

  const sorted = [...rows].sort((a, b) => sharpe(b) - sharpe(a));
  const table = `<div class="table-wrap"><table class="data">
    <thead><tr>
      <th>Strategia</th><th>Famiglia</th><th>Stato</th><th>Peso</th><th>Sharpe</th><th>Sortino</th>
      <th>Max DD</th><th>Hit rate</th><th>Profit factor</th><th>Trade</th><th>Segnale</th>
    </tr></thead>
    <tbody>${sorted
      .map((r) => {
        const p = r.performance ?? {};
        return `<tr>
          <td><a href="#/strategie/${encodeURIComponent(r.id)}">${escapeHtml(r.id)} · ${escapeHtml(r.name ?? "")}</a></td>
          <td>${escapeHtml(family(r.family))}</td>
          <td><span class="chip ${r.lifecycle === "active" ? "good" : r.lifecycle === "retired" ? "bad" : "warn"}">${escapeHtml(lifecycle(r.lifecycle))}</span></td>
          <td class="num">${percent(r.weight, 0)}</td>
          <td class="num">${metric(p["sharpe"], 2)}</td>
          <td class="num">${metric(p["sortino"], 2)}</td>
          <td class="num">${metricPct(p["max_drawdown"])}</td>
          <td class="num">${metricPct(p["hit_rate"])}</td>
          <td class="num">${metric(p["profit_factor"], 2)}</td>
          <td class="num">${metric(p["n_trades"], 0)}</td>
          <td>${signalChip(r)}</td>
        </tr>`;
      })
      .join("")}</tbody></table></div>`;

  const zeroWeight = sorted.filter((r) => (r.weight ?? 0) === 0);
  const note = zeroWeight.length
    ? `<p class="muted">${zeroWeight.length} strategie hanno peso zero nel master: operano solo sul conto ombra
       finché la validazione non le promuove. Il dettaglio spiega perché.</p>`
    : "";

  el.innerHTML =
    pageTitle(
      "Strategie",
      `Classifica dei conti ombra (10.000 $ ciascuno, sempre senza leva) · ${relative(doc.data?.generated_at)}`,
    ) +
    originBanner(doc, doc.data?.generated_at) +
    card("Classifica", table + note);
}

function sharpe(r: StrategyRow): number {
  const v = (r.performance ?? {})["sharpe"];
  return typeof v === "number" && Number.isFinite(v) ? v : -99;
}

function metric(v: unknown, digits: number): string {
  return typeof v === "number" && Number.isFinite(v) ? num(v, digits) : "n/d";
}

function metricPct(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v) ? percent(v, 1) : "n/d";
}

function signalChip(r: StrategyRow): string {
  const s = r.signal;
  if (!s || !s.direction) return '<span class="chip">nessuno</span>';
  const cls = s.direction === "long" ? "good" : s.direction === "short" ? "bad" : "";
  return `<span class="chip ${cls}">${escapeHtml(s.direction)}${s.prob != null ? ` ${percent(s.prob, 0)}` : ""}</span>`;
}

function detail(r: StrategyRow): string {
  const p = r.performance ?? {};
  const v = (r.validation ?? {}) as Record<string, unknown>;
  const byYear = (p["by_year"] as Record<string, number> | undefined) ?? {};
  return (
    `<p><a href="#/strategie">← Torna alla classifica</a></p>` +
    pageTitle(`${r.id} · ${r.name ?? ""}`, `${family(r.family)} · ${lifecycle(r.lifecycle)}`) +
    card(
      "Segnale corrente",
      r.signal
        ? `<p>${escapeHtml(r.signal.rationale ?? "—")}</p>
           <dl class="kv">
             <dt>Direzione</dt><dd>${escapeHtml(r.signal.direction ?? "n/d")}</dd>
             <dt>Probabilità</dt><dd>${percent(r.signal.prob, 0)}</dd>
             <dt>Orizzonte</dt><dd>${num(r.signal.horizon_days, 0)} giorni</dd>
             <dt>Emesso</dt><dd>${dateTime(r.signal.ts)}</dd>
           </dl>`
        : empty("Nessun segnale attivo."),
    ) +
    card(
      "Peso nel master",
      `<p>${escapeHtml(r.weight_explanation ?? "Nessuna spiegazione disponibile.")}</p>
       <dl class="kv"><dt>Peso</dt><dd>${percent(r.weight, 1)}</dd></dl>`,
    ) +
    card(
      "Performance del conto ombra",
      `<dl class="kv">
        <dt>Sharpe</dt><dd>${metric(p["sharpe"], 2)}</dd>
        <dt>Sortino</dt><dd>${metric(p["sortino"], 2)}</dd>
        <dt>Max drawdown</dt><dd>${metricPct(p["max_drawdown"])}</dd>
        <dt>Hit rate</dt><dd>${metricPct(p["hit_rate"])}</dd>
        <dt>Profit factor</dt><dd>${metric(p["profit_factor"], 2)}</dd>
        <dt>Operazioni</dt><dd>${metric(p["n_trades"], 0)}</dd>
        <dt>Rendimento totale</dt><dd>${metricPct(p["total_return"])}</dd>
      </dl>` +
        (Object.keys(byYear).length
          ? `<div class="table-wrap" style="margin-top:10px"><table class="data">
              <thead><tr>${Object.keys(byYear)
                .map((y) => `<th>${escapeHtml(y)}</th>`)
                .join("")}</tr></thead>
              <tbody><tr>${Object.values(byYear)
                .map((x) => `<td class="num">${percent(x, 1)}</td>`)
                .join("")}</tr></tbody></table></div>`
          : ""),
    ) +
    card(
      "Validazione",
      Object.keys(v).length
        ? `<dl class="kv">${Object.entries(v)
            .map(([k, val]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(val))}</dd>`)
            .join("")}</dl>`
        : `<div class="banner warn">Nessun report di validazione per questa strategia: il job settimanale
           calcola Deflated Sharpe, PBO, sensibilità ai costi e decide il ciclo di vita.</div>`,
    ) +
    card(
      "Tesi economica",
      `<p class="muted">La tesi completa, la controparte che perde, i regimi favorevoli e le condizioni di
       invalidazione sono documentate in <code>docs/STRATEGIES.md</code> nel repository.</p>`,
    )
  );
}
