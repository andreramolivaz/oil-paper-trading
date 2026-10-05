/** Mercato e regimi: curva, spread Brent-WTI, scorte contro il range a 5 anni, COT, geopolitica, eventi.
 *
 * I grafici sono ordinati, non impilati tutti alla stessa altezza: la curva è il grafico primario (è la forma
 * che decide S4-S7), lo spread Brent-WTI resta nella taglia normale, COT e GPR scendono nella taglia "short"
 * con la loro ultima lettura grande nell'intestazione della card.
 *
 * Lo storico dei regimi contiene oggi UNA sola rilevazione (due righe identiche): disegnarla come uno stack
 * produceva una lastra piena con la stessa data ai due estremi, cioè un grafico che mentiva. Sotto cinque
 * rilevazioni distinte la pagina mostra il regime corrente come misuratore di confidenza con la soglia del
 * gate segnata, che è lo stato vuoto onesto.
 */
import { addLine, attachReadout, baseChart, cssVar, fit, spansMultipleDays, toLineData } from "../charts/base";
import { bandChart, curveChart, regimeStack } from "../charts/svg";
import { load } from "../data";
import { contracts, dateTime, escapeHtml, num, percent, price, relative, stamp, tone, withUnit } from "../format";
import { card, chartBlock, empty, legend, originBanner, pageTitle, src } from "../components/ui";
import type { MarketDoc, Num } from "../types";

/** config/risk.yaml → gate.min_regime_confidence: sotto questa confidenza il gate alpha non passa. */
const GATE_REGIME_CONFIDENCE = 0.7;
/** Sotto questo numero di rilevazioni distinte uno stack temporale non racconta nulla. */
const MIN_REGIME_POINTS = 5;

type RegimeRow = { ts?: string | null; label?: string | null; confidence?: Num };

