/** Mercato e regimi: curva, spread, Brent-WTI, crack, scorte contro il range a 5 anni, COT, geopolitica, eventi. */
import { addLine, baseChart, cssVar, fit, toLineData } from "../charts/base";
import { bandChart, curveChart, regimeStack } from "../charts/svg";
import { load } from "../data";
import { dateTime, escapeHtml, num, relative, usd } from "../format";
import { card, empty, legend, originBanner, pageTitle, src } from "../components/ui";
import type { MarketDoc } from "../types";

export async function renderMarket(el: HTMLElement): Promise<void> {
  const doc = await load<MarketDoc>("market.json");
  const m = doc.data;
  if (!m) {
    el.innerHTML =
      pageTitle("Mercato e regimi") +
      `<div class="banner bad">⚠ Dati di mercato non disponibili${doc.error ? ` (${escapeHtml(doc.error)})` : ""}.</div>`;
    return;
  }

  const curve = m.curve;
  const curveBlock = curve?.points?.length
    ? curveChart(curve.points, { approx: curve.approx }) +
      `<p class="muted">Front ${escapeHtml(curve.m1_code ?? "n/d")} · ${src(curve.source, curve.asof, curve.approx)}
       ${curve.note ? `<br />${escapeHtml(curve.note)}` : ""}</p>` +
      curveTable(curve.points)
    : empty("Curva non disponibile.");

  const regimeBlock = m.regime_history?.length
    ? regimeStack(m.regime_history) +
      `<p class="muted">Altezza della barra = confidenza del regime; colore = etichetta. Quando nessun regime supera
       la soglia il motore dichiara «Transizione» e riduce il rischio.</p>`
    : empty("Storico dei regimi non ancora disponibile.");

  const inv = m.inventories;
  const invBlock = inv?.band?.length
    ? bandChart(inv.band, inv.current ?? []) +
      `<p class="muted">Banda grigia: minimo-massimo delle stesse settimane negli anni ${escapeHtml(inv.band_years ?? "")};
       tratteggiata: media. Linea arancione: anno in corso. Unità: ${escapeHtml(inv.unit ?? "")}.
       ${src(inv.source, undefined)}</p>`
    : empty("Scorte EIA non disponibili (serve la chiave EIA_API_KEY per lo storico completo).");

  const cotRows = (m.cot?.series ?? []).filter((r) => r.t && r.mm_net != null);
  const cotBlock = cotRows.length
    ? `<div id="cot-chart" class="chart short"></div>${legend([
        { label: "Managed money net (contratti)", color: cssVar("--series-3") },
      ])}<p class="muted">${src(m.cot?.source, cotRows[cotRows.length - 1]?.t ?? undefined)}</p>`
    : empty("COT non disponibile.");

  const geoRows = m.geopolitics?.series ?? [];
  const geoBlock = geoRows.length
    ? `<div id="geo-chart" class="chart short"></div>${legend([
        { label: "Indice geopolitico (GPR)", color: cssVar("--series-5") },
      ])}<p class="muted">${src(m.geopolitics?.source, geoRows[geoRows.length - 1]?.t)}</p>`
    : empty("Indice geopolitico non disponibile.");

  const spreadRows = m.spreads?.["brent_wti"] ?? [];
  const spreadBlock = spreadRows.length
    ? `<div id="spread-chart" class="chart short"></div>${legend([
        { label: "Brent − WTI ($/bbl)", color: cssVar("--series-2") },
      ])}`
    : empty("Spread Brent-WTI non disponibile.");

  const events = (m.events ?? []).slice(0, 12);
  const eventsBlock = events.length
    ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>Evento</th><th>Quando</th><th>Fra</th><th>Tipo</th></tr></thead>
        <tbody>${events
          .map(
            (e) => `<tr>
              <td>${escapeHtml(e.name)}${e.confirmed ? "" : ' <span class="chip warn">data da confermare</span>'}</td>
              <td>${dateTime(e.ts)}</td>
              <td class="num">${num(e.hours_away, 0)} h</td>
              <td>${e.binary ? '<span class="chip info">binario</span>' : "informativo"}</td>
            </tr>`,
          )
          .join("")}</tbody></table></div>
        <p class="muted">Gli eventi binari tagliano la leva (componente L_evento) nelle ore precedenti.</p>`
    : empty("Nessun evento in calendario.");

  el.innerHTML =
    pageTitle("Mercato e regimi", `Aggiornato ${relative(m.generated_at)}`) +
    originBanner(doc, m.generated_at) +
    card("Curva dei futures", curveBlock) +
    card("Probabilità di regime nel tempo", regimeBlock) +
    card("Scorte di greggio contro il range a 5 anni", invBlock) +
    card("Spread Brent − WTI", spreadBlock) +
    card("Posizionamento (COT managed money)", cotBlock) +
    card("Premio geopolitico", geoBlock) +
    card("Calendario eventi", eventsBlock);

  mountLine("spread-chart", spreadRows, cssVar("--series-2"));
  mountLine(
    "cot-chart",
    cotRows.map((r) => ({ t: String(r.t), v: r.mm_net as number })),
    cssVar("--series-3"),
  );
  mountLine("geo-chart", geoRows, cssVar("--series-5"));
}

function mountLine(id: string, points: { t: string; v: number | null }[], color: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  const data = toLineData(points);
  if (data.length < 2) {
    el.outerHTML = empty("Serie troppo corta per il grafico.");
    return;
  }
  const chart = baseChart(el);
  addLine(chart, data, color);
  fit(chart);
}

function curveTable(points: { rank: number; price: number }[]): string {
  const head = points.slice(0, 12);
  return `<div class="table-wrap" style="margin-top:8px"><table class="data">
    <thead><tr>${head.map((p) => `<th>M${p.rank}</th>`).join("")}</tr></thead>
    <tbody><tr>${head.map((p) => `<td class="num">${usd(p.price)}</td>`).join("")}</tr></tbody>
  </table></div>`;
}
