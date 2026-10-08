/** The backtest page: what the three linear books would have done, how fragile that is, and what was rejected.
 *
 * Reads `desk_backtest.json`, the payload the weekly job writes. Everything a reader needs to distrust a
 * backtest is on the page: the t-statistic next to every Sharpe ratio, each calendar year, the same replay at
 * double cost and with the optimistic fill, every component on its own, where each day's return came from.
 */
import { lineChart, lineLegend, type LineSeries } from "../charts/lines";
import { repoUrl } from "../config";
import { load } from "../data";
import { bookColor, type BacktestDoc, type CurvePoint, type Stats } from "../desk";
import { EMPTY, escapeHtml, leverage, num, percent, relative, signedPercent, tone } from "../format";

const span = (cls: string, text: string): string => `<span class="${cls}">${text}</span>`;

function section(title: string, body: string, note = ""): string {
  return `<section class="t-sec">
    <h2 class="t-h"><span>${escapeHtml(title)}</span>${note ? `<small>${note}</small>` : ""}</h2>
    ${body}
  </section>`;
}

function points(curve: CurvePoint[]): { t: number; v: number }[] {
  return curve.map((p) => ({ t: Date.parse(`${p.t}T12:00:00Z`), v: p.v })).filter((p) => Number.isFinite(p.t) && p.v > 0);
}

const sharpeCell = (s: Stats | undefined): string =>
  s ? `${num(s.sharpe ?? null, 2)}<small>t ${num(s.t_stat ?? null, 1)}</small>` : EMPTY;

function summary(doc: BacktestDoc, names: Map<string, string>): string {
  const rows = Object.entries(doc.books)
    .map(([id, b]) => {
      const s = b.stats;
      return `<tr>
        <th scope="row"><span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}<small>${escapeHtml(
          b.vehicle,
        )} · ${escapeHtml(b.start.slice(0, 4))}–${escapeHtml(b.end.slice(0, 4))}</small></th>
        <td class="n">${signedPercent(s.cagr ?? null, 1)}</td>
        <td class="n">${percent(s.vol ?? null, 0)}</td>
        <td class="n">${sharpeCell(s)}</td>
        <td class="n down">${percent(s.max_drawdown ?? null, 0)}<small>${num((s.longest_drawdown_days ?? 0) / 252, 1)} anni sotto</small></td>
        <td class="n down">${percent(s.worst_day ?? null, 1)}<small>sett. ${percent(s.worst_week ?? null, 0)}</small></td>
        <td class="n">${leverage(s.leverage_median ?? null)}<small>95° ${leverage(s.leverage_p95 ?? null)} · max ${leverage(
          s.leverage_max ?? null,
        )}</small></td>
        <td class="n">${percent(s.share_days_invested ?? null, 0)}<small>short ${percent(s.share_days_short ?? null, 0)}</small></td>
        <td class="n">${percent(b.costs.per_year_of_mean_equity ?? null, 2)}<small>${b.n_fills} eseguiti</small></td>
        <td class="n">×${num(s.final_multiple ?? null, 2)}${b.died ? `<small class="down">azzerato il ${escapeHtml(b.died)}</small>` : ""}</td>
      </tr>`;
    })
    .join("");
  const bench = Object.entries(doc.benchmarks)
    .map(([id, b]) => {
      const s = b.stats;
      return `<tr class="t-bench"><th scope="row">${escapeHtml(id)} comprato e tenuto<small>senza leva, senza costi</small></th>
        <td class="n">${signedPercent(s.cagr ?? null, 1)}</td><td class="n">${percent(s.vol ?? null, 0)}</td>
        <td class="n">${sharpeCell(s)}</td>
        <td class="n down">${percent(s.max_drawdown ?? null, 0)}<small>${num((s.longest_drawdown_days ?? 0) / 252, 1)} anni sotto</small></td>
        <td class="n down">${percent(s.worst_day ?? null, 1)}<small>sett. ${percent(s.worst_week ?? null, 0)}</small></td>
        <td class="n">1,00x</td><td class="n">100%</td><td></td><td class="n">×${num(s.final_multiple ?? null, 2)}</td></tr>`;
    })
    .join("");
  return section(
    "Risultato",
    `<div class="t-scroll"><table class="t-table t-backtest">
      <thead><tr><th>Libro</th><th class="n">Rend. annuo</th><th class="n">Volatilità</th><th class="n">Sharpe</th>
        <th class="n">Perdita max</th><th class="n">Giorno peggiore</th><th class="n">Leva mediana</th>
        <th class="n">Giorni investito</th><th class="n">Costi/anno</th><th class="n">Capitale</th></tr></thead>
      <tbody>${rows}${bench}</tbody></table></div>
      <p class="t-dim">${escapeHtml(doc.meta.fill_rule)}; capitale iniziale ${num(doc.meta.initial_capital, 0)} $ per libro.
      «t» è lo Sharpe per la radice degli anni: sotto 2 il risultato non si distingue con sicurezza dal caso.</p>`,
    `calcolato ${escapeHtml(relative(doc.meta.generated_at))}`,
  );
}