export async function renderMarket(el: HTMLElement): Promise<void> {
  const doc = await load<MarketDoc>("market.json");
  const m = doc.data;
  if (!m) {
    el.innerHTML =
      pageTitle("Mercato e regimi") +
      `<div class="banner bad">⚠ Dati di mercato non disponibili${doc.error ? ` (${escapeHtml(doc.error)})` : ""}.</div>`;
    return;
  }

  // ---------------------------------------------------------------- curva (grafico primario)
  const curve = m.curve;
  const points = curve?.points ?? [];
  const front = points[0];
  const curveBlock =
    points.length > 1
      ? curveChart(points, { approx: curve?.approx, height: 300 }) +
        `<p class="muted">Front ${escapeHtml(curve?.m1_code ?? "n/d")} · asse orizzontale equispaziato per indice,
          non per rango: i ranghi reali, non sempre consecutivi, sono nella tabella qui sotto.${
            curve?.note ? `<br />${escapeHtml(curve.note)}` : ""
          }</p>` +
        curveTable(points)
      : empty(
          "Curva non disponibile.",
          "Servono almeno due scadenze quotate nello stesso snapshot per disegnare la forma della curva.",
        );

  // ---------------------------------------------------------------- regimi
  const regimeRows = collapseRegime(m.regime_history ?? []);
  const latestRegime = regimeRows[regimeRows.length - 1];
  const regimeHasHistory = regimeRows.length >= MIN_REGIME_POINTS;
  const regimeBlock = !latestRegime
    ? empty(
        "Storico dei regimi non ancora disponibile.",
        "Il motore scrive un'etichetta di regime a ogni chiusura: la serie compare dal primo eod.",
      )
    : regimeHasHistory
      ? regimeStack(regimeRows) +
        `<p class="muted">Altezza della barra = confidenza del regime; colore = etichetta. Quando nessun regime
          supera la soglia il motore dichiara «Transizione» e riduce il rischio.</p>` +
        `<div style="margin-top:8px">${src("engine/regime", latestRegime.ts)}</div>`
      : regimeMeter(latestRegime, regimeRows.length);

  // ---------------------------------------------------------------- spread Brent-WTI
  const spreadRows = m.spreads?.["brent_wti"] ?? [];
  const spreadData = toLineData(spreadRows);
  const spreadLast = spreadRows[spreadRows.length - 1];
  const spreadBlock = chartBlock({
    id: "spread-chart",
    hasData: spreadData.length > 1,
    emptyMessage: "Spread Brent − WTI non disponibile.",
    emptyHint: "Serve il fronte di entrambi i contratti nello stesso giorno per calcolare la differenza.",
    legendItems: [{ label: "Brent − WTI ($/bbl)", color: cssVar("--series-2") }],
    source: "Yahoo Finance (fronti Brent e WTI)",
    asof: spreadLast?.t,
  });

  // ---------------------------------------------------------------- scorte contro il range a 5 anni
  const inv = m.inventories;
  const invBand = inv?.band ?? [];
  const invCurrent = inv?.current ?? [];
  const invLast = invCurrent[invCurrent.length - 1];
  const invBlock =
    invBand.length && invCurrent.length
      ? bandChart(invBand, invCurrent) +
        legend([
          { label: "Anno in corso", color: cssVar("--series-2") },
          { label: `Media ${inv?.band_years ?? ""}`, color: cssVar("--text-muted"), dashed: true },
          { label: `Minimo-massimo ${inv?.band_years ?? ""}`, color: cssVar("--text-muted"), block: true },
        ]) +
        `<p class="muted">Banda grigia: minimo-massimo delle stesse settimane negli anni
          ${escapeHtml(inv?.band_years ?? "")}; tratteggiata: media. Linea arancione: anno in corso.
          Unità: ${escapeHtml(inv?.unit ?? "")}.</p>` +
        `<div style="margin-top:8px">${src(inv?.source, invLast?.t)}</div>`
      : chartBlock({
          id: "inv-chart",
          hasData: false,
          emptyMessage: `Banda stagionale ${inv?.band_years ?? "a 5 anni"} non ancora ricostruita.`,
          emptyHint: invLast
            ? `C'è una sola settimana di storico (sett. ${invLast.week}). Serve la serie settimanale EIA completa (chiave EIA_API_KEY) per il confronto con il range.`
            : "Serve lo storico settimanale EIA (chiave EIA_API_KEY) per scorte e range stagionale.",
          source: inv?.source,
          // L'asof esiste: è la data dell'ultimo punto pubblicato, non "undefined".
          asof: invLast?.t,
        }) +
        (invLast
          ? `<p class="muted" style="margin-top:10px">Ultimo dato pubblicato: ${num(invLast.v, 0)}
              ${escapeHtml(inv?.unit ?? "")} nella settimana ${invLast.week}.</p>`
          : "");

  // ---------------------------------------------------------------- COT (taglia short)
  const cotRows = (m.cot?.series ?? [])
    .filter((r) => r.t && r.mm_net != null)
    .map((r) => ({ t: String(r.t), v: r.mm_net }));
  const cotLast = cotRows[cotRows.length - 1];
  const cotPrev = cotRows[cotRows.length - 2];
  const cotData = toLineData(cotRows);
  const cotOi = (m.cot?.series ?? []).filter((r) => r.oi != null).pop();
  const cotChange = cotLast?.v != null && cotPrev?.v != null ? cotLast.v - cotPrev.v : null;
  const cotBlock =
    chartBlock({
      id: "cot-chart",
      hasData: cotData.length > 1,
      emptyMessage: "COT non disponibile.",
      emptyHint: "Il report settimanale ICE arriva il venerdì: la serie compare dal primo download riuscito.",
      legendItems: [{ label: "Managed money net (contratti)", color: cssVar("--series-3") }],
      source: m.cot?.source,
      asof: cotLast?.t,
      size: "short",
    }) +
    (cotLast
      ? `<p class="muted" style="margin-top:10px">Variazione sulla settimana precedente
          <span class="num ${tone(cotChange)}">${signedNum(cotChange, 0)}</span> contratti${
            cotOi?.oi != null ? ` · open interest ${contracts(cotOi.oi)} contratti` : ""
          }. Net positivo = managed money netta lunga.</p>`
      : "");

  // ---------------------------------------------------------------- premio geopolitico (taglia short)
  const geoRows = m.geopolitics?.series ?? [];
  const geoData = toLineData(geoRows);
  const geoLast = geoRows[geoRows.length - 1];
  const geoPrev = geoRows[geoRows.length - 2];
  const geoBlock =
    chartBlock({
      id: "geo-chart",
      hasData: geoData.length > 1,
      emptyMessage: "Indice geopolitico non disponibile.",
      emptyHint: "La serie GPR viene ricalcolata a ogni fetch: compare appena il file della fonte risponde.",
      legendItems: [{ label: "Indice geopolitico (GPR)", color: cssVar("--series-5") }],
      source: m.geopolitics?.source,
      asof: geoLast?.t,
      size: "short",
    }) +
    (geoLast && geoPrev
      ? `<p class="muted" style="margin-top:10px">Rispetto al giorno precedente
          <span class="num">${signedNum(geoLast.v - geoPrev.v, 0)}</span> punti indice. Un indice più alto alza il
          premio di rischio, non la direzione: il motore lo usa come filtro, non come segnale.</p>`
      : "");

  // ---------------------------------------------------------------- calendario eventi
  const events = m.events ?? [];
  const eventsBlock = events.length
    ? `<div class="table-wrap tall"><table class="data">
        <thead><tr>
          <th class="wrap-text">Evento</th>
          <th>Quando</th>
          <th class="num">Fra <span class="unit">h</span></th>
          <th>Tipo</th>
        </tr></thead>
        <tbody>${events
          .map(
            (e) => `<tr>
              <td class="wrap-text">${escapeHtml(e.name)}${
                e.confirmed ? "" : ' <span class="chip warn">data da confermare</span>'
              }</td>
              <td>${dateTime(e.ts)}</td>
              <td class="num">${num(e.hours_away, 0)}</td>
              <td>${e.binary ? '<span class="chip info">binario</span>' : "informativo"}</td>
            </tr>`,
          )
          .join("")}</tbody></table></div>
        <p class="muted">Gli eventi binari dimezzano la leva (componente L_evento, moltiplicatore 0,5) nelle
          24 ore precedenti. Orari in ora italiana.</p>`
    : empty(
        "Nessun evento in calendario.",
        "Il calendario copre i 45 giorni successivi: EIA, Baker Hughes, COT, OPEC, IEA, FOMC e scadenze ICE.",
      );

  el.innerHTML =
    pageTitle("Mercato e regimi", `Aggiornato ${relative(m.generated_at)}`) +
    originBanner(doc, m.generated_at) +
    card(
      "Curva dei futures",
      curveBlock,
      front
        ? headerFigure(withUnit(price(front.price)), `M${front.rank}`, src(curve?.source, curve?.asof, curve?.approx))
        : src(curve?.source, curve?.asof, curve?.approx),
    ) +
    card(regimeHasHistory ? "Probabilità di regime nel tempo" : "Regime corrente e confidenza", regimeBlock) +
    card(
      "Spread Brent − WTI",
      spreadBlock,
      spreadLast
        ? headerFigure(withUnit(price(spreadLast.v)), "ultimo", "")
        : "",
    ) +
    card(
      "Scorte di greggio contro il range a 5 anni",
      invBlock,
      invLast
        ? headerFigure(
            `${num(invLast.v, 0)}<span class="unit">${escapeHtml(inv?.unit ?? "")}</span>`,
            `sett. ${invLast.week}`,
            "",
          )
        : "",
    ) +
    card(
      "Posizionamento (COT managed money)",
      cotBlock,
      cotLast?.v != null
        ? headerFigure(
            `<span class="${tone(cotLast.v)}">${contracts(cotLast.v)}</span><span class="unit">contratti</span>`,
            "net",
            "",
          )
        : "",
    ) +
    card(
      "Premio geopolitico",
      geoBlock,
      geoLast ? headerFigure(`${num(geoLast.v, 0)}<span class="unit">GPR</span>`, "ultimo", "") : "",
    ) +
    card(
      "Calendario eventi",
      eventsBlock,
      `<span class="muted">${events.length} eventi · 45 giorni</span>
       ${src("config/events.yaml + calendario ICE Brent", m.generated_at)}`,
    );

  // I grafici si montano dopo l'innerHTML: chartBlock ha già deciso se il contenitore esiste.
  mountSeries("spread-chart", spreadRows, {
    color: cssVar("--series-2"),
    label: "Brent − WTI",
    precision: 2,
    fmt: (v) => price(v),
  });
  mountSeries("cot-chart", cotRows, {
    color: cssVar("--series-3"),
    label: "MM net",
    // Un conteggio di contratti con due decimali si legge "274047,00": la precisione è 0.
    precision: 0,
    fmt: (v) => contracts(v),
  });
  mountSeries("geo-chart", geoRows, {
    color: cssVar("--series-5"),
    label: "GPR",
    // Un livello di indice non ha centesimi.
    precision: 0,
    fmt: (v) => num(v, 0),
  });
}

