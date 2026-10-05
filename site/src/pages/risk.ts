/** Rischio: esposizione corrente, storico della leva, margini, VaR/ES, rischio di rovina, stress test.
 *
 * `risk.json` ships an `unavailable` list: one sentence per block the engine could not compute, with the real
 * reason. Each sentence is routed into the empty state of the block it is about, so a missing block reads as a
 * declared limit instead of a hole in the page; whatever is left over is printed at the bottom, never dropped.
 *
 * The leverage history is INTRADAY — one mark per 30-minute tick. Deduping it by calendar date collapsed six
 * real marks into a single point and the page then drew an empty 180px box, so the series is built once with
 * `intraday: true` and that same deduped array decides both the markup branch and the mount.
 */
import { addLine, attachReadout, baseChart, cssVar, fit, spansMultipleDays, toLineData } from "../charts/base";
import { load } from "../data";
import {
  DASH,
  EMPTY,
  contracts,
  escapeHtml,
  leverage as fmtLev,
  money,
  notional,
  num,
  percent,
  price,
  relative,
  stamp,
  tone,
  withUnit,
} from "../format";
import { card, chartBlock, empty, originBanner, pageTitle, src, stat, tile } from "../components/ui";
import type { EquityDoc, RiskDoc, SummaryDoc } from "../types";

const DEFAULT_SOURCE = "engine/monitoring/risk_report.py";
const LEV_COLOR = "--series-4";

/** Narrowing helpers: everything outside the typed keys of RiskDoc arrives as `unknown`. */
const asNum = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const asStr = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);
const asRec = (v: unknown): Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const asRows = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => x !== null && typeof x === "object") : [];

/** In a table a missing figure is a dash, not the "n/d" a tile uses. */
const cell = (formatted: string): string => (formatted === EMPTY ? DASH : formatted);

/** A loss magnitude is never green: VaR, ES and drawdowns are POSITIVE numbers that mean damage, so tone()
 *  would paint them as a gain. Only signed returns go through tone(). */
const lossTone = (v: number | null): string => (v !== null && v > 0 ? "down" : "");

/** The engine ships its own Italian labels for the Monte Carlo and the stress block; prefer them. */
const labelled = (labels: Record<string, unknown>, key: string, fallback: string): string =>
  asStr(labels[key]) ?? fallback;

/** A boolean that means "something bad happened". Unknown stays a dash: no is a claim, not a default. */
const flagChip = (v: unknown): string => {
  if (v === true) return '<span class="chip bad">sì</span>';
  if (v === false) return "no";
  return DASH;
};