function ruin(doc: BacktestDoc, names: Map<string, string>): string {
  const rows = Object.entries(doc.books)
    .map(([id, b]) => {
      const r = b.ruin ?? {};
      return `<tr><th scope="row"><span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}</th>
        <td class="n">${percent(r.p_lose_quarter ?? null, 1)}</td>
        <td class="n">${percent(r.p_lose_half ?? null, 1)}</td>
        <td class="n">${percent(r.p_dead ?? null, 1)}</td>
        <td class="n">×${num(r.p5_final ?? null, 2)}</td><td class="n">×${num(r.median_final ?? null, 2)}</td>
        <td class="n">×${num(r.p95_final ?? null, 2)}</td></tr>`;
    })
    .join("");
  return section(
    "Rischio di rovina in un anno",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Libro</th><th class="n">P(perdere un quarto)</th><th class="n">P(perdere metà)</th>
        <th class="n">P(azzerare)</th><th class="n">5° percentile</th><th class="n">Mediana</th><th class="n">95° percentile</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="t-dim">Bootstrap a blocchi sui rendimenti giornalieri del libro stesso, 4 000 percorsi di un anno. I percorsi non
      possono contenere un giorno peggiore del peggiore già visto: per un libro a leva questo numero è un minimo del rischio,
      non un massimo.</p>`,
  );
}

function yearly(doc: BacktestDoc, names: Map<string, string>): string {
  const cols: { id: string; label: string; data: Record<string, number> }[] = [
    ...Object.entries(doc.books).map(([id, b]) => ({ id, label: names.get(id) ?? id, data: b.yearly })),
    ...Object.entries(doc.benchmarks).map(([id, b]) => ({ id: `bh-${id}`, label: `${id} tenuto`, data: b.yearly })),
  ];
  const years = [...new Set(cols.flatMap((c) => Object.keys(c.data)))].sort().reverse();
  const rows = years
    .map(
      (y) =>
        `<tr><th scope="row">${escapeHtml(y)}</th>${cols
          .map((c) => {
            const v = c.data[y];
            return `<td class="n">${v === undefined ? "" : span(tone(v), signedPercent(v, 1))}</td>`;
          })
          .join("")}</tr>`,
    )
    .join("");
  return section(
    "Anno per anno",
    `<div class="t-scroll t-tall"><table class="t-table t-years">
      <thead><tr><th>Anno</th>${cols.map((c) => `<th class="n">${escapeHtml(c.label)}</th>`).join("")}</tr></thead>
      <tbody>${rows}</tbody></table></div>`,
    "tre anni negativi di fila si vedono qui, non in una media",
  );
}

function sensitivity(doc: BacktestDoc, names: Map<string, string>): string {
  const rows = Object.entries(doc.books)
    .map(([id, b]) => {
      const dc = doc.double_cost[id]?.stats;
      const sc = doc.same_close[id]?.stats;
      return `<tr><th scope="row"><span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}</th>
        <td class="n">${sharpeCell(b.stats)}</td>
        <td class="n">${sharpeCell(dc)}</td>
        <td class="n">${sharpeCell(sc)}</td>
        <td class="n">${signedPercent(b.stats.cagr ?? null, 1)}</td>
        <td class="n">${signedPercent(dc?.cagr ?? null, 1)}</td>
        <td class="n">${signedPercent(sc?.cagr ?? null, 1)}</td></tr>`;
    })
    .join("");
  return section(
    "Quanto dipende da costi ed esecuzione",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Libro</th><th class="n">Sharpe</th><th class="n">a costi doppi</th><th class="n">eseguito alla chiusura</th>
        <th class="n">Rend. annuo</th><th class="n">a costi doppi</th><th class="n">eseguito alla chiusura</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="t-dim">Dal vivo un libro decide nell'ultima ora di seduta e si esegue sulla barra successiva: il vero
      risultato sta fra «apertura del giorno dopo» (la colonna principale, la più severa per i fondi) e «stessa chiusura».</p>`,
  );
}

