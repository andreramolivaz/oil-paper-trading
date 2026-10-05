/** Previsioni. La pagina risponde a tre domande, in quest'ordine:
 *
 *   1. dove vede il prezzo il modello pubblicato, e con quanta incertezza (fan chart + quantili);
 *   2. come si confronta con i due benchmark obbligatori (random walk e curva dei futures);
 *   3. quanto vale storicamente questa previsione (track record, oggi vuoto per costruzione).
 *
 * Il fan chart è disegnato qui e non in charts/svg.ts perché ha un requisito che la versione condivisa non
 * soddisfa: a 1 giorno la mediana dista dal prezzo attuale meno di mezzo dollaro, cioè ~1px, e il marcatore
 * si fondeva con la tratteggiata. Le barre sono più larghe, la linea della mediana ha un alone in
 * --surface-1 sotto il tratto della serie, e la linea del prezzo attuale è disegnata PRIMA delle barre.
 */
import { load } from "../data";
import {
  EMPTY,
  contracts,
  dateTime,
  escapeHtml,
  num,
  percent,
  price,
  relative,
  signedPercent,
  stamp,
  tone,
  withUnit,
} from "../format";
import { card, chartBlock, empty, legend, originBanner, pageTitle, src, tile } from "../components/ui";
import type { ForecastRow, ForecastsDoc, MarketDoc } from "../types";

const HORIZON_LABELS: Record<string, string> = {
  h1d: "1 giorno",
  h1w: "1 settimana",
  h1m: "1 mese",
  h3m: "3 mesi",
};

const HORIZON_ORDER: Record<string, number> = { h1d: 1, h1w: 2, h1m: 3, h3m: 4 };

const MODEL_LABELS: Record<string, string> = {
  random_walk: "Random walk",
  futures_curve: "Curva futures",
  garch: "GARCH",
  har_rv: "HAR-RV",
  arima: "ARIMA",
  ets: "ETS",
  lgbm_quantile: "LightGBM quantile",
  stacking: "Stacking",
  ensemble: "Ensemble",
};

/** I due benchmark che il brief rende obbligatori: una previsione che non li batte non viene promossa. */
const BENCHMARKS = new Set(["random_walk", "futures_curve"]);

/** L'ordine in cui i modelli vanno letti: prima i benchmark, poi i modelli, infine l'ensemble pubblicato. */
const MODEL_ORDER = ["random_walk", "futures_curve", "garch", "har_rv", "arima", "ets", "lgbm_quantile", "stacking", "ensemble"];

/** `th` è in maiuscoletto: senza questo l'unità in testa alla colonna diventerebbe "$/BBL". */
const unit = (text: string) => `<span class="unit" style="text-transform:none">${escapeHtml(text)}</span>`;

const DOLLARS_BBL = unit("$/bbl");

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** La data in cui l'orizzonte scade. Il motore la pubblica, il tipo condiviso non la dichiara. */
function targetDate(row: ForecastRow): string | null {
  return (row as { target_date?: string | null }).target_date ?? null;
}

interface FanRow {
  label: string;
  target: string | null;
  median: number;
  q05: number;
  q25: number;
  q75: number;
  q95: number;
}