export async function renderRisk(el: HTMLElement): Promise<void> {
  const [risk, equity, summary] = await Promise.all([
    load<RiskDoc>("risk.json"),
    load<EquityDoc>("equity.json"),
    load<SummaryDoc>("summary.json"),
  ]);
  const r = risk.data;
  const acct = summary.data?.account ?? {};
  const lev = acct.leverage ?? {};
  const source = asStr(r?.["source"]) ?? DEFAULT_SOURCE;
  const generatedAt = r?.generated_at ?? summary.data?.generated_at ?? null;

  // The engine's own list of what it could not compute. Each block takes the sentence that is about it.
  const pool = (Array.isArray(r?.["unavailable"]) ? (r?.["unavailable"] as unknown[]) : [])
    .map((m) => asStr(m))
    .filter((m): m is string => m !== null);
  // Most specific needle first: "var" is short enough to collide with another sentence, so it goes last.
  const levReason = takeReason(pool, "della leva", "leverage");
  const ruinReason = takeReason(pool, "rovina");
  const stressReason = takeReason(pool, "stress");
  const varReason = takeReason(pool, "var", "shortfall");

  const margin = asRec(r?.margin);
  const levStats = asRec(r?.["leverage_stats"]);
  const breakers = asRec(r?.["breakers"]);
  const initial = asNum(r?.["initial_capital"]) ?? acct.initial_capital ?? null;

  // risk.json is this page's own document, so it wins; summary.json is the fallback for a partial payload.
  const marginUsed = asNum(margin["used"]) ?? acct.margin_used ?? null;
  const marginLevel = asNum(margin["level"]) ?? acct.margin_level ?? null;
  const grossNotional = asNum(margin["gross_notional"]) ?? acct.gross_notional ?? null;
  const liqPrice = asNum(margin["liquidation_price"]) ?? acct.liquidation_price ?? null;
  const marginRate = asNum(margin["rate"]);
  const stopOut = asNum(margin["stop_out_level"]);
  const capLev = asNum(levStats["cap"]);

  // ---------------------------------------------------------------- esposizione adesso
  const levMean = asNum(levStats["mean"]);
  const levP95 = asNum(levStats["p95"]);
  const maxSub = [levMean === null ? "" : `media ${fmtLev(levMean)}`, levP95 === null ? "" : `p95 ${fmtLev(levP95)}`]
    .filter(Boolean)
    .join(" · ");
  const tiles = `<div class="tiles" style="margin-bottom:12px">
    ${tile("Leva attuale", fmtLev(lev.value), escapeHtml(asStr(lev.limited_by) ?? "nessun limite attivo"), "", "nessuna marcatura del conto registrata")}
    ${tile("Leva massima storica", fmtLev(asNum(levStats["max"])), maxSub, "", "storico della leva non disponibile")}
    ${tile("Margine richiesto", notional(marginUsed), marginLevel === null ? "nessuna posizione aperta" : `livello ${num(marginLevel, 2)}`)}
    ${tile("Prezzo di liquidazione", price(liqPrice), "stima al requisito di margine", "", "richiede una posizione aperta")}
  </div>`;

  // ---------------------------------------------------------------- margini e liquidazione
  const marginNote =
    marginRate !== null && capLev !== null
      ? `Requisito di margine e tetto di leva sono la stessa regola vista da due lati: con un margine pari al
         ${percent(marginRate, 0)} del nozionale, il nozionale massimo è ${fmtLev(capLev)} l'equity.`
      : "Il margine richiesto è una percentuale fissa del nozionale lordo.";
  const marginBlock = `<dl class="kv">
      <dt>Nozionale lordo</dt><dd>${notional(grossNotional)}</dd>
      <dt>Requisito di margine</dt><dd>${percent(marginRate, 0)}</dd>
      <dt>Margine richiesto</dt><dd>${notional(marginUsed)}</dd>
      <dt>Livello di margine (equity / margine)</dt><dd>${num(marginLevel, 2)}</dd>
      <dt>Soglia di stop-out</dt><dd>${num(stopOut, 2)}</dd>
      <dt>Prezzo di liquidazione stimato</dt><dd>${price(liqPrice)}</dd>
    </dl>
    <p class="muted" style="margin-bottom:0">${marginNote} Quando il livello di margine scende sotto la soglia di
    stop-out il broker simulato riduce la posizione alla chiusura con slippage peggiorativo, fino a riportare il
    livello a 1,00 o ad azzerare la posizione.</p>`;

  // ---------------------------------------------------------------- storico della leva
  const rawLev: { t: string; v: number | null }[] = (r?.leverage_history ?? []).length
    ? (r?.leverage_history ?? []).map((p) => ({ t: p.t, v: p.v }))
    : (equity.data?.series ?? []).map((p) => ({ t: p.t, v: p.leverage ?? null }));
  // ONE deduped series: it decides the markup branch AND the mount. Today's six raw rows carry three
  // distinct instants, and a single calendar date, so counting raw rows promised a chart of six points
  // while toLineData handed the mount one, and the box came out empty.
  const levData = toLineData(rawLev, { intraday: true });
  const hasLev = levData.length > 1;
  const levValues = levData.map((p) => p.value);
  const levFlat = hasLev && Math.max(...levValues) === Math.min(...levValues);
  const levAsof = rawLev.length ? rawLev[rawLev.length - 1].t : null;

  const levStatsBlock = Object.keys(levStats).length
    ? `<dl class="kv" style="margin-top:12px">
        <dt>Leva media</dt><dd>${fmtLev(levMean)}</dd>
        <dt>Leva massima</dt><dd>${fmtLev(asNum(levStats["max"]))}</dd>
        <dt>95° percentile</dt><dd>${fmtLev(levP95)}</dd>
        <dt>Rilevazioni sopra 1x</dt><dd>${contracts(asNum(levStats["days_above_1x"]))}</dd>
        <dt>Tetto assoluto</dt><dd>${fmtLev(capLev)}</dd>
      </dl>`
    : "";
  const levNote = `<p class="muted" style="margin-bottom:0">Sopra 1x serve il gate alpha, cioè tutte e sette le
    condizioni soddisfatte${capLev === null ? "" : `, e il tetto assoluto resta ${fmtLev(capLev)}`}: in pratica la
    leva resta vicina a 1x fino a quando la volatilità non scende. Le rilevazioni sono quelle del conto, una ogni
    30 minuti, non giorni di borsa.</p>`;

  // ---------------------------------------------------------------- VaR ed ES
  const varRec = asRec(r?.var);
  const esRec = asRec(r?.es);
  const varLevels = [...new Set([...Object.keys(varRec), ...Object.keys(esRec)])];
  const varMethod = asStr(r?.["var_source"]);
  const varBlock =
    (varLevels.length
      ? `<div class="table-wrap"><table class="data">
          <thead><tr>
            <th>Livello e orizzonte</th>
            <th class="num">VaR <span class="unit">dell'equity</span></th>
            <th class="num">ES <span class="unit">dell'equity</span></th>
          </tr></thead>
          <tbody>${varLevels
            .map((k) => {
              const v = asNum(varRec[k]);
              const e = asNum(esRec[k]);
              return `<tr>
                <td>${escapeHtml(k)}</td>
                <td class="num ${lossTone(v)}">${cell(percent(v, 2))}</td>
                <td class="num ${lossTone(e)}">${cell(percent(e, 2))}</td>
              </tr>`;
            })
            .join("")}</tbody></table></div>
          ${varMethod ? `<p class="muted" style="margin-top:10px">Metodo: ${escapeHtml(varMethod)}.</p>` : ""}`
      : empty(
          "VaR ed Expected Shortfall non ancora disponibili.",
          varReason ?? "Il VaR storico richiede almeno 30 giorni di equity.",
        )) +
    `<p class="muted" style="margin:10px 0 0">Entrambi sono perdite espresse come numero positivo, in frazione
     dell'equity, su un giorno. Li ricalcola il job settimanale (<code>weekly</code>) insieme al rischio di rovina:
     finché lo storico del conto è corto il motore usa la coda parametrica Student-t dell'esposizione corrente,
     dichiarandolo, invece di inventare un quantile.</p>`;

  // ---------------------------------------------------------------- soglie automatiche
  const dailyLoss = asNum(breakers["daily_loss"]);
  const deadFraction = asNum(breakers["dead_equity_fraction"]);
  const status = asStr(breakers["status"]) ?? asStr(acct.status);
  const breakersBlock = `<dl class="kv">
      <dt>Perdita giornaliera massima</dt><dd>${percent(dailyLoss, 0)}</dd>
      <dt>Conto dichiarato morto sotto</dt><dd>${percent(deadFraction, 0)}</dd>
      <dt>Capitale iniziale di riferimento</dt><dd>${notional(initial)}</dd>
      <dt>Soglia di stop-out</dt><dd>${num(stopOut, 2)}</dd>
      <dt>Tetto di leva</dt><dd>${fmtLev(capLev)}</dd>
    </dl>
    <p style="margin:12px 0 0">${accountStatusChip(status)}</p>
    <p class="muted" style="margin:10px 0 0">Superata la perdita giornaliera sull'equity di apertura, il broker
    simulato chiude tutto e resta fermo fino alla seduta successiva. Se l'equity scende a
    ${deadFraction === null ? "la frazione minima" : percent(deadFraction, 0)} del capitale iniziale il conto è
    dichiarato morto, l'epoca viene archiviata e il trading si ferma fino al reset.</p>`;

  // ---------------------------------------------------------------- rischio di rovina e stress
  const ruin = asRec(r?.ruin);
  const ruinBlock = Object.keys(ruin).length
    ? ruinDetail(ruin)
    : empty(
        "Rischio di rovina non ancora stimato.",
        ruinReason ?? "Serve una storia di prezzi reali sufficiente per il bootstrap a blocchi.",
      );
  const ruinNote = `<p class="muted" style="margin:10px 0 0">Stima Monte Carlo con bootstrap a blocchi sui
    rendimenti reali del Brent, con scenari sintetici di riapertura ed escalation iniettati e dichiarati. Il
    vincolo di calibrazione è una probabilità di rovina sotto il 5%.</p>`;

  const stress = asRec(r?.stress);
  const stressBlock = Object.keys(stress).length
    ? stressDetail(stress)
    : empty(
        "Stress test non ancora pubblicati.",
        stressReason ?? "Servono abbastanza prezzi reali per ricostruire gli episodi.",
      );
  const stressNote = `<p class="muted" style="margin:10px 0 0">Gli stress test applicano la posizione attuale,
    tenuta ferma e mai ridimensionata, alla Guerra del Golfo 1990-91, ad Abqaiq 2019, al Covid 2020,
    all'invasione russa del 2022 e a scenari sintetici di shock. Gli scenari sintetici sono etichettati come
    tali: sono ipotesi, non dati di mercato.</p>`;

  // ---------------------------------------------------------------- limiti rimasti
  const limits = pool.length
    ? card(
        "Altri limiti dichiarati",
        `<ul class="muted" style="margin:0;padding-left:18px;line-height:1.6">${pool
          .map((m) => `<li>${escapeHtml(m)}</li>`)
          .join("")}</ul>`,
        src(source, generatedAt),
      )
    : "";

  el.innerHTML =
    pageTitle("Rischio", generatedAt ? `Aggiornato ${relative(generatedAt)} · ${stamp(generatedAt)}` : undefined) +
    originBanner(risk, generatedAt) +
    tiles +
    card("Margini e liquidazione", marginBlock, src("engine/broker", acct.asof ?? generatedAt)) +
    card(
      "Storico della leva",
      chartBlock({
        id: "lev-chart",
        hasData: hasLev,
        emptyMessage: "Storico della leva non ancora disegnabile.",
        emptyHint: levReason ?? "Serve più di una rilevazione: il motore marca il conto ogni 30 minuti.",
        legendItems: [{ label: "Leva effettiva", color: cssVar(LEV_COLOR) }],
        source,
        asof: levAsof,
        size: "short",
      }) +
        levStatsBlock +
        levNote,
      levFlat ? `<span class="chip">ferma a ${fmtLev(levValues[0])}</span>` : "",
    ) +
    card("VaR ed Expected Shortfall", varBlock, src(source, generatedAt)) +
    card("Soglie automatiche e circuit breaker", breakersBlock, src("config/risk.yaml", generatedAt)) +
    card("Rischio di rovina", ruinBlock + ruinNote, src(source, generatedAt)) +
    card("Stress test", stressBlock + stressNote, src(source, generatedAt)) +
    limits;

  if (hasLev) {
    const chartEl = document.getElementById("lev-chart");
    const strip = document.getElementById("lev-chart-readout");
    if (chartEl) {
      // The axis is a multiplier, not a price, so it gets its own formatter ending in "x" instead of the
      // default 2-decimal price. A line, not an area: a flat 0,00x drawn as an area would fill half the box
      // and read as exposure that does not exist. The header chip says the series is flat.
      const chart = baseChart(chartEl, {
        priceFormatter: (v: number) => fmtLev(v),
        timeVisible: !spansMultipleDays(rawLev),
      });
      const series = addLine(chart, levData, cssVar(LEV_COLOR), { precision: 2 });
      if (strip) {
        attachReadout(chart, strip, [
          { api: series, label: "Leva effettiva", color: cssVar(LEV_COLOR), fmt: (v) => fmtLev(v) },
        ]);
      }
      fit(chart);
    }
  }
}