function sleeves(doc: BacktestDoc): string {
  const blocks = Object.entries(doc.sleeves)
    .map(([vehicle, block]) => {
      const rows = block.rows
        .map(
          (r) => `<tr><th scope="row">${escapeHtml(r.name)}</th>
            <td class="n">${sharpeCell(r.stats)}</td>
            <td class="n">${sharpeCell(r.stats_same_close)}</td>
            <td class="n">${signedPercent(r.stats.cagr ?? null, 1)}</td>
            <td class="n down">${percent(r.stats.max_drawdown ?? null, 0)}</td>
            <td class="n">${percent(r.stats.share_days_invested ?? null, 0)}</td></tr>`,
        )
        .join("");
      return `<h3 class="t-sub">${escapeHtml(vehicle)} · ${block.long_only ? "solo long" : "long e short"} · obiettivo di volatilità ${percent(
        block.vol_target,
        0,
      )}</h3>
      <div class="t-scroll"><table class="t-table">
        <thead><tr><th>Componente</th><th class="n">Sharpe</th><th class="n">eseguito alla chiusura</th><th class="n">Rend. annuo</th>
          <th class="n">Perdita max</th><th class="n">Giorni investito</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
    })
    .join("");
  return section(
    "Le tre componenti, una alla volta",
    `${blocks}<p class="t-dim">Conto di riferimento mille volte più grande, per togliere l'effetto del lotto minimo. Nessuna
    componente è stata scelta guardando questo risultato: parametri pubblicati, pesi uguali.</p>`,
  );
}

function optionsReplay(doc: BacktestDoc): string {
  const o = doc.options;
  if (!o || !o.available || !o.cases) {
    return section("≈ Opzioni", `<div class="t-empty">Simulazione non disponibile${o?.reason ? `: ${escapeHtml(o.reason)}` : ""}.</div>`);
  }
  const SMILE: Record<string, string> = {
    piatto: "volatilità uguale su tutti gli strike",
    ottobre_2026: "smile letto sulla catena dell'8 ottobre 2026",
    put_ripido: "put lontane più care (forma abituale)",
  };
  const rows = o.cases
    .map(
      (c) => `<tr><th scope="row">${escapeHtml(SMILE[c.smile] ?? c.smile)}<small>costi ×${num(c.cost_multiplier, 0)}</small></th>
        <td class="n">${signedPercent(c.stats.cagr ?? null, 1)}</td>
        <td class="n">${percent(c.stats.vol ?? null, 1)}</td>
        <td class="n">${sharpeCell(c.stats)}</td>
        <td class="n down">${percent(c.stats.max_drawdown ?? null, 0)}</td>
        <td class="n">${num(c.stats.trades_per_year ?? null, 1)}</td>
        <td class="n">${percent(c.stats.win_rate ?? null, 0)}</td>
        <td class="n">${num(c.stats.avg_r ?? null, 3)}</td></tr>`,
    )
    .join("");
  return section(
    "≈ Opzioni: simulazione su modello",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Ipotesi</th><th class="n">Rend. annuo</th><th class="n">Volatilità</th><th class="n">Sharpe</th>
        <th class="n">Perdita max</th><th class="n">Operazioni/anno</th><th class="n">Chiuse in utile</th>
        <th class="n">Risultato medio / rischio</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="t-dim">${escapeHtml(o.method ?? "")} Non esiste uno storico gratuito di quotazioni di opzioni: questi numeri
      dicono l'ordine di grandezza, non il secondo decimale. Nove operazioni su dieci chiudono in utile e una ogni tanto perde
      dieci volte tanto: è la forma di questa strategia, non un difetto del conto. Il libro di carta esiste per sostituire il
      modello con i prezzi veri.</p>`,
    "approssimazione dichiarata",
  );
}

function provenance(doc: BacktestDoc): string {
  const LABEL: Record<string, string> = {
    fondo: "prezzo del fondo",
    contratto: "contratto detenuto (esatto)",
    eia: "regolamenti EIA dei contratti 1 e 2 (esatto)",
    front: "primo contratto, nei giorni in cui è quello detenuto",
    "fondo (approx)": "≈ fondo USO nei giorni di roll",
  };
  const rows = Object.entries(doc.data)
    .map(([vehicle, d]) => {
      const total = Object.values(d.return_sources).reduce((a, b) => a + b, 0) || 1;
      const mix = Object.entries(d.return_sources)
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `${escapeHtml(LABEL[k] ?? k)} ${percent(v / total, 1)}`)
        .join(" · ");
      return `<tr><th scope="row">${escapeHtml(vehicle)}</th><td class="t-nowrap">${escapeHtml(d.start)} → ${escapeHtml(d.end)}</td>
        <td class="n">${num(d.days, 0)}</td><td class="w">${mix}</td>
        <td class="n">${percent(d.slope_approx_share, 0)}</td></tr>`;
    })
    .join("");
  return section(
    "Da dove vengono i dati",
    `<div class="t-scroll"><table class="t-table">
      <thead><tr><th>Strumento</th><th>Periodo</th><th class="n">Sedute</th><th>Rendimento giornaliero</th>
        <th class="n">≈ curva per procura</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="t-dim">Per BNO la curva del Brent per contratto esiste solo da quando il motore la archivia: prima, la pendenza
      è quella del WTI (contratti 2 e 4, EIA) ed è marcata come approssimazione.</p>`,
  );
}