export async function renderForecasts(el: HTMLElement): Promise<void> {
  const [doc, market] = await Promise.all([load<ForecastsDoc>("forecasts.json"), load<MarketDoc>("market.json")]);
  const f = doc.data;
  const head =
    pageTitle("Previsioni", f ? `Asof ${dateTime(f.asof)} · generato ${relative(f.generated_at)}` : undefined) +
    originBanner(doc, f?.generated_at);

  const horizons = f?.horizons ?? {};
  const rows = Object.entries(horizons)
    .filter(([, r]) => r && finite(r.median) !== null)
    .sort((a, b) => (HORIZON_ORDER[a[0]] ?? 99) - (HORIZON_ORDER[b[0]] ?? 99));

  if (!f || !rows.length) {
    el.innerHTML =
      head +
      card(
        "Previsione pubblicata",
        empty(
          "Nessuna previsione pubblicata.",
          "Le previsioni vengono prodotte dal job di fine giornata (eod), dopo il settlement ICE.",
        ),
        src(f?.source, f?.asof),
      );
    return;
  }

  const priceNow = finite(f.price);
  const provenance = src(f.source, f.asof);

  // ------------------------------------------------------------------ sintesi
  const [leadKey, lead] = rows[0];
  const leadLabel = HORIZON_LABELS[leadKey] ?? leadKey;
  const leadMedian = finite(lead.median);
  const leadDelta = priceNow !== null && leadMedian !== null && priceNow !== 0 ? leadMedian / priceNow - 1 : null;
  const pUp = finite(lead.p_up);

  const summary = `<div class="tiles">
    ${tile("Prezzo attuale", withUnit(price(priceNow)), "front Brent, ultimo prezzo noto")}
    ${tile(
      `Mediana a ${leadLabel}`,
      withUnit(price(leadMedian)),
      leadDelta === null
        ? "nessun prezzo di riferimento"
        : `<span class="${tone(leadDelta)}">${signedPercent(leadDelta)}</span> sul prezzo attuale`,
    )}
    ${tile(
      `P(rialzo) a ${leadLabel}`,
      percent(pUp),
      "sopra il 50% la distribuzione pende al rialzo",
      tone(pUp === null ? null : pUp - 0.5),
    )}
    ${tile(`Vol attesa a ${leadLabel}`, percent(lead.expected_vol), "annualizzata, media di 3 modelli")}
  </div>`;

  // ------------------------------------------------------------------ fan chart
  const fanRows: FanRow[] = rows.map(([k, r]) => {
    const median = finite(r.median) as number;
    return {
      label: HORIZON_LABELS[k] ?? k,
      target: targetDate(r),
      median,
      q05: finite(r.q05) ?? median,
      q25: finite(r.q25) ?? median,
      q75: finite(r.q75) ?? median,
      q95: finite(r.q95) ?? median,
    };
  });

  const fanBody =
    priceNow === null
      ? chartBlock({
          id: "fan-chart",
          hasData: false,
          emptyMessage: "Il fan chart richiede il prezzo attuale.",
          emptyHint: "Senza un prezzo di riferimento le bande non sarebbero collocabili: i quantili restano in tabella.",
        })
      : forecastFan(fanRows, priceNow) +
        legend([
          { label: "Intervallo 5–95%", color: "color-mix(in srgb, var(--series-1) 16%, var(--surface-1))", block: true },
          { label: "Intervallo 25–75%", color: "color-mix(in srgb, var(--series-1) 34%, var(--surface-1))", block: true },
          { label: "Mediana", color: "var(--series-1)" },
          { label: "Prezzo attuale", color: "var(--text-muted)", dashed: true },
        ]);

  const curveNote = market.data?.curve?.points?.length
    ? `<p class="muted">La curva dei futures è la previsione del mercato ed è uno dei due benchmark obbligatori.
       Vedi la pagina <a href="#/mercato">Mercato</a> per la curva completa${
         market.data.curve.approx ? ' <span class="approx">(≈ curva approssimata)</span>' : ""
       }.</p>`
    : "";

  const fanCard = card(
    "Distribuzione per orizzonte",
    fanBody +
      `<p class="muted">Il 90% degli scenari cade nella banda chiara (5–95%), il 50% in quella scura (25–75%);
       la barra piena è la mediana, la tratteggiata il prezzo attuale. I quattro orizzonti condividono una sola
       scala dei prezzi: la banda si allarga perché l'incertezza cresce con il tempo, non per effetto grafico.</p>` +
      curveNote,
    provenance,
  );

  // ------------------------------------------------------------------ quantili
  const quantiles = `<div class="table-wrap"><table class="data">
    <thead><tr>
      <th>Orizzonte</th>
      <th class="num">Mediana ${DOLLARS_BBL}</th>
      <th class="num">5% ${DOLLARS_BBL}</th>
      <th class="num">25% ${DOLLARS_BBL}</th>
      <th class="num">75% ${DOLLARS_BBL}</th>
      <th class="num">95% ${DOLLARS_BBL}</th>
      <th class="num">P(rialzo)</th>
      <th class="num">Vol attesa ${unit("ann.")}</th>
      <th>Modello</th>
    </tr></thead>
    <tbody>${rows
      .map(
        ([k, r]) => `<tr>
          <td>${escapeHtml(HORIZON_LABELS[k] ?? k)}</td>
          <td class="num">${num(r.median)}</td>
          <td class="num">${num(r.q05)}</td>
          <td class="num">${num(r.q25)}</td>
          <td class="num">${num(r.q75)}</td>
          <td class="num">${num(r.q95)}</td>
          <td class="num">${percent(r.p_up)}</td>
          <td class="num">${percent(r.expected_vol)}</td>
          <td>${escapeHtml(MODEL_LABELS[r.model ?? ""] ?? r.model ?? EMPTY)}${
            r.approx ? ' <span class="approx">≈</span>' : ""
          }</td>
        </tr>`,
      )
      .join("")}</tbody></table></div>
    <p class="muted">I quantili sono quelli della distribuzione predittiva: sotto la colonna 5% cade uno scenario
    su venti, sotto la 95% diciannove su venti. La volatilità attesa è annualizzata.</p>`;

  const drivers = rows
    .filter(([, r]) => (r.drivers ?? []).length)
    .map(([k, r]) => {
      const t = targetDate(r);
      return `<details><summary>Driver a ${escapeHtml(HORIZON_LABELS[k] ?? k)}${
        t ? ` · scadenza ${escapeHtml(stamp(t))}` : ""
      }</summary>
        <ul class="muted" style="margin:8px 0 0;padding-left:18px">${(r.drivers ?? [])
          .map((d) => `<li>${escapeHtml(d)}</li>`)
          .join("")}</ul>
      </details>`;
    })
    .join("");

  const driversBlock = drivers
    ? `<div style="margin-top:12px">${drivers}</div>`
    : `<div style="margin-top:12px">${empty(
        "Nessun driver pubblicato per questi orizzonti.",
        "Il motore elenca i driver solo quando le feature che decidono la previsione sono tutte disponibili.",
      )}</div>`;

  // ------------------------------------------------------------------ OVX
  const ovx = f.ovx_implied_range ?? null;
  const low = finite(ovx?.low);
  const high = finite(ovx?.high);
  const halfWidth = low !== null && high !== null && priceNow !== null && priceNow !== 0 ? (high - low) / 2 / priceNow : null;
  const hasRange = low !== null || high !== null;
  const implied = hasRange
    ? `<dl class="kv">
        <dt>Minimo implicito</dt><dd>${price(low)}</dd>
        <dt>Massimo implicito</dt><dd>${price(high)}</dd>
        <dt>Ampiezza sul prezzo</dt><dd>${halfWidth === null ? EMPTY : `±${percent(halfWidth)}`}</dd>
        <dt>Orizzonte</dt><dd>${num(ovx?.days, 0)} giorni</dd>
      </dl>
      <p class="muted"><span class="approx">≈</span> L'OVX misura la volatilità implicita a 30 giorni delle opzioni
      su un ETF petrolifero, non sul Brent: il range è un'approssimazione dichiarata, non una previsione del motore.</p>`
    : empty(
        "Range implicito OVX non disponibile.",
        "Serve l'OVX del giorno: senza quel dato il range non viene calcolato, e non viene inventato.",
      );

  // ------------------------------------------------------------------ assemblaggio
  el.innerHTML =
    head +
    card("Previsione pubblicata", summary, provenance) +
    fanCard +
    card("Quantili, probabilità e driver", quantiles + driversBlock, provenance) +
    card("Range implicito nell'OVX", implied, src(ovx?.source, f.asof, hasRange && (ovx?.approx ?? true))) +
    card(
      "Confronto fra modelli",
      modelsTable(f, rows.map(([k]) => k), lead.model ?? null),
      provenance,
    ) +
    card("Track record contro random walk e curva", trackRecord(f), src(trackSource(f), trackAsof(f)));
}