/** La figura di testata di una card: il numero di cui parla la card, più la sua provenienza. */
function headerFigure(value: string, label: string, provenance: string): string {
  return `<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap">
    <span class="value" style="font-size:var(--t-xl);font-weight:550;letter-spacing:-0.015em">${value}</span>
    <span class="sub" style="font-size:var(--t-xs)">${escapeHtml(label)}</span>
    ${provenance}
  </div>`;
}

/** Una differenza senza unità, per le celle e le righe di nota: il segno c'è, il simbolo no.
 *  I decimali li decide la grandezza, come per le altre figure: 0 per conteggi e livelli di indice. */
function signedNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return num(value, digits);
  return `${value > 0 ? "+" : ""}${num(value, digits)}`;
}

/**
 * Monta una serie temporale: grafico, readout e fit. La serie non disegna più il proprio badge dell'ultimo
 * valore, lo porta la striscia di readout.
 */
function mountSeries(
  id: string,
  points: { t: string; v: number | null }[],
  opts: { color: string; label: string; precision: number; fmt: (v: number) => string },
): void {
  const host = document.getElementById(id);
  if (!host) return; // chartBlock ha già reso lo stato vuoto
  const data = toLineData(points);
  if (data.length < 2) return;
  const chart = baseChart(host, {
    timeVisible: !spansMultipleDays(points),
    priceFormatter: (v: number) => num(v, opts.precision),
  });
  const series = addLine(chart, data, opts.color, { precision: opts.precision });
  const strip = document.getElementById(`${id}-readout`);
  if (strip) attachReadout(chart, strip, [{ api: series, label: opts.label, color: opts.color, fmt: opts.fmt }]);
  fit(chart);
}