function rejected(): string {
  const rows: [string, string][] = [
    ["Momentum infragiornaliero (rottura dell'area di rumore) su BNO, USO, UCO, XLE", "Sharpe lordo fra −1,5 e −2,1 sulle barre orarie"],
    ["Prima e ultima ora, seguito del mercoledì EIA, finestra di regolamento", "nessun effetto distinguibile dal caso (|t| sotto 1,7)"],
    ["Ritorno alla media dello spread BNO–USO", "Sharpe fra −0,2 e −0,5 dopo i costi"],
    ["Vendere insieme UCO e SCO per incassare il decadimento", "0,96 sull'intero campione ma −0,36 dal 2022: non regge"],
    ["Comprare call e put insieme (straddle) ogni mese", "perde: le opzioni costano più della mossa che segue, t oltre −2,5"],
    ["Vendere premio con condor o farfalle coperte", "a zero o sotto, una volta pagate quattro gambe e lo smile"],
  ];
  return section(
    "Provato e scartato",
    `<div class="t-scroll"><table class="t-table t-log"><tbody>${rows
      .map(([a, b]) => `<tr><td class="w">${escapeHtml(a)}</td><td class="w t-dim">${escapeHtml(b)}</td></tr>`)
      .join("")}</tbody></table></div>
    <p class="t-dim">Misure dell'ottobre 2026 su dati reali. <a href="${repoUrl()}/blob/main/docs/RESEARCH.md" target="_blank"
    rel="noopener">Metodo, numeri e fonti →</a></p>`,
    "ciò che non ha retto resta fuori dai libri",
  );
}

export async function renderBacktest(el: HTMLElement): Promise<void> {
  const loaded = await load<BacktestDoc>("desk_backtest.json");
  const doc = loaded.data;
  if (!doc || !doc.books) {
    el.innerHTML = `<div class="term"><div class="t-empty">Backtest non ancora pubblicato: lo calcola il primo giro che ha i dati,
      poi il lavoro settimanale (o <code>python -m engine.cli desk-backtest</code>).</div></div>`;
    return;
  }
  const names = new Map(doc.books_config.map((b) => [b.id, b.name]));
  const vehicles = [...new Set(Object.values(doc.books).map((b) => b.vehicle))];
  const charts = vehicles
    .map(
      (v) => `<h3 class="t-sub">${escapeHtml(v)}: capitale da ${num(doc.meta.initial_capital, 0)} $, scala logaritmica</h3>
        <div class="t-chart" id="bt-${escapeHtml(v)}"></div><div id="bt-${escapeHtml(v)}-legend"></div>`,
    )
    .join("");
  el.innerHTML = `<div class="term">
    ${summary(doc, names)}
    ${section("Curve", charts, "ogni libro contro il proprio strumento comprato e tenuto")}
    ${ruin(doc, names)}
    ${sensitivity(doc, names)}
    ${sleeves(doc)}
    ${yearly(doc, names)}
    ${optionsReplay(doc)}
    ${provenance(doc)}
    ${rejected()}
  </div>`;
  for (const v of vehicles) {
    const target = document.getElementById(`bt-${v}`);
    const legend = document.getElementById(`bt-${v}-legend`);
    if (!target || !legend) continue;
    const series: LineSeries[] = Object.entries(doc.books)
      .filter(([, b]) => b.vehicle === v)
      .map(([id, b]) => ({ id, label: names.get(id) ?? id, color: bookColor(id), points: points(b.curve) }));
    const bench = doc.benchmarks[v];
    if (bench) {
      series.push({ id: `bh-${v}`, label: `${v} comprato e tenuto`, color: "var(--text-muted)", points: points(bench.curve), dashed: true });
    }
    const yearFmt = (t: number): string => String(new Date(t).getUTCFullYear());
    lineChart(target, series, {
      ariaLabel: `Capitale dei libri su ${v} contro lo strumento comprato e tenuto`,
      height: 240,
      log: true,
      fmt: (x) => num(x, 0),
      dateFmt: yearFmt,
    });
    legend.innerHTML = lineLegend(series, (x) => `${num(x, 0)} $`);
  }
}