/** Takes the one sentence of `unavailable` that is about this block, so it cannot be printed twice. */
function takeReason(pool: string[], ...needles: string[]): string | undefined {
  const i = pool.findIndex((m) => needles.some((n) => m.toLowerCase().includes(n)));
  return i >= 0 ? pool.splice(i, 1)[0] : undefined;
}

/** The engine's vocabulary for the account state, as a chip. */
function accountStatusChip(status: string | null): string {
  const map: Record<string, { cls: string; text: string }> = {
    active: { cls: "good", text: "conto attivo" },
    halted_breaker: { cls: "warn", text: "fermo: circuit breaker" },
    halted_stale: { cls: "warn", text: "fermo: dati stantii" },
    dead: { cls: "bad", text: "conto azzerato" },
  };
  const hit = status ? map[status] : undefined;
  if (!hit) return `<span class="chip">stato ${escapeHtml(status ?? EMPTY)}</span>`;
  return `<span class="chip ${hit.cls}">${hit.text}</span>`;
}

/** Monte Carlo ruin. The headline is the two probabilities; the parameters say what was simulated. */
function ruinDetail(ruin: Record<string, unknown>): string {
  const labels = asRec(ruin["labels"]);
  const probRuin = asNum(ruin["prob_ruin"]);
  const probDead = asNum(ruin["prob_dead"]);
  const budget = asNum(ruin["max_ruin_probability"]);
  const within = ruin["within_budget"];
  const strip = `<div class="strip" style="margin-bottom:12px">
    ${stat("Probabilità di rovina", percent(probRuin, 2), lossTone(probRuin))}
    ${stat("Probabilità conto azzerato", percent(probDead, 2), lossTone(probDead))}
    ${stat("Equity finale mediana", withUnit(money(asNum(ruin["median_terminal"]))))}
    ${stat("5° percentile", withUnit(money(asNum(ruin["p05_terminal"]))))}
    ${stat("95° percentile", withUnit(money(asNum(ruin["p95_terminal"]))))}
  </div>`;
  const verdict =
    within === true
      ? `<span class="chip good">entro il budget${budget === null ? "" : ` · ${percent(budget, 0)}`}</span>`
      : within === false
        ? `<span class="chip bad">oltre il budget${budget === null ? "" : ` · ${percent(budget, 0)}`}</span>`
        : "";
  const defs = [labelled(labels, "prob_ruin", ""), labelled(labels, "prob_dead", "")].filter(Boolean).join(" · ");

  const params = asRec(ruin["params"]);
  const paramBlock = `<dl class="kv">
    <dt>Percorsi simulati</dt><dd>${contracts(asNum(ruin["n_paths"]) ?? asNum(params["n_paths"]))}</dd>
    <dt>Orizzonte (sedute)</dt><dd>${contracts(asNum(ruin["horizon_days"]) ?? asNum(params["horizon_days"]))}</dd>
    <dt>Soglia di rovina (frazione del capitale)</dt><dd>${percent(asNum(ruin["ruin_threshold"]), 0)}</dd>
    <dt>${escapeHtml(labelled(labels, "geometric_growth", "Crescita geometrica (mediana)"))}</dt>
    <dd>${percent(asNum(ruin["geometric_growth"]), 2)}</dd>
    <dt>Leva media simulata</dt><dd>${fmtLev(asNum(ruin["leverage_mean"]))}</dd>
  </dl>`;

  const dd = asRec(ruin["max_drawdown"]);
  const ddBlock = Object.keys(dd).length
    ? `<h3 style="margin:16px 0 8px">${escapeHtml(labelled(labels, "max_drawdown", "Drawdown massimo per percorso"))}</h3>
       <div class="table-wrap"><table class="data">
         <thead><tr>${["mean", "median", "p95", "p99", "worst"]
           .filter((k) => k in dd)
           .map((k) => `<th class="num">${escapeHtml(ddLabel(k))}</th>`)
           .join("")}</tr></thead>
         <tbody><tr>${["mean", "median", "p95", "p99", "worst"]
           .filter((k) => k in dd)
           .map((k) => `<td class="num ${lossTone(asNum(dd[k]))}">${cell(percent(asNum(dd[k]), 1))}</td>`)
           .join("")}</tr></tbody>
       </table></div>`
    : "";

  const byScenario = asRec(ruin["by_scenario"]);
  const scenarioRows = Object.entries(byScenario).map(([id, raw]) => ({ id, row: asRec(raw) }));
  const scenarioBlock = scenarioRows.length
    ? `<h3 style="margin:16px 0 8px">${escapeHtml(labelled(labels, "by_scenario", "Dettaglio per scenario sintetico"))}</h3>
       <div class="table-wrap"><table class="data">
         <thead><tr>
           <th class="wrap-text">Scenario</th>
           <th class="num">Percorsi</th>
           <th class="num">Quota</th>
           <th class="num">Rovina</th>
           <th class="num">Azzerato</th>
           <th class="num">Equity mediana <span class="unit">$</span></th>
           <th class="num">Max DD mediano</th>
         </tr></thead>
         <tbody>${scenarioRows
           .map(({ id, row }) => {
             const pr = asNum(row["prob_ruin"]);
             const pd = asNum(row["prob_dead"]);
             const mdd = asNum(row["median_max_drawdown"]);
             return `<tr>
               <td class="wrap-text">${escapeHtml(asStr(row["label"]) ?? id)}</td>
               <td class="num">${cell(contracts(asNum(row["n_paths"])))}</td>
               <td class="num">${cell(percent(asNum(row["share"]), 1))}</td>
               <td class="num ${lossTone(pr)}">${cell(percent(pr, 2))}</td>
               <td class="num ${lossTone(pd)}">${cell(percent(pd, 2))}</td>
               <td class="num">${cell(num(asNum(row["median_terminal"]), 0))}</td>
               <td class="num ${lossTone(mdd)}">${cell(percent(mdd, 1))}</td>
             </tr>`;
           })
           .join("")}</tbody>
       </table></div>`
    : "";

  // Anything the engine adds later still reaches the page instead of disappearing silently.
  const known = new Set([
    "labels",
    "params",
    "scenarios",
    "synthetic_scenarios",
    "by_scenario",
    "max_drawdown",
    "prob_ruin",
    "prob_dead",
    "max_ruin_probability",
    "within_budget",
    "median_terminal",
    "p05_terminal",
    "p95_terminal",
    "geometric_growth",
    "leverage_mean",
    "n_paths",
    "horizon_days",
    "ruin_threshold",
    "initial_capital",
    "dead_fraction",
  ]);
  const extra = Object.entries(ruin).filter(
    ([k, v]) => !known.has(k) && (typeof v === "number" || typeof v === "string" || typeof v === "boolean"),
  );
  const extraBlock = extra.length
    ? `<details style="margin-top:12px"><summary>Altri valori della simulazione</summary>
        <dl class="kv" style="margin-top:10px">${extra
          .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(plain(v))}</dd>`)
          .join("")}</dl></details>`
    : "";

  const note = labelled(labels, "note", "");
  return (
    strip +
    (verdict || defs
      ? `<p class="muted" style="margin:0 0 12px">${verdict}${verdict && defs ? " " : ""}${escapeHtml(defs)}</p>`
      : "") +
    paramBlock +
    ddBlock +
    scenarioBlock +
    extraBlock +
    (note ? `<p class="muted" style="margin:12px 0 0">${escapeHtml(note)}</p>` : "")
  );
}

function ddLabel(key: string): string {
  const map: Record<string, string> = {
    mean: "Medio",
    median: "Mediano",
    p95: "p95",
    p99: "p99",
    worst: "Peggiore",
  };
  return map[key] ?? key;
}

/** Historical analogs plus synthetic scenarios, one table. An episode without data keeps its row and says
 *  why in the note column: a colspan row used to break the alignment of every figure below it. */
function stressDetail(stress: Record<string, unknown>): string {
  const labels = asRec(stress["labels"]);
  const position = asRec(stress["position"]);
  const episodes = asRows(stress["episodes"]);
  const scenarios = asRows(stress["scenarios"]);
  const all = [...episodes, ...scenarios];

  const refPrice = asNum(stress["reference_price"]);
  const posLev = asNum(position["leverage"]);
  const posDir = asNum(position["direction"]);
  const header = `<div class="strip" style="margin-bottom:12px">
      ${stat("Leva testata", fmtLev(posLev))}
      ${stat("Direzione", posDir === null ? DASH : posDir >= 0 ? "Long" : "Short")}
      ${stat("Prezzo di riferimento", withUnit(price(refPrice)))}
      ${stat("Analoghi con dati", contracts(asNum(stress["n_available"])))}
      ${stat("Analoghi senza dati", contracts(asNum(stress["n_unavailable"])))}
    </div>
    <p class="muted" style="margin:0 0 12px">
      ${escapeHtml(labelled(labels, "position", asStr(position["label"]) ?? ""))}
      ${src(asStr(stress["source"]), asStr(stress["asof"]))}
    </p>`;

  const worst = asRec(stress["worst_episode"]);
  const worstReturn = asNum(worst["total_return"]);
  const worstLine = Object.keys(worst).length
    ? `<p class="muted" style="margin:0 0 12px">Analogo peggiore: <b>${escapeHtml(
        asStr(worst["name"]) ?? asStr(worst["id"]) ?? EMPTY,
      )}</b>, rendimento <span class="num ${tone(worstReturn)}">${percent(worstReturn, 1)}</span>.</p>`
    : "";

  if (!all.length) {
    return (
      header +
      worstLine +
      empty("Nessuno scenario di stress disponibile.", "Il job settimanale li ricostruisce dai prezzi reali.")
    );
  }

  const rows = all
    .map((e) => {
      const name = asStr(e["name"]) ?? asStr(e["label"]) ?? asStr(e["id"]) ?? DASH;
      const unavailable = e["available"] === false;
      const total = asNum(e["total_return"]);
      const maxDd = asNum(e["max_drawdown"]);
      const worstDay = asNum(e["worst_day"]);
      const minLevel = asNum(e["min_margin_level"]);
      const note = [asStr(e["reason"]), asStr(e["note"])].filter(Boolean).join(" · ");
      const tags = [
        e["synthetic"] ? '<span class="chip warn">sintetico</span>' : "",
        unavailable ? '<span class="chip">non disponibile</span>' : "",
      ]
        .filter(Boolean)
        .join(" ");
      return `<tr>
        <td>${escapeHtml(name)}${tags ? ` ${tags}` : ""}</td>
        <td class="num ${tone(total)}">${cell(percent(total, 1))}</td>
        <td class="num ${lossTone(maxDd)}">${cell(percent(maxDd, 1))}</td>
        <td class="num ${tone(worstDay)}">${cell(percent(worstDay, 1))}</td>
        <td class="num">${cell(num(minLevel, 2))}</td>
        <td>${flagChip(e["margin_call"])}</td>
        <td>${flagChip(e["liquidated"])}</td>
        <td class="wrap-text">${escapeHtml(note || DASH)}</td>
      </tr>`;
    })
    .join("");

  return `${header}${worstLine}<div class="table-wrap tall"><table class="data">
    <thead><tr>
      <th>Scenario</th>
      <th class="num">Rendimento</th>
      <th class="num">Max DD</th>
      <th class="num">Peggior seduta</th>
      <th class="num">Margine minimo</th>
      <th>Margin call</th>
      <th>Liquidazione</th>
      <th class="wrap-text">Nota</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

/** A value the page has no named formatter for: an integer keeps no decimals, anything else gets four. */
function plain(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? contracts(v) : num(v, 4);
  if (typeof v === "boolean") return v ? "sì" : "no";
  return String(v);
}
