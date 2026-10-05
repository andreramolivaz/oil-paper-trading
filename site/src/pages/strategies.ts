/** Strategie: i conti ombra di oggi, il backtest che decide i pesi, e per ciascuna il perché del suo peso.
 *
 * La pagina tiene separate due cose che la versione precedente mescolava in un'unica tabella da 11 colonne:
 *
 *   - il CONTO OMBRA esiste da oggi. Ha una sola rilevazione distinta per strategia e l'equity è ancora a
 *     10.000 $, quindi rendimento, Sharpe, Sortino, hit rate e profit factor sono tutti zero: non ordinano
 *     niente e non si colorano, perché tone() lascia neutro lo zero (un P&L fermo non è un guadagno).
 *   - il BACKTEST walk-forward è quello che decide la promozione, e i suoi numeri sono veri e diversi da
 *     strategia a strategia. È l'unica grandezza che oggi si muove, quindi è lui a prendersi il grafico a
 *     barre a base zero; le sparkline dei conti ombra sono, onestamente, una linea tratteggiata.
 *
 * Le soglie di promozione sono quelle di engine/validation/lifecycle.py, dove un valore MANCANTE non passa.
 * Una strategia che nel backtest non ha chiuso nessuna operazione non è stata bocciata: non è stata
 * testata. La pagina conta quante sono e lo dice, invece di nasconderlo dietro un peso zero.
 */
import { addArea, attachReadout, baseChart, cssVar, fit, spansMultipleDays, toLineData } from "../charts/base";
import { sparkline } from "../charts/svg";
import { load } from "../data";
import {
  EMPTY,
  contracts,
  dateOnly,
  dateTime,
  direction as fmtDirection,
  escapeHtml,
  family,
  lifecycle,
  money,
  num,
  percent,
  signedPercent,
  tone,
  withUnit,
} from "../format";
import { card, chartBlock, empty, originBanner, pageTitle, src, tile } from "../components/ui";
import type { EquityDoc, Num, StrategiesDoc, StrategyRow } from "../types";

/** Soglie di promozione: engine/validation/lifecycle.py (Thresholds). Un valore mancante non passa. */
const DSR_MIN = 0.95;
const PBO_MAX = 0.5;
const OOS_SHARPE_MIN = 0;
const MIN_TRADES = 100;
const MIN_YEARS = 3;

type Bag = Record<string, unknown>;
type ShadowSeries = { t: string; v: Num }[];

// ---------------------------------------------------------------------------------------------- accessori
/** I blocchi `performance` e `validation` arrivano come JSON generico: si leggono senza fidarsi del tipo. */
function bagOf(value: unknown): Bag {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Bag) : {};
}

function numOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function strOf(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function yesNo(value: unknown): string {
  return value === true ? "sì" : value === false ? "no" : EMPTY;
}

function perf(r: StrategyRow): Bag {
  return bagOf(r.performance);
}

function val(r: StrategyRow): Bag {
  return bagOf(r.validation);
}

function backtestMetrics(r: StrategyRow): Bag {
  return bagOf(val(r)["metrics"]);
}

function backtestSharpe(r: StrategyRow): number | null {
  return numOf(backtestMetrics(r)["sharpe"]);
}

function backtestTrades(r: StrategyRow): number | null {
  return numOf(backtestMetrics(r)["n_trades"]);
}

/** Ordine della pagina: Sharpe del backtest, dal migliore; chi non ha operazioni (Sharpe n/d) va in fondo. */
function ranking(rows: StrategyRow[]): StrategyRow[] {
  return [...rows].sort((a, b) => {
    const sa = backtestSharpe(a);
    const sb = backtestSharpe(b);
    if (sa === null && sb === null) return a.id.localeCompare(b.id, "it", { numeric: true });
    if (sa === null) return 1;
    if (sb === null) return -1;
    return sb - sa || a.id.localeCompare(b.id, "it", { numeric: true });
  });
}

/** Le quattro verifiche di engine/validation/lifecycle.py, nello stesso ordine e con la stessa regola. */
function checkStatus(record: Bag): { dsr: boolean; pbo: boolean; oos: boolean; history: boolean } {
  const dsr = numOf(record["dsr_prob"]);
  const pbo = numOf(record["pbo"]);
  const oos = numOf(record["oos_sharpe_double_cost"]);
  const trades = numOf(record["n_trades"]);
  const years = numOf(record["years"]);
  return {
    dsr: dsr !== null && dsr > DSR_MIN,
    pbo: pbo !== null && pbo < PBO_MAX,
    oos: oos !== null && oos > OOS_SHARPE_MIN,
    history: (trades !== null && trades >= MIN_TRADES) || (years !== null && years >= MIN_YEARS),
  };
}

function lastTs(series: ShadowSeries): string | null {
  const last = series[series.length - 1];
  return last?.t ?? null;
}

/** Il colore della sparkline segue il segno del rendimento: fermo = neutro, non verde. */
function shadowColor(ret: number | null): string {
  if (ret === null || ret === 0) return cssVar("--text-muted");
  return ret > 0 ? cssVar("--pos") : cssVar("--neg");
}

function lifecycleChip(state: string | null | undefined): string {
  const cls = state === "active" ? "good" : state === "retired" ? "bad" : "warn";
  return `<span class="chip ${cls}">${escapeHtml(lifecycle(state))}</span>`;
}

function directionChip(dir: string | null | undefined): string {
  if (!dir) return `<span class="chip">nessuno</span>`;
  const cls = dir === "long" ? "good" : dir === "short" ? "bad" : "";
  return `<span class="chip ${cls}">${escapeHtml(fmtDirection(dir))}</span>`;
}

/** Il chip con la probabilità accanto, per le tabelle che non hanno una colonna dedicata. */
function signalChip(r: StrategyRow): string {
  const s = r.signal;
  if (!s?.direction) return directionChip(null);
  const cls = s.direction === "long" ? "good" : s.direction === "short" ? "bad" : "";
  return `<span class="chip ${cls}">${escapeHtml(fmtDirection(s.direction))}${
    s.prob == null ? "" : ` · ${percent(s.prob)}`
  }</span>`;
}

function strategyLink(r: StrategyRow): string {
  return `<a href="#/strategie/${encodeURIComponent(r.id)}">${escapeHtml(r.id)} · ${escapeHtml(r.name ?? "")}</a>`;
}

// ------------------------------------------------------------------------------------------------- pagina
export async function renderStrategies(el: HTMLElement): Promise<void> {
  const [doc, equity] = await Promise.all([load<StrategiesDoc>("strategies.json"), load<EquityDoc>("equity.json")]);
  const rows = doc.data?.strategies ?? [];
  const shadows = equity.data?.shadows ?? {};
  const generatedAt = doc.data?.generated_at ?? null;
  const hash = window.location.hash.replace(/^#/, "");
  const detailId = hash.startsWith("/strategie/") ? decodeURIComponent(hash.slice("/strategie/".length)) : null;

  if (!rows.length) {
    el.innerHTML =
      pageTitle("Strategie") +
      originBanner(doc, generatedAt) +
      empty(
        "Nessuna strategia pubblicata.",
        "La classifica dei conti ombra la scrive il job di fine giornata, dopo il settlement ICE.",
      );
    return;
  }

  if (detailId) {
    const row = rows.find((r) => r.id === detailId);
    if (row) {
      const series = shadows[row.id] ?? [];
      el.innerHTML =
        detailView(row, series, generatedAt, equity.data?.source ?? null) + originBannerFoot(doc, generatedAt);
      mountShadowChart(series);
      return;
    }
  }

  const ranked = ranking(rows);
  const withSignal = ranked.filter((r) => r.signal?.direction);
  const untested = ranked.filter((r) => backtestTrades(r) === 0);
  const totalWeight = rows.reduce((acc, r) => acc + (r.weight ?? 0), 0);
  const nActive = rows.filter((r) => r.lifecycle === "active").length;
  const years = rows.map((r) => numOf(val(r)["years"])).find((v) => v !== null) ?? null;
  const nObs = rows.map((r) => numOf(backtestMetrics(r)["n_obs"])).find((v) => v !== null) ?? null;
  const trials = rows.map((r) => numOf(bagOf(val(r)["dsr"])["n_trials"])).find((v) => v !== null) ?? null;
  const dsrPassed = rows.filter((r) => checkStatus(bagOf(val(r)["lifecycle_record"])).dsr).length;
  const windowLabel = `${num(years, 1)} anni · ${contracts(nObs)} osservazioni`;

  // ---------------------------------------------------------------- intestazione
  const tiles = `<div class="tiles" style="margin-bottom:12px">
    ${tile("Strategie", contracts(rows.length), "conti ombra da 10.000 $, sempre senza leva")}
    ${tile(
      "Peso nel master",
      percent(totalWeight),
      nActive ? `${contracts(nActive)} attive` : "nessuna promossa dalla validazione",
    )}
    ${tile("Segnali all'ultima chiusura", contracts(withSignal.length), `su ${contracts(rows.length)} strategie`)}
    ${tile("Tesi non testate", contracts(untested.length), `zero operazioni in ${num(years, 1)} anni di backtest`)}
  </div>`;

  // ---------------------------------------------------------------- perché i pesi sono a zero
  const whyCard = card(
    totalWeight > 0 ? "Come sono stati scelti i pesi" : "Perché nessuna strategia ha peso nel master",
    whyBody(ranked, totalWeight),
    src(doc.data?.source, generatedAt),
  );

  // ---------------------------------------------------------------- segnali di oggi
  const signalsCard = card(
    "Segnali all'ultima chiusura",
    withSignal.length
      ? signalsTable(withSignal)
      : empty(
          "Nessun segnale attivo.",
          "Una strategia che non vede la feature da cui dipende la direzione non emette segnali: tace invece di indovinare.",
        ),
    src(doc.data?.source, withSignal[0]?.signal?.ts ?? generatedAt),
  );

  // ---------------------------------------------------------------- classifica dei conti ombra
  const classificaCard = card(
    "Classifica dei conti ombra",
    `<p class="muted">Queste colonne sono il conto ombra aperto oggi: 10.000 $ per strategia, nessuna leva.
      Con una sola rilevazione distinta sono tutte a zero, quindi non possono ordinare niente: l'ordine è
      quello dello Sharpe del backtest (${escapeHtml(windowLabel)}), nella tabella qui sotto. La miniatura è
      la curva dell'equity ombra: tratteggiata quando le rilevazioni distinte sono meno di due.</p>` +
      shadowTable(ranked, shadows),
    src(equity.data?.source ?? doc.data?.source, equity.data?.generated_at ?? generatedAt),
  );

  // ---------------------------------------------------------------- backtest: il grafico e la decisione
  const scored = ranked
    .map((r) => ({ row: r, sharpe: backtestSharpe(r), trades: backtestTrades(r) }))
    .filter((x): x is { row: StrategyRow; sharpe: number; trades: number | null } => x.sharpe !== null);
  const backtestCard = card(
    "Backtest walk-forward: lo Sharpe che decide",
    (scored.length
      ? sharpeBars(scored.map((x) => ({ id: x.row.id, name: x.row.name ?? "", sharpe: x.sharpe, trades: x.trades }))) +
        `<p class="muted">Sharpe annualizzato su ${escapeHtml(windowLabel)}, costi del backtest inclusi,
          stime walk-forward (ogni parametro usa solo il passato). Barre a base zero: la lunghezza è il valore,
          il colore solo il segno.${
            dsrPassed === 0
              ? ` Nessuno di questi Sharpe supera il deflazionamento su ${contracts(
                  trials,
                )} prove registrate (DSR): è il motivo per cui il master è fermo.`
              : ""
          }</p>`
      : empty(
          "Nessuno Sharpe di backtest disponibile.",
          "Serve almeno un'operazione chiusa nella finestra di validazione.",
        )) +
      (untested.length
        ? `<p class="muted">${contracts(untested.length)} strategie non compaiono nel grafico: hanno chiuso
            zero operazioni in ${num(years, 1)} anni (${escapeHtml(untested.map((r) => r.id).join(" · "))}).
            Le feature che richiedono — mesi lontani della curva, scorte, macro, calendario OPEC+, stagionalità,
            opzioni — non coprono tutta la finestra: le loro tesi non sono state smentite, non sono state
            testate. È una differenza importante e non va nascosta dietro un «peso zero».</p>`
        : "") +
      backtestTable(ranked),
    src("engine/validation", generatedAt),
  );

  el.innerHTML =
    pageTitle(
      "Strategie",
      `${contracts(rows.length)} strategie · il conto ombra di oggi, il backtest che decide i pesi, il motivo di ciascuno`,
    ) +
    originBanner(doc, generatedAt) +
    tiles +
    whyCard +
    signalsCard +
    classificaCard +
    backtestCard;
}

/** Nel dettaglio il banner di provenienza va in fondo: la prima cosa da leggere è la strategia, non la CDN. */
function originBannerFoot(doc: Parameters<typeof originBanner>[0], generatedAt: string | null): string {
  const banner = originBanner(doc, generatedAt);
  return banner ? `<div style="margin-top:12px">${banner}</div>` : "";
}

// -------------------------------------------------------------------------------------- blocchi della lista
/** Il motivo del peso, raggruppato per testo identico: con 18 strategie in incubazione è UNA frase, non 18. */
function whyBody(rows: StrategyRow[], totalWeight: number): string {
  const groups = new Map<string, string[]>();
  for (const r of rows) {
    const text = strOf(r.weight_explanation) ?? "Nessuna spiegazione pubblicata dal motore.";
    const ids = groups.get(text) ?? [];
    ids.push(r.id);
    groups.set(text, ids);
  }
  const failed = { dsr: 0, pbo: 0, oos: 0, history: 0 };
  for (const r of rows) {
    const ok = checkStatus(bagOf(val(r)["lifecycle_record"]));
    if (!ok.dsr) failed.dsr += 1;
    if (!ok.pbo) failed.pbo += 1;
    if (!ok.oos) failed.oos += 1;
    if (!ok.history) failed.history += 1;
  }
  const n = rows.length;
  const chip = (label: string, count: number) =>
    `<span class="chip ${count ? "warn" : "good"}">${escapeHtml(label)}: ${contracts(count)} su ${contracts(
      n,
    )} non la superano</span>`;

  return (
    `<p>La promozione la decide il job settimanale sul backtest, non l'andamento di oggi, e servono tutte e
      quattro le condizioni: DSR &gt; ${num(DSR_MIN)}, PBO &lt; ${num(PBO_MAX)}, Sharpe fuori campione a costi
      raddoppiati &gt; ${num(OOS_SHARPE_MIN)}, esperienza di almeno ${contracts(MIN_TRADES)} operazioni oppure
      ${contracts(MIN_YEARS)} anni.</p>` +
    [...groups.entries()]
      .map(
        ([text, ids]) => `<div class="banner stack ${totalWeight > 0 ? "info" : "warn"}">
          <strong>${contracts(ids.length)} ${ids.length === 1 ? "strategia" : "strategie"}</strong>
          <p>${escapeHtml(text)}</p>
          <p class="muted">${escapeHtml(ids.join(" · "))}</p>
        </div>`,
      )
      .join("") +
    `<div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0">
      ${chip("DSR", failed.dsr)}
      ${chip("PBO", failed.pbo)}
      ${chip("Sharpe a costi 2×", failed.oos)}
      ${chip("Esperienza", failed.history)}
    </div>` +
    `<p class="muted">Peso zero non vuol dire strategia rotta: continua a operare sul conto ombra, con i suoi
      10.000 $ e senza leva, finché la validazione non la promuove. Il dettaglio di ciascuna strategia dice
      quale condizione manca, con valore e soglia.</p>`
  );
}

function signalsTable(rows: StrategyRow[]): string {
  const body = rows
    .map((r) => {
      const s = r.signal;
      return `<tr>
        <td class="wrap-text">${strategyLink(r)}</td>
        <td>${directionChip(s?.direction)}</td>
        <td class="num">${percent(s?.prob)}</td>
        <td class="num">${contracts(s?.horizon_days)}</td>
        <td>${dateTime(s?.ts)}</td>
        <td class="wrap-text">${escapeHtml(strOf(s?.rationale) ?? EMPTY)}</td>
      </tr>`;
    })
    .join("");
  return (
    `<div class="table-wrap"><table class="data">
      <thead><tr>
        <th class="wrap-text">Strategia</th>
        <th>Direzione</th>
        <th class="num">Probabilità</th>
        <th class="num">Orizzonte <span class="unit">giorni</span></th>
        <th>Emesso</th>
        <th class="wrap-text">Motivazione</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table></div>` +
    `<p class="muted">Un segnale è un'intenzione del conto ombra, non un ordine del master: con peso zero non
      diventa una posizione. La probabilità è quella dichiarata dalla strategia, non una previsione di prezzo.</p>`
  );
}

/** La classifica: le colonne sono il conto ombra di oggi, più la miniatura della sua curva. */
function shadowTable(rows: StrategyRow[], shadows: Record<string, ShadowSeries>): string {
  const body = rows
    .map((r) => {
      const p = perf(r);
      const ret = numOf(p["total_return"]);
      const series = shadows[r.id] ?? [];
      const points = toLineData(series, { intraday: true }).map((d) => d.value);
      const sharpe = numOf(p["sharpe"]);
      const sortino = numOf(p["sortino"]);
      const dd = numOf(p["max_drawdown"]);
      return `<tr>
        <td class="wrap-text">${strategyLink(r)}</td>
        <td>${escapeHtml(family(r.family))}</td>
        <td>${lifecycleChip(r.lifecycle)}</td>
        <td class="num">${percent(r.weight)}</td>
        <td>${sparkline(points, shadowColor(ret))}</td>
        <td class="num ${tone(ret)}">${signedPercent(ret)}</td>
        <td class="num ${tone(sharpe)}">${num(sharpe)}</td>
        <td class="num ${tone(sortino)}">${num(sortino)}</td>
        <td class="num ${tone(dd)}">${percent(dd)}</td>
        <td class="num">${percent(numOf(p["hit_rate"]))}</td>
        <td class="num">${num(numOf(p["profit_factor"]))}</td>
        <td class="num">${contracts(numOf(p["n_trades"]))}</td>
        <td>${signalChip(r)}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap tall"><table class="data">
    <thead><tr>
      <th class="wrap-text">Strategia</th>
      <th>Famiglia</th>
      <th>Stato</th>
      <th class="num">Peso</th>
      <th>Equity ombra</th>
      <th class="num">Rendimento</th>
      <th class="num">Sharpe</th>
      <th class="num">Sortino</th>
      <th class="num">Max DD</th>
      <th class="num">Hit rate</th>
      <th class="num">Profit factor</th>
      <th class="num">Operazioni</th>
      <th>Segnale</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}

/** Il backtest, strategia per strategia: i numeri che decidono il peso e la frase che lo spiega. */
function backtestTable(rows: StrategyRow[]): string {
  const body = rows
    .map((r) => {
      const m = backtestMetrics(r);
      const v = val(r);
      const record = bagOf(v["lifecycle_record"]);
      const sharpe = numOf(m["sharpe"]);
      const cagr = numOf(m["cagr"]);
      const dd = numOf(m["max_drawdown"]);
      const oos = numOf(record["oos_sharpe_double_cost"]);
      // La condizione mancata, che cambia da strategia a strategia; la frase sul ciclo di vita (identica
      // per tutte e 18) sta una volta sola nella card «Perché nessuna strategia ha peso» e nel dettaglio.
      const explanation = strOf(v["explanation"]);
      const weightWhy = strOf(r.weight_explanation);
      return `<tr>
        <td class="wrap-text">${strategyLink(r)}</td>
        <td class="num ${tone(sharpe)}">${num(sharpe)}</td>
        <td class="num ${tone(cagr)}">${signedPercent(cagr)}</td>
        <td class="num ${tone(dd)}">${percent(dd)}</td>
        <td class="num">${contracts(numOf(m["n_trades"]))}</td>
        <td class="num">${num(numOf(record["dsr_prob"]))}</td>
        <td class="num">${num(numOf(record["pbo"]))}</td>
        <td class="num ${tone(oos)}">${num(oos)}</td>
        <td class="wrap-text">${escapeHtml(explanation ?? weightWhy ?? EMPTY)}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap tall" style="margin-top:10px"><table class="data">
    <thead><tr>
      <th class="wrap-text">Strategia</th>
      <th class="num">Sharpe</th>
      <th class="num">CAGR</th>
      <th class="num">Max DD</th>
      <th class="num">Operazioni</th>
      <th class="num">DSR <span class="unit">soglia &gt; ${num(DSR_MIN)}</span></th>
      <th class="num">PBO <span class="unit">soglia &lt; ${num(PBO_MAX)}</span></th>
      <th class="num">Sharpe 2× costi</th>
      <th class="wrap-text">Condizione mancata</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}

// ------------------------------------------------------------------------------------------------ dettaglio
function detailView(
  r: StrategyRow,
  series: ShadowSeries,
  generatedAt: string | null,
  shadowSource: string | null,
): string {
  const p = perf(r);
  const v = val(r);
  const m = backtestMetrics(r);
  const dsr = bagOf(v["dsr"]);
  const costs = bagOf(v["costs"]);
  const record = bagOf(v["lifecycle_record"]);
  const years = numOf(v["years"]);
  const shadowPoints = toLineData(series, { intraday: true });
  const sharpe = numOf(m["sharpe"]);
  const signal = r.signal;

  const head =
    `<p><a href="#/strategie">← Torna alla classifica</a></p>` +
    pageTitle(`${r.id} · ${r.name ?? ""}`, `${family(r.family)} · ${lifecycle(r.lifecycle)}`);

  const tiles = `<div class="tiles" style="margin-bottom:12px">
    ${tile("Peso nel master", percent(r.weight), r.weight ? "nel portafoglio" : "solo conto ombra")}
    ${tile(
      "Sharpe backtest",
      num(sharpe),
      `${num(years, 1)} anni · ${contracts(numOf(m["n_trades"]))} operazioni`,
      tone(sharpe),
      "nessuna operazione chiusa nel backtest: lo Sharpe non esiste",
    )}
    ${tile(
      "DSR",
      num(numOf(dsr["dsr_prob"])),
      `soglia &gt; ${num(DSR_MIN)} · ${contracts(numOf(dsr["n_trials"]))} prove`,
      "",
      "senza operazioni non c'è uno Sharpe da deflazionare",
    )}
    ${tile("PBO", num(numOf(v["pbo"])), `soglia &lt; ${num(PBO_MAX)}`)}
  </div>`;

  const signalCard = card(
    "Segnale corrente",
    signal?.direction
      ? `<div style="margin-bottom:10px">${signalChip(r)}</div>
         <p>${escapeHtml(strOf(signal.rationale) ?? EMPTY)}</p>
         <dl class="kv">
           <dt>Direzione</dt><dd class="text">${escapeHtml(fmtDirection(signal.direction))}</dd>
           <dt>Probabilità dichiarata</dt><dd>${percent(signal.prob)}</dd>
           <dt>Orizzonte</dt><dd>${contracts(signal.horizon_days)} giorni</dd>
           <dt>Emesso</dt><dd class="text">${dateTime(signal.ts)}</dd>
         </dl>
         <p class="muted">Con peso zero il segnale resta sul conto ombra: non diventa un ordine del master.</p>`
      : empty(
          "Nessun segnale attivo.",
          "Quando manca la feature che decide la DIREZIONE la strategia non emette nulla: tace invece di indovinare.",
        ),
    src("engine/strategies", signal?.ts ?? generatedAt),
  );

  const weightCard = card("Perché questo peso", weightBody(r, record), src("engine/validation", generatedAt));

  const shadowCard = card(
    "Conto ombra (da oggi)",
    chartBlock({
      id: "shadow-chart",
      hasData: shadowPoints.length > 1,
      emptyMessage: "Il conto ombra non ha ancora una storia da disegnare.",
      emptyHint:
        shadowPoints.length === 1
          ? "C'è una sola rilevazione distinta: la curva compare dalla seconda, il motore segna l'equity ogni 30 minuti."
          : "Nessuna rilevazione pubblicata: la curva compare dal primo tick del motore.",
      legendItems: [{ label: "Equity del conto ombra", color: cssVar("--series-1") }],
      source: shadowSource,
      asof: lastTs(series),
    }) +
      metricTable([
        { label: "Rilevazioni", value: contracts(numOf(p["n_obs"])) },
        { label: "Equity iniziale", value: money(numOf(p["start_equity"])) },
        { label: "Equity attuale", value: money(numOf(p["end_equity"])) },
        {
          label: "Rendimento totale",
          value: signedPercent(numOf(p["total_return"])),
          cls: tone(numOf(p["total_return"])),
        },
        { label: "CAGR", value: percent(numOf(p["cagr"])) },
        { label: "Volatilità annualizzata", value: percent(numOf(p["ann_vol"])) },
        { label: "Sharpe", value: num(numOf(p["sharpe"])), cls: tone(numOf(p["sharpe"])) },
        { label: "Sortino", value: num(numOf(p["sortino"])), cls: tone(numOf(p["sortino"])) },
        { label: "Calmar", value: num(numOf(p["calmar"])) },
        { label: "Max drawdown", value: percent(numOf(p["max_drawdown"])), cls: tone(numOf(p["max_drawdown"])) },
        { label: "Durata del drawdown massimo", unit: "giorni", value: contracts(numOf(p["max_drawdown_days"])) },
        { label: "Hit rate", value: percent(numOf(p["hit_rate"])) },
        { label: "Profit factor", value: num(numOf(p["profit_factor"])) },
        { label: "Operazioni chiuse", value: contracts(numOf(p["n_trades"])) },
        { label: "Vincita media per operazione", value: money(numOf(p["avg_win"])) },
        { label: "Perdita media per operazione", value: money(numOf(p["avg_loss"])) },
        { label: "Turnover annualizzato", unit: "volte il book", value: num(numOf(p["turnover"])) },
        { label: "Tempo con posizione aperta", value: percent(numOf(p["exposure"])) },
        { label: "Giorno migliore", value: signedPercent(numOf(p["best_day"])), cls: tone(numOf(p["best_day"])) },
        { label: "Giorno peggiore", value: signedPercent(numOf(p["worst_day"])), cls: tone(numOf(p["worst_day"])) },
      ]) +
      yearBlock(p),
    // La provenienza di questa card la porta il grafico (chartBlock): due chip identici a 40px di distanza
    // sono rumore, non trasparenza.
    `<span class="value" style="font-size:var(--t-xl);font-weight:550;letter-spacing:-0.015em">${withUnit(
      money(numOf(p["end_equity"])),
    )}</span> <span class="sub" style="font-size:var(--t-xs)">equity ombra</span>`,
  );

  const backtestCard = card(
    "Backtest walk-forward",
    `<p class="muted">Stime walk-forward su ${num(years, 1)} anni e ${contracts(numOf(m["n_obs"]))} osservazioni
      (${contracts(numOf(m["periods_per_year"]))} per anno), costi inclusi. Sono questi i numeri che il job
      settimanale confronta con le soglie, non quelli del conto ombra.</p>` +
      metricTable([
        { label: "Rendimento totale", value: signedPercent(numOf(m["total_return"])), cls: tone(numOf(m["total_return"])) },
        { label: "CAGR", value: signedPercent(numOf(m["cagr"])), cls: tone(numOf(m["cagr"])) },
        { label: "Volatilità annualizzata", value: percent(numOf(m["ann_vol"])) },
        { label: "Sharpe", value: num(sharpe), cls: tone(sharpe) },
        { label: "Sortino", value: num(numOf(m["sortino"])), cls: tone(numOf(m["sortino"])) },
        { label: "Calmar", value: num(numOf(m["calmar"])) },
        { label: "Max drawdown", value: percent(numOf(m["max_drawdown"])), cls: tone(numOf(m["max_drawdown"])) },
        {
          label: "Durata del drawdown massimo",
          unit: "osservazioni",
          value: contracts(numOf(m["max_drawdown_duration"])),
        },
        { label: "Periodo più lungo sotto il picco", unit: "osservazioni", value: contracts(numOf(m["longest_underwater"])) },
        { label: "Hit rate", value: percent(numOf(m["hit_rate"])) },
        { label: "Asimmetria (skew)", value: num(numOf(m["skew"])) },
        { label: "Curtosi", value: num(numOf(m["kurtosis"])) },
        { label: "Curtosi in eccesso", value: num(numOf(m["excess_kurtosis"])) },
        { label: "PSR: probabilità che lo Sharpe vero sia positivo", value: percent(numOf(m["psr"])) },
        { label: "Track record minimo per PSR 95%", unit: "osservazioni", value: contracts(numOf(m["min_trl"])) },
        { label: "Turnover annualizzato", unit: "volte il book", value: num(numOf(m["turnover"])) },
        { label: "Operazioni", value: contracts(numOf(m["n_trades"])) },
        { label: "Profit factor", value: num(numOf(m["profit_factor"])) },
      ]) +
      `<h3 style="margin-top:14px">Sensibilità ai costi</h3>` +
      costsBlock(costs) +
      `<h3 style="margin-top:14px">Deflazionamento e overfitting</h3>` +
      `<dl class="kv">
        <dt>DSR (probabilità)</dt><dd>${num(numOf(dsr["dsr_prob"]))}</dd>
        <dt>Sharpe di riferimento (SR0)</dt><dd>${num(numOf(dsr["sr0"]))}</dd>
        <dt>Prove nel registro</dt><dd>${contracts(numOf(dsr["n_trials"]))}</dd>
        <dt>PBO (CSCV)</dt><dd>${num(numOf(v["pbo"]))}</dd>
      </dl>` +
      `<p class="muted">Il DSR corregge lo Sharpe per il numero di tentativi registrati, per la non normalità
        dei rendimenti e per la lunghezza del campione: una probabilità vicina a zero vuol dire che lo Sharpe
        osservato non si distingue dal rumore di ${contracts(numOf(dsr["n_trials"]))} prove. Un PBO vicino a
        ${num(PBO_MAX)} vuol dire che scegliere la variante migliore in campione non trasferisce niente fuori
        campione: è il valore atteso quando la selezione non ha informazione, non un errore del codice.</p>`,
    src("engine/validation", generatedAt),
  );

  const regimeCard = card("Risultati per regime", regimeTable(bagOf(v["by_regime"])), src("engine/validation", generatedAt));
  const crisisCard = card("Risultati per crisi", crisisTable(bagOf(v["by_crisis"])), src("engine/validation", generatedAt));

  const thesisCard = card(
    "Tesi economica",
    `<p class="muted">La tesi completa, chi è la controparte che perde, i regimi favorevoli e le condizioni di
      invalidazione sono documentate in <code>docs/STRATEGIES.md</code> nel repository. La dashboard non la
      riassume per non riscriverla in due posti.</p>`,
  );

  return head + tiles + signalCard + weightCard + shadowCard + backtestCard + regimeCard + crisisCard + thesisCard;
}

/** Il motivo del peso, per esteso e senza troncature, più la verifica condizione per condizione. */
function weightBody(r: StrategyRow, record: Bag): string {
  const ok = checkStatus(record);
  const explanation = strOf(val(r)["explanation"]);
  const weightWhy = strOf(r.weight_explanation);
  const trades = numOf(record["n_trades"]);
  const years = numOf(record["years"]);
  const rows: { label: string; note?: string; value: string; threshold: string; ok: boolean }[] = [
    {
      label: "Deflated Sharpe Ratio (probabilità)",
      value: num(numOf(record["dsr_prob"])),
      threshold: `&gt; ${num(DSR_MIN)}`,
      ok: ok.dsr,
    },
    {
      label: "Probability of Backtest Overfitting",
      value: num(numOf(record["pbo"])),
      threshold: `&lt; ${num(PBO_MAX)}`,
      ok: ok.pbo,
    },
    {
      label: "Sharpe fuori campione a costi raddoppiati",
      value: num(numOf(record["oos_sharpe_double_cost"])),
      threshold: `&gt; ${num(OOS_SHARPE_MIN)}`,
      ok: ok.oos,
    },
    {
      label: "Esperienza nel backtest",
      note: "basta una delle due",
      value: `${contracts(trades)}<span class="unit">op.</span> / ${num(years, 1)}<span class="unit">anni</span>`,
      threshold: `≥ ${contracts(MIN_TRADES)} op. o ≥ ${contracts(MIN_YEARS)} anni`,
      ok: ok.history,
    },
  ];
  return (
    `<p>${escapeHtml(weightWhy ?? "Nessuna spiegazione pubblicata dal motore.")}</p>` +
    (explanation && explanation !== weightWhy
      ? `<p class="muted">Dalla validazione: ${escapeHtml(explanation)}</p>`
      : "") +
    `<div class="table-wrap"><table class="data">
      <thead><tr>
        <th class="wrap-text">Condizione di promozione</th>
        <th class="num">Valore</th>
        <th class="num">Soglia</th>
        <th>Esito</th>
      </tr></thead>
      <tbody>${rows
        .map(
          (c) => `<tr>
            <td class="wrap-text">${escapeHtml(c.label)}${
              c.note ? ` <span class="muted">(${escapeHtml(c.note)})</span>` : ""
            }</td>
            <td class="num">${c.value}</td>
            <td class="num">${c.threshold}</td>
            <td><span class="chip ${c.ok ? "good" : "warn"}">${c.ok ? "superata" : "non superata"}</span></td>
          </tr>`,
        )
        .join("")}</tbody>
    </table></div>` +
    `<dl class="kv" style="margin-top:10px">
      <dt>Backtest validato</dt><dd class="text">${yesNo(record["validated"])}</dd>
      <dt>Allarme di decadimento (CUSUM)</dt><dd class="text">${yesNo(record["cusum_alarm"])}</dd>
      <dt>Peso attuale</dt><dd>${percent(r.weight)}</dd>
    </dl>` +
    `<p class="muted">Soglie di engine/validation/lifecycle.py; un valore mancante NON passa, perché una serie
      senza operazioni non dimostra niente. All'allarme CUSUM una strategia attiva passa a ritirata e il peso
      torna a zero.</p>`
  );
}

/** Tabella metrica/valore: l'unità sta nell'etichetta di riga, la cella porta solo la cifra. */
function metricTable(rows: { label: string; unit?: string; value: string; cls?: string }[]): string {
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th class="wrap-text">Metrica</th><th class="num">Valore</th></tr></thead>
    <tbody>${rows
      .map(
        (r) => `<tr>
          <td class="wrap-text">${escapeHtml(r.label)}${
            r.unit ? ` <span class="unit">${escapeHtml(r.unit)}</span>` : ""
          }</td>
          <td class="num ${r.cls ?? ""}">${r.value}</td>
        </tr>`,
      )
      .join("")}</tbody>
  </table></div>`;
}

function costsBlock(costs: Bag): string {
  const x1 = bagOf(costs["x1"]);
  const x2 = bagOf(costs["x2"]);
  const labels = bagOf(costs["labels"]);
  if (costs["available"] === false || (!Object.keys(x1).length && !Object.keys(x2).length)) {
    return empty(
      "Sensibilità ai costi non disponibile.",
      strOf(costs["reason"]) ?? "Serve una serie di turnover ricostruibile per raddoppiare i costi.",
    );
  }
  const l1 = strOf(labels["x1"]) ?? "costi 1x";
  const l2 = strOf(labels["x2"]) ?? "costi 2x";
  const rows: { label: string; unit?: string; fmt: (bag: Bag) => string; cls?: (bag: Bag) => string }[] = [
    { label: "Costo per turnover", unit: "bps", fmt: (b) => num(numOf(b["cost_per_turn_bps"])) },
    { label: "Costo totale sul periodo", fmt: (b) => percent(numOf(b["total_cost"])) },
    { label: "Costo annualizzato", fmt: (b) => percent(numOf(b["ann_cost"])) },
    { label: "Sharpe", fmt: (b) => num(numOf(b["sharpe"])), cls: (b) => tone(numOf(b["sharpe"])) },
    { label: "CAGR", fmt: (b) => signedPercent(numOf(b["cagr"])), cls: (b) => tone(numOf(b["cagr"])) },
    { label: "Volatilità annualizzata", fmt: (b) => percent(numOf(b["ann_vol"])) },
    { label: "Max drawdown", fmt: (b) => percent(numOf(b["max_drawdown"])) },
    { label: "Calmar", fmt: (b) => num(numOf(b["calmar"])) },
  ];
  const body = rows
    .map(
      (row) => `<tr>
        <td class="wrap-text">${escapeHtml(row.label)}${
          row.unit ? ` <span class="unit">${escapeHtml(row.unit)}</span>` : ""
        }</td>
        <td class="num ${row.cls ? row.cls(x1) : ""}">${row.fmt(x1)}</td>
        <td class="num ${row.cls ? row.cls(x2) : ""}">${row.fmt(x2)}</td>
      </tr>`,
    )
    .join("");
  return (
    `<div class="table-wrap"><table class="data">
      <thead><tr>
        <th class="wrap-text">Metrica</th>
        <th class="num">${escapeHtml(l1)}</th>
        <th class="num">${escapeHtml(l2)}</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table></div>` +
    `<dl class="kv" style="margin-top:10px">
      <dt>Turnover medio per periodo</dt><dd>${percent(numOf(costs["mean_turnover"]))}</dd>
      <dt>Costo di breakeven <span class="unit">bps</span></dt><dd>${num(numOf(costs["breakeven_cost_bps"]))}</dd>
    </dl>` +
    `<p class="muted">Il costo di breakeven è il costo per turnover che azzera il margine: negativo vuol dire
      che il margine non c'era nemmeno ai costi del backtest. Se lo Sharpe sparisce raddoppiando i costi, il
      margine non era un margine.</p>`
  );
}

function regimeTable(byRegime: Bag): string {
  const entries = Object.entries(byRegime);
  if (!entries.length) {
    return empty(
      "Nessuna scomposizione per regime.",
      "La calcola il job settimanale insieme al backtest completo.",
    );
  }
  const body = entries
    .map(([label, raw]) => {
      const b = bagOf(raw);
      const ret = numOf(b["total_return"]);
      const dd = numOf(b["max_drawdown"]);
      const sharpe = numOf(b["sharpe"]);
      return `<tr>
        <td class="wrap-text">${escapeHtml(label)}${
          b["enough_obs"] === false ? ' <span class="chip warn">campione troppo corto</span>' : ""
        }</td>
        <td class="num">${contracts(numOf(b["n_obs"]))}</td>
        <td class="num ${tone(sharpe)}">${num(sharpe)}</td>
        <td class="num ${tone(ret)}">${signedPercent(ret)}</td>
        <td class="num">${percent(numOf(b["ann_vol"]))}</td>
        <td class="num ${tone(dd)}">${percent(dd)}</td>
        <td class="num">${percent(numOf(b["hit_rate"]))}</td>
      </tr>`;
    })
    .join("");
  return (
    `<div class="table-wrap"><table class="data">
      <thead><tr>
        <th class="wrap-text">Regime</th>
        <th class="num">Osservazioni</th>
        <th class="num">Sharpe</th>
        <th class="num">Rendimento</th>
        <th class="num">Vol. annualizzata</th>
        <th class="num">Max DD</th>
        <th class="num">Hit rate</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table></div>` +
    `<p class="muted">Lo Sharpe è n/d quando nel regime non c'è stata nessuna operazione: la strategia era
      ferma, non in perdita.</p>`
  );
}

function crisisTable(byCrisis: Bag): string {
  const entries = Object.entries(byCrisis);
  if (!entries.length) {
    return empty("Nessuna scomposizione per crisi.", "La calcola il job settimanale sugli episodi di config.");
  }
  const body = entries
    .map(([key, raw]) => {
      const b = bagOf(raw);
      const name = strOf(b["name"]) ?? key;
      const period = `${dateOnly(strOf(b["start"]))} → ${dateOnly(strOf(b["end"]))}`;
      if (b["available"] === false) {
        return `<tr>
          <td class="wrap-text">${escapeHtml(name)}</td>
          <td>${escapeHtml(period)}</td>
          <td class="wrap-text" colspan="5">${escapeHtml(strOf(b["reason"]) ?? "episodio non valutabile")}</td>
        </tr>`;
      }
      const ret = numOf(b["total_return"]);
      const dd = numOf(b["max_drawdown"]);
      const worst = numOf(b["worst_day"]);
      const sharpe = numOf(b["sharpe"]);
      return `<tr>
        <td class="wrap-text">${escapeHtml(name)}</td>
        <td>${escapeHtml(period)}</td>
        <td class="num">${contracts(numOf(b["n_obs"]))}</td>
        <td class="num ${tone(sharpe)}">${num(sharpe)}</td>
        <td class="num ${tone(ret)}">${signedPercent(ret)}</td>
        <td class="num ${tone(dd)}">${percent(dd)}</td>
        <td class="num ${tone(worst)}">${signedPercent(worst)}</td>
      </tr>`;
    })
    .join("");
  return (
    `<div class="table-wrap"><table class="data">
      <thead><tr>
        <th class="wrap-text">Episodio</th>
        <th>Periodo</th>
        <th class="num">Osservazioni</th>
        <th class="num">Sharpe</th>
        <th class="num">Rendimento</th>
        <th class="num">Max DD</th>
        <th class="num">Giorno peggiore</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table></div>` +
    `<p class="muted">Gli episodi coprono 1990-91, 2008, 2014-16, 2019, 2020, 2022 e il 2026 in corso. Un
      episodio con meno di cinque giorni di backtest non viene valutato: è scritto perché, non lasciato vuoto.</p>`
  );
}

// -------------------------------------------------------------------------------------------------- grafici
/**
 * Barre orizzontali a base zero dello Sharpe di backtest, una riga per strategia.
 *
 * Disegnata qui e non in charts/svg.ts perché è l'unico posto che mette le strategie su un asse: la
 * lunghezza è il valore, il colore porta solo il segno (verde/rosso come tone() per le cifre), l'identità
 * e il numero sono etichette dirette, quindi non dipendono dal colore. Chi non ha operazioni non ha barra:
 * la riga di testo sotto il grafico dice chi e perché.
 */
function sharpeBars(rows: { id: string; name: string; sharpe: number; trades: number | null }[]): string {
  if (!rows.length) return "";
  // .svg-chart scala a larghezza 100%: con un viewBox da 640 il testo da 10px finisce a 5,6px su un
  // telefono. A 420 il fattore è ~0,85 su telefono e ~1,5 su desktop, e le etichette restano leggibili.
  const w = 420;
  const rowH = 20;
  const barH = 12;
  const padTop = 22;
  const padBottom = 6;
  const h = padTop + rows.length * rowH + padBottom;
  const plotL = 32;
  const plotR = w - 8;
  const values = rows.map((r) => r.sharpe);
  const lo0 = Math.min(0, ...values);
  const hi0 = Math.max(0, ...values);
  // Il 22% di margine su ciascun lato è lo spazio per l'etichetta del valore oltre la punta della barra.
  const pad = (hi0 - lo0) * 0.22 || 0.1;
  const lo = lo0 - pad;
  const hi = hi0 + pad;
  const x = (v: number) => plotL + ((v - lo) / (hi - lo)) * (plotR - plotL);
  const zero = x(0);
  const bars = rows
    .map((r, i) => {
      const top = padTop + i * rowH + (rowH - barH) / 2;
      const mid = top + barH / 2;
      const end = x(r.sharpe);
      const positive = r.sharpe >= 0;
      const color = r.sharpe > 0 ? "var(--pos)" : r.sharpe < 0 ? "var(--neg)" : "var(--text-muted)";
      const title = `${r.id} · ${r.name} · Sharpe ${num(r.sharpe)} · ${contracts(r.trades)} operazioni`;
      return `<g>
        <title>${escapeHtml(title)}</title>
        <text x="0" y="${mid.toFixed(1)}" dominant-baseline="middle">${escapeHtml(r.id)}</text>
        <path d="${hBar(Math.min(zero, end), Math.max(zero, end), top, barH, positive)}" fill="${color}" />
        <text class="value" x="${(positive ? end + 5 : end - 5).toFixed(1)}" y="${mid.toFixed(1)}"
          dominant-baseline="middle" text-anchor="${positive ? "start" : "end"}">${num(r.sharpe)}</text>
      </g>`;
    })
    .join("");
  const best = rows[0];
  return `<svg viewBox="0 0 ${w} ${h}" class="svg-chart" role="img"
      aria-label="Sharpe del backtest per ${rows.length} strategie, da ${best.id} ${num(best.sharpe)} in giù, barre a base zero">
    <line x1="${zero.toFixed(1)}" y1="${padTop - 10}" x2="${zero.toFixed(1)}" y2="${h - padBottom}"
      stroke="var(--border-strong)" stroke-width="1" />
    <text x="${zero.toFixed(1)}" y="${padTop - 13}" text-anchor="middle">0</text>
  ${bars}
  </svg>`;
}

/** Rettangolo con l'estremità del DATO arrotondata (4px) e quella sulla linea dello zero piatta. */
function hBar(x0: number, x1: number, y: number, h: number, positive: boolean): string {
  const r = Math.min(4, h / 2, Math.max(0, x1 - x0));
  const bot = y + h;
  if (positive) {
    return `M${x0.toFixed(1)},${y.toFixed(1)} H${(x1 - r).toFixed(1)} Q${x1.toFixed(1)},${y.toFixed(1)} ${x1.toFixed(
      1,
    )},${(y + r).toFixed(1)} V${(bot - r).toFixed(1)} Q${x1.toFixed(1)},${bot.toFixed(1)} ${(x1 - r).toFixed(
      1,
    )},${bot.toFixed(1)} H${x0.toFixed(1)} Z`;
  }
  return `M${x1.toFixed(1)},${y.toFixed(1)} H${(x0 + r).toFixed(1)} Q${x0.toFixed(1)},${y.toFixed(1)} ${x0.toFixed(
    1,
  )},${(y + r).toFixed(1)} V${(bot - r).toFixed(1)} Q${x0.toFixed(1)},${bot.toFixed(1)} ${(x0 + r).toFixed(
    1,
  )},${bot.toFixed(1)} H${x1.toFixed(1)} Z`;
}

/** Il rendimento per anno del conto ombra: barre verticali a base zero, con il valore sopra la punta. */
function yearBlock(p: Bag): string {
  const entries = Object.entries(bagOf(p["by_year"]))
    .map(([year, value]) => ({ year, value: numOf(value) }))
    .filter((e): e is { year: string; value: number } => e.value !== null)
    .sort((a, b) => a.year.localeCompare(b.year));
  if (!entries.length) return "";
  const flat = entries.every((e) => e.value === 0);
  return (
    `<h3 style="margin-top:14px">Rendimento per anno</h3>` +
    yearBars(entries) +
    `<p class="muted">Barre a base zero: un anno chiuso in pari non ha barra, solo l'etichetta sulla linea
      dello zero.${
        flat
          ? ` ${entries.length === 1 ? "L'unico anno" : "Ogni anno"} del conto ombra è ancora a zero: il conto è aperto da oggi.`
          : ""
      }</p>`
  );
}

function yearBars(entries: { year: string; value: number }[]): string {
  const w = 420;
  // Con uno o due anni un riquadro alto come un grafico sarebbe un riquadro vuoto: la striscia basta.
  const h = entries.length <= 2 ? 96 : 144;
  const padX = 8;
  const padTop = 22;
  const padBottom = 30;
  const values = entries.map((e) => e.value);
  let lo = Math.min(0, ...values);
  let hi = Math.max(0, ...values);
  if (hi === lo) {
    // Tutti gli anni a zero: la linea dello zero sta in mezzo, con le etichette sopra. Nessuna barra.
    lo = -0.01;
    hi = 0.01;
  } else {
    const pad = (hi - lo) * 0.18;
    lo -= pad;
    hi += pad;
  }
  const y = (v: number) => padTop + (1 - (v - lo) / (hi - lo)) * (h - padTop - padBottom);
  const zero = y(0);
  const slot = (w - 2 * padX) / entries.length;
  const bw = Math.min(44, slot * 0.5);
  const body = entries
    .map((e, i) => {
      const cx = padX + slot * (i + 0.5);
      const top = Math.min(zero, y(e.value));
      const bottom = Math.max(zero, y(e.value));
      const positive = e.value >= 0;
      const color = e.value > 0 ? "var(--pos)" : e.value < 0 ? "var(--neg)" : "var(--text-muted)";
      const bar =
        e.value === 0
          ? ""
          : `<path d="${vBar(cx - bw / 2, cx + bw / 2, top, bottom, positive)}" fill="${color}" />`;
      const labelY = positive ? top - 6 : bottom + 11;
      return `<g>
        <title>${escapeHtml(`${e.year}: ${signedPercent(e.value)}`)}</title>
        ${bar}
        <text class="value" x="${cx.toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="middle">${signedPercent(
          e.value,
        )}</text>
        <text x="${cx.toFixed(1)}" y="${h - 6}" text-anchor="middle">${escapeHtml(e.year)}</text>
      </g>`;
    })
    .join("");
  return `<svg viewBox="0 0 ${w} ${h}" class="svg-chart" role="img"
      aria-label="Rendimento per anno del conto ombra su ${entries.length} ${
        entries.length === 1 ? "anno" : "anni"
      }, barre a base zero">
    <line x1="${padX}" y1="${zero.toFixed(1)}" x2="${w - padX}" y2="${zero.toFixed(1)}"
      stroke="var(--border-strong)" stroke-width="1" />
    ${body}
  </svg>`;
}

/** Barra verticale con l'estremità del DATO arrotondata e quella sulla linea dello zero piatta. */
function vBar(x0: number, x1: number, top: number, bottom: number, positive: boolean): string {
  const r = Math.min(4, (x1 - x0) / 2, Math.max(0, bottom - top));
  if (positive) {
    return `M${x0.toFixed(1)},${bottom.toFixed(1)} V${(top + r).toFixed(1)} Q${x0.toFixed(1)},${top.toFixed(
      1,
    )} ${(x0 + r).toFixed(1)},${top.toFixed(1)} H${(x1 - r).toFixed(1)} Q${x1.toFixed(1)},${top.toFixed(
      1,
    )} ${x1.toFixed(1)},${(top + r).toFixed(1)} V${bottom.toFixed(1)} Z`;
  }
  return `M${x0.toFixed(1)},${top.toFixed(1)} V${(bottom - r).toFixed(1)} Q${x0.toFixed(1)},${bottom.toFixed(
    1,
  )} ${(x0 + r).toFixed(1)},${bottom.toFixed(1)} H${(x1 - r).toFixed(1)} Q${x1.toFixed(1)},${bottom.toFixed(
    1,
  )} ${x1.toFixed(1)},${(bottom - r).toFixed(1)} V${top.toFixed(1)} Z`;
}

/** La curva dell'equity ombra, montata solo quando le rilevazioni DISTINTE sono almeno due. */
function mountShadowChart(series: ShadowSeries): void {
  const host = document.getElementById("shadow-chart");
  if (!host) return; // chartBlock ha già reso lo stato vuoto
  const data = toLineData(series, { intraday: true });
  if (data.length < 2) return;
  const chart = baseChart(host, {
    timeVisible: !spansMultipleDays(series),
    priceFormatter: (v: number) => num(v, 0),
  });
  const area = addArea(chart, data, cssVar("--series-1"), 0.14, { precision: 0 });
  const strip = document.getElementById("shadow-chart-readout");
  if (strip) {
    attachReadout(chart, strip, [
      { api: area, label: "Conto ombra", color: cssVar("--series-1"), fmt: (v) => money(v) },
    ]);
  }
  fit(chart);
}