/**
 * Lo storico dei regimi arriva con righe ripetute: il motore riscrive la stessa rilevazione a ogni tick.
 * Una chiave per timestamp (vince l'ultima osservazione) elimina la lastra piena e fa emergere quante
 * rilevazioni DISTINTE esistono davvero.
 */
function collapseRegime(history: RegimeRow[]): RegimeRow[] {
  const out: RegimeRow[] = [];
  const index = new Map<string, number>();
  for (const row of history) {
    if (!row.ts || !row.label) continue;
    const at = index.get(row.ts);
    if (at !== undefined) {
      out[at] = row;
      continue;
    }
    index.set(row.ts, out.length);
    out.push(row);
  }
  return out;
}

/**
 * Lo stato vuoto onesto dello storico dei regimi: il regime corrente come misuratore di confidenza, con la
 * soglia del gate segnata. Due righe identiche non sono una storia da disegnare.
 */
function regimeMeter(row: RegimeRow, distinct: number): string {
  const conf = row.confidence ?? null;
  const value = conf === null || !Number.isFinite(conf) ? 0 : Math.max(0, Math.min(1, conf));
  const passes = conf !== null && conf >= GATE_REGIME_CONFIDENCE;
  const threshold = `${(GATE_REGIME_CONFIDENCE * 100).toFixed(0)}%`;
  return `<div class="value" style="font-size:var(--t-xl);font-weight:550;line-height:1.1;margin-top:4px">
      ${escapeHtml(row.label ?? "n/d")}
      <span class="unit" style="font-size:0.7em">confidenza ${percent(conf, 0)}</span>
    </div>
    <div role="img" aria-label="Confidenza del regime ${percent(conf, 0)} contro la soglia del ${threshold}"
      style="position:relative;height:14px;margin:14px 0 4px;background:var(--surface-3);border:1px solid var(--border);border-radius:var(--radius-sm)">
      <div style="position:absolute;left:0;top:0;bottom:0;width:${(value * 100).toFixed(1)}%;background:var(--series-1);border-radius:var(--radius-sm) 0 0 var(--radius-sm)"></div>
      <div style="position:absolute;left:${threshold};top:-5px;bottom:-5px;width:2px;background:var(--text-primary)"></div>
    </div>
    <div style="position:relative;height:16px;font-family:var(--mono);font-size:var(--t-micro);color:var(--text-muted)">
      <span style="position:absolute;left:0">0%</span>
      <span style="position:absolute;left:${threshold};transform:translateX(-50%);white-space:nowrap;color:var(--text-secondary)">soglia ${threshold}</span>
      <span style="position:absolute;right:0">100%</span>
    </div>
    <div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      ${
        passes
          ? `<span class="chip good">sopra la soglia del gate</span>`
          : `<span class="chip warn">sotto la soglia del gate · leva 1x</span>`
      }
      ${src("engine/regime", row.ts)}
    </div>
    <p class="muted">Sotto il ${threshold} di confidenza (config/risk.yaml, gate.min_regime_confidence) la
      condizione «qualità del regime» del gate alpha non passa e la leva resta a 1x. Quando nessun regime
      supera la soglia del modello il motore dichiara «Transizione» e riduce il rischio.</p>
    <p class="muted">Lo storico contiene ${distinct === 1 ? "una sola rilevazione" : `${num(distinct, 0)} rilevazioni distinte`}
      (${stamp(row.ts)}): il grafico nel tempo compare da ${MIN_REGIME_POINTS} rilevazioni distinte.</p>`;
}

/** La curva per scadenza: prezzo e distanza dal fronte, che è la grandezza che decide carry e roll. */
function curveTable(points: { rank: number; price: number }[]): string {
  const front = points[0];
  const rows = points
    .map(
      (p) => `<tr>
        <td>M${p.rank}</td>
        <td class="num">${num(p.price)}</td>
        <td class="num">${p.rank === front.rank ? "—" : signedNum(p.price - front.price)}</td>
      </tr>`,
    )
    .join("");
  return `<div class="table-wrap tall" style="margin-top:10px"><table class="data">
    <thead><tr>
      <th>Scadenza</th>
      <th class="num">Prezzo <span class="unit">$/bbl</span></th>
      <th class="num">Contro M${front.rank} <span class="unit">$/bbl</span></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}