// ---------------------------------------------------------------------------- fan chart

const FAN_W = 640;
const FAN_H = 272;
const FAN_PAD_L = 48;
const FAN_PAD_R = 52;
const FAN_PAD_TOP = 20;
const FAN_PAD_BOTTOM = 34;
/** Il tratto della mediana e il suo alone. L'alone è 3px più largo: è quello che la stacca dalla tratteggiata. */
const MEDIAN_W = 3;
const MEDIAN_HALO_W = MEDIAN_W + 3;

/** Fan chart: una colonna per orizzonte, bande di quantili intorno alla mediana, scala dei prezzi condivisa. */
function forecastFan(rows: FanRow[], priceNow: number): string {
  const plotH = FAN_H - FAN_PAD_TOP - FAN_PAD_BOTTOM;
  const values = [priceNow, ...rows.flatMap((r) => [r.q05, r.q95])];
  const min = Math.min(...values);
  const max = Math.max(...values);
  // Una serie costante non ha ampiezza: senza questo tutto collasserebbe su una riga.
  const headroom = max > min ? (max - min) * 0.1 : Math.max(1, Math.abs(max) * 0.02);
  const lo = min - headroom;
  const span = max - min + 2 * headroom;
  const sy = (v: number) => FAN_PAD_TOP + (1 - (v - lo) / span) * plotH;
  const step = (FAN_W - FAN_PAD_L - FAN_PAD_R) / rows.length;
  // Barre larghe: il vecchio tetto di 46px su un passo di 135px lasciava un marcatore troppo corto per
  // distinguersi dalla linea del prezzo attuale.
  const bw = Math.min(78, step * 0.72);
  const fmt = (v: number) => v.toFixed(1).replace(".", ",");
  const left = FAN_PAD_L - 8;
  const right = FAN_W - FAN_PAD_R + 8;

  // Scala dei prezzi come scaletta orizzontale: la convenzione del resto del sito, non una griglia.
  const ladder =
    max > min
      ? [max, (max + min) / 2, min]
          .map((v) => {
            const y = sy(v).toFixed(1);
            return `<line class="grid-line" x1="${left}" y1="${y}" x2="${right}" y2="${y}" />
              <text x="${right + 4}" y="${y}" dominant-baseline="middle">${fmt(v)}</text>`;
          })
          .join("")
      : "";

  // Il prezzo attuale è un riferimento, non una serie: in --text-muted e disegnato SOTTO le barre.
  const nowY = sy(priceNow);
  const nowLine = `<line x1="${left}" y1="${nowY.toFixed(1)}" x2="${right}" y2="${nowY.toFixed(1)}"
      stroke="var(--text-muted)" stroke-width="1" stroke-dasharray="5 4" />
    <text x="${left - 6}" y="${nowY.toFixed(1)}" dominant-baseline="middle" text-anchor="end">${fmt(priceNow)}</text>`;

  const body = rows
    .map((r, i) => {
      const cx = FAN_PAD_L + step * (i + 0.5);
      const x = (cx - bw / 2).toFixed(1);
      const w = bw.toFixed(1);
      const outer = `<rect x="${x}" y="${sy(r.q95).toFixed(1)}" width="${w}"
        height="${Math.max(1, sy(r.q05) - sy(r.q95)).toFixed(1)}" rx="3" fill="var(--series-1)" fill-opacity="0.16" />`;
      const inner = `<rect x="${x}" y="${sy(r.q75).toFixed(1)}" width="${w}"
        height="${Math.max(1, sy(r.q25) - sy(r.q75)).toFixed(1)}" rx="3" fill="var(--series-1)" fill-opacity="0.34" />`;
      const my = sy(r.median).toFixed(1);
      const mx1 = (cx - bw / 2 - 4).toFixed(1);
      const mx2 = (cx + bw / 2 + 4).toFixed(1);
      // Prima l'alone in --surface-1, poi il tratto della serie: a 1 giorno la mediana dista ~1px dalla
      // tratteggiata e senza alone le due linee erano una sola.
      const median = `<line x1="${mx1}" y1="${my}" x2="${mx2}" y2="${my}"
          stroke="var(--surface-1)" stroke-width="${MEDIAN_HALO_W}" stroke-linecap="round" />
        <line x1="${mx1}" y1="${my}" x2="${mx2}" y2="${my}"
          stroke="var(--series-1)" stroke-width="${MEDIAN_W}" stroke-linecap="round" />`;
      // L'etichetta va dalla parte OPPOSTA al prezzo attuale: a 1 giorno la mediana è a 1px dalla
      // tratteggiata e un'etichetta messa sempre in alto si leggeva come il valore della tratteggiata.
      const mNum = sy(r.median);
      const labelY = Math.min(
        FAN_PAD_TOP + plotH - 3,
        Math.max(FAN_PAD_TOP + 9, mNum + (mNum >= nowY ? 13 : -10)),
      );
      const value = `<text class="value" x="${cx.toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="middle"
        stroke="var(--surface-1)" stroke-width="3" stroke-linejoin="round" paint-order="stroke">${fmt(r.median)}</text>`;
      const label = `<text x="${cx.toFixed(1)}" y="${FAN_H - 18}" text-anchor="middle">${escapeHtml(r.label)}</text>`;
      const target = r.target
        ? `<text x="${cx.toFixed(1)}" y="${FAN_H - 5}" text-anchor="middle" opacity="0.75">${escapeHtml(stamp(r.target))}</text>`
        : "";
      return outer + inner + median + value + label + target;
    })
    .join("");

  const spoken = rows
    .map((r) => `${r.label}, mediana ${fmt(r.median)} dollari, 90% degli scenari fra ${fmt(r.q05)} e ${fmt(r.q95)}`)
    .join("; ");
  return `<svg viewBox="0 0 ${FAN_W} ${FAN_H}" class="svg-chart" role="img"
      aria-label="${escapeHtml(`Distribuzione prevista del Brent per orizzonte. Prezzo attuale ${fmt(priceNow)} dollari. ${spoken}.`)}">
    ${ladder}
    ${nowLine}
    ${body}
  </svg>`;
}

