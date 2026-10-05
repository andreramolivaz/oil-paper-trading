/** Previsioni: fan chart per orizzonte, probabilità di rialzo, driver, range implicito OVX, track record. */
import { load } from "../data";
import { fanChart } from "../charts/svg";
import { dateTime, escapeHtml, num, percent, relative, usd } from "../format";
import { card, empty, originBanner, pageTitle, src } from "../components/ui";
import type { ForecastsDoc, MarketDoc } from "../types";

const HORIZON_LABELS: Record<string, string> = {
  h1d: "1 giorno",
  h1w: "1 settimana",
  h1m: "1 mese",
  h3m: "3 mesi",
};

export async function renderForecasts(el: HTMLElement): Promise<void> {
  const [doc, market] = await Promise.all([load<ForecastsDoc>("forecasts.json"), load<MarketDoc>("market.json")]);
  const f = doc.data;
  if (!f || !f.horizons || !Object.keys(f.horizons).length) {
    el.innerHTML =
      pageTitle("Previsioni") +
      originBanner(doc, f?.generated_at) +
      `<div class="banner warn">Nessuna previsione pubblicata. Le previsioni vengono prodotte dal job di fine giornata
       (<code>eod</code>) dopo il settlement ICE.</div>`;
    return;
  }
  const price = f.price ?? null;
  const rows = Object.entries(f.horizons)
    .filter(([, r]) => r && r.median != null)
    .sort((a, b) => order(a[0]) - order(b[0]));

  const fan = price
    ? fanChart(
        rows.map(([k, r]) => ({
          label: HORIZON_LABELS[k] ?? k,
          q05: r.q05 ?? r.median!,
          q25: r.q25 ?? r.median!,
          median: r.median!,
          q75: r.q75 ?? r.median!,
          q95: r.q95 ?? r.median!,
        })),
        price,
      )
    : "";

  const table = `<div class="table-wrap"><table class="data">
    <thead><tr><th>Orizzonte</th><th>Mediana</th><th>5%</th><th>25%</th><th>75%</th><th>95%</th><th>P(rialzo)</th><th>Vol attesa</th><th>Modello</th></tr></thead>
    <tbody>${rows
      .map(
        ([k, r]) => `<tr>
          <td>${escapeHtml(HORIZON_LABELS[k] ?? k)}</td>
          <td class="num">${usd(r.median)}</td>
          <td class="num">${usd(r.q05)}</td>
          <td class="num">${usd(r.q25)}</td>
          <td class="num">${usd(r.q75)}</td>
          <td class="num">${usd(r.q95)}</td>
          <td class="num">${percent(r.p_up, 0)}</td>
          <td class="num">${percent(r.expected_vol, 0)}</td>
          <td>${escapeHtml(r.model ?? "n/d")}${r.approx ? ' <span class="approx">≈</span>' : ""}</td>
        </tr>`,
      )
      .join("")}</tbody></table></div>`;

  const drivers = rows
    .filter(([, r]) => (r.drivers ?? []).length)
    .map(
      ([k, r]) =>
        `<details><summary>Driver a ${escapeHtml(HORIZON_LABELS[k] ?? k)}</summary><ul class="muted">${(r.drivers ?? [])
          .map((d) => `<li>${escapeHtml(d)}</li>`)
          .join("")}</ul></details>`,
    )
    .join("");

  const implied = f.ovx_implied_range
    ? `<dl class="kv">
        <dt>Minimo implicito</dt><dd>${usd(f.ovx_implied_range.low)}</dd>
        <dt>Massimo implicito</dt><dd>${usd(f.ovx_implied_range.high)}</dd>
        <dt>Orizzonte</dt><dd>${num(f.ovx_implied_range.days, 0)} giorni</dd>
      </dl>
      <p class="muted"><span class="approx">≈</span> L'OVX misura la volatilità implicita a 30 giorni delle opzioni
      su un ETF petrolifero, non sul Brent: il range è un'approssimazione dichiarata.
      ${src(f.ovx_implied_range.source, f.asof, true)}</p>`
    : empty("Range implicito OVX non disponibile.");

  const curve = market.data?.curve?.points?.length
    ? `<p class="muted">La curva dei futures è la previsione del mercato ed è uno dei due benchmark obbligatori.
       Vedi la pagina <a href="#/mercato">Mercato</a> per la curva completa
       ${market.data.curve.approx ? '<span class="approx">(≈ curva approssimata)</span>' : ""}.</p>`
    : "";

  el.innerHTML =
    pageTitle("Previsioni", `Asof ${dateTime(f.asof)} · generato ${relative(f.generated_at)}`) +
    originBanner(doc, f.generated_at) +
    card(
      "Fan chart per orizzonte",
      (fan || empty("Serve il prezzo corrente per disegnare il fan chart.")) +
        `<p class="muted">Bande: 5–95% (chiara) e 25–75% (scura); la linea è la mediana. La tratteggiata è il prezzo attuale.</p>` +
        curve,
    ) +
    card("Quantili e probabilità", table + drivers) +
    card("Range implicito nell'OVX", implied) +
    card("Track record contro random walk e curva", trackRecord(f));
}

function order(key: string): number {
  return { h1d: 1, h1w: 2, h1m: 3, h3m: 4 }[key] ?? 99;
}

function trackRecord(f: ForecastsDoc): string {
  const tr = f.track_record as
    | { n_forecasts?: number; n_resolved?: number; models?: Record<string, Record<string, Record<string, unknown>>> }
    | null
    | undefined;
  if (!tr || !tr.models || !Object.keys(tr.models).length) {
    return `<div class="banner warn">Track record live non ancora disponibile: ogni previsione viene archiviata con
      il suo orario e valutata solo quando l'orizzonte è scaduto. Previsioni archiviate: ${num(tr?.n_forecasts ?? 0, 0)},
      risolte: ${num(tr?.n_resolved ?? 0, 0)}.</div>`;
  }
  const rows: string[] = [];
  for (const [model, horizons] of Object.entries(tr.models)) {
    for (const [h, m] of Object.entries(horizons)) {
      const beats = m["beats_rw"];
      const verdict = String(m["verdict"] ?? (beats === true ? "batte il random walk" : "non batte il random walk"));
      rows.push(`<tr>
        <td>${escapeHtml(model)}</td>
        <td>${escapeHtml(HORIZON_LABELS[h] ?? h)}</td>
        <td class="num">${fmt(m["n"], 0)}</td>
        <td class="num">${fmt(m["theil_u"], 3)}</td>
        <td class="num">${fmt(m["dm_p_one_sided"], 3)}</td>
        <td class="num">${pctOf(m["dir_hit_rate"])}</td>
        <td class="num">${fmt(m["crps"], 3)}</td>
        <td><span class="chip ${beats === true ? "good" : "warn"}">${escapeHtml(verdict)}</span></td>
      </tr>`);
    }
  }
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>Modello</th><th>Orizzonte</th><th>n</th><th>Theil U</th><th>DM p</th><th>Direzione</th><th>CRPS</th><th>Verdetto</th></tr></thead>
    <tbody>${rows.join("")}</tbody></table></div>
    <p class="muted">Theil U &lt; 1 significa errore minore del random walk; il test di Diebold-Mariano dice se la
    differenza è statisticamente significativa. Un modello che non batte il random walk viene dichiarato tale.</p>`;
}

function fmt(v: unknown, digits: number): string {
  return typeof v === "number" ? num(v, digits) : "n/d";
}

function pctOf(v: unknown): string {
  return typeof v === "number" ? percent(v, 0) : "n/d";
}