// ---------------------------------------------------------------------------- confronto fra modelli

/** I punti di ogni modello accanto all'ensemble pubblicato: è l'unico posto dove si vedono i benchmark. */
function modelsTable(f: ForecastsDoc, horizonKeys: string[], published: string | null): string {
  const models = f.models;
  if (!models || !Object.keys(models).length || !horizonKeys.length) {
    return empty(
      "Nessun confronto fra modelli disponibile.",
      "Il job eod pubblica i punti di ogni modello insieme alla previsione dell'ensemble.",
    );
  }
  const keys = Object.keys(models).sort((a, b) => rank(a) - rank(b));
  const header = horizonKeys
    .map((h) => `<th class="num">${escapeHtml(HORIZON_LABELS[h] ?? h)} ${DOLLARS_BBL}</th>`)
    .join("");
  const body = keys
    .map((k) => {
      const tags = [
        BENCHMARKS.has(k) ? '<span class="chip">benchmark</span>' : "",
        k === published ? '<span class="chip info">pubblicato</span>' : "",
      ]
        .filter(Boolean)
        .join(" ");
      const cells = horizonKeys.map((h) => `<td class="num">${num(models[k]?.[h])}</td>`).join("");
      return `<tr><td>${escapeHtml(MODEL_LABELS[k] ?? k)}${tags ? ` ${tags}` : ""}</td>${cells}</tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>Modello</th>${header}</tr></thead>
    <tbody>${body}</tbody></table></div>
    <p class="muted">Random walk e curva dei futures sono i due benchmark obbligatori del brief: una previsione che
    non li batte non viene promossa, e il verdetto compare nel track record qui sotto. L'ensemble è la previsione
    effettivamente pubblicata nelle tabelle sopra.</p>`;
}

function rank(key: string): number {
  const i = MODEL_ORDER.indexOf(key);
  return i < 0 ? 99 : i;
}

// ---------------------------------------------------------------------------- track record

interface TrackRecord {
  generated_at?: string | null;
  source?: string | null;
  n_forecasts?: number;
  n_resolved?: number;
  n_pending?: number;
  first_asof?: string | null;
  last_asof?: string | null;
  models?: Record<string, Record<string, Record<string, unknown>>> | null;
}

function track(f: ForecastsDoc): TrackRecord {
  return (f.track_record ?? {}) as TrackRecord;
}

function trackSource(f: ForecastsDoc): string | null {
  const tr = track(f);
  return tr.source ? `engine/forecast · ${tr.source}` : (f.source ?? null);
}

function trackAsof(f: ForecastsDoc): string | null {
  return track(f).generated_at ?? f.generated_at ?? null;
}

function trackRecord(f: ForecastsDoc): string {
  const tr = track(f);
  const counts = `<div class="tiles">
    ${tile("Previsioni archiviate", count(tr.n_forecasts))}
    ${tile("In attesa di scadenza", count(tr.n_pending))}
    ${tile("Risolte e valutate", count(tr.n_resolved))}
    ${tile(
      "Prima archiviata",
      tr.first_asof ? escapeHtml(stamp(tr.first_asof)) : "",
      tr.last_asof ? `ultima ${escapeHtml(stamp(tr.last_asof))}` : "",
      "",
      "nessuna previsione ancora archiviata",
    )}
  </div>`;
  const note = `<p class="muted">Theil U &lt; 1 significa errore minore del random walk; il test di Diebold-Mariano dice
    se la differenza è statisticamente significativa. Il CRPS valuta l'intera distribuzione, non solo il punto
    centrale. Un modello che non batte il random walk viene dichiarato tale, senza attenuanti.</p>`;

  if (!tr.models || !Object.keys(tr.models).length) {
    return (
      empty(
        "Nessun orizzonte è ancora scaduto: non esiste un track record da mostrare.",
        "Ogni previsione viene archiviata con il suo orario e valutata solo quando l'orizzonte è passato.",
      ) +
      `<div style="margin-top:12px">${counts}</div>` +
      note
    );
  }

  const body = Object.entries(tr.models)
    .flatMap(([model, horizons]) =>
      Object.entries(horizons).map(([h, m]) => {
        const beats = m["beats_rw"];
        const verdict = String(m["verdict"] ?? (beats === true ? "batte il random walk" : "non batte il random walk"));
        return `<tr>
          <td>${escapeHtml(MODEL_LABELS[model] ?? model)}</td>
          <td>${escapeHtml(HORIZON_LABELS[h] ?? h)}</td>
          <td class="num">${count(m["n"])}</td>
          <td class="num">${score(m["theil_u"])}</td>
          <td class="num">${score(m["dm_p_one_sided"])}</td>
          <td class="num">${rate(m["dir_hit_rate"])}</td>
          <td class="num">${score(m["crps"])}</td>
          <td><span class="chip ${beats === true ? "good" : "warn"}">${escapeHtml(verdict)}</span></td>
        </tr>`;
      }),
    )
    .join("");

  return `<div class="table-wrap tall"><table class="data">
    <thead><tr>
      <th>Modello</th><th>Orizzonte</th>
      <th class="num">n</th><th class="num">Theil U</th><th class="num">DM p</th>
      <th class="num">Direzione</th><th class="num">CRPS</th><th>Verdetto</th>
    </tr></thead>
    <tbody>${body}</tbody></table></div>
    <div style="margin-top:12px">${counts}</div>
    ${note}`;
}

/** Theil U, CRPS e la p di Diebold-Mariano non hanno un'unità: tre decimali, nessun formattatore dedicato. */
function score(value: unknown): string {
  return finite(value) === null ? EMPTY : num(value as number, 3);
}

function count(value: unknown): string {
  return finite(value) === null ? EMPTY : contracts(value as number);
}

function rate(value: unknown): string {
  return finite(value) === null ? EMPTY : percent(value as number);
}
