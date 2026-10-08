/** The terminal: one screen of text for the desk's four paper books.
 *
 * It answers, top to bottom, the questions somebody opening the page actually has: what is oil doing, what
 * does the model think, what does each book hold and why, what happened lately, is the machine alive. No
 * number is computed here: the page formats what `desk.json` carries and says "n/d" for what it does not.
 */
import { lineChart, lineLegend, type LineSeries } from "../charts/lines";
import { repoUrl } from "../config";
import { load } from "../data";
import { bookColor, type Book, type DeskDoc, type ForecastBlock, type OptionUnderlying, type Quote } from "../desk";
import { EMPTY, escapeHtml, leverage, num, percent, relative, signedPercent, signedUsd, tone, usd } from "../format";

const MINUS = "−";

/** dd/MM HH:mm in the reader's own time zone (the header says which). */
function clock(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("it-IT", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(t);
}

function zoneName(): string {
  try {
    const part = new Intl.DateTimeFormat("it-IT", { timeZoneName: "short" })
      .formatToParts(new Date())
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? "ora locale";
  } catch {
    return "ora locale";
  }
}

function day(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso.length <= 10 ? `${iso}T12:00:00Z` : iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "short", timeZone: "UTC" }).format(t);
}

function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const s = num(Math.abs(value), digits);
  return value > 0 ? `+${s}` : value < 0 ? `${MINUS}${s}` : s;
}

const span = (cls: string, text: string): string => `<span class="${cls}">${text}</span>`;
const toneSpan = (value: number | null | undefined, text: string): string => span(tone(value), text);

function section(title: string, body: string, note = ""): string {
  return `<section class="t-sec">
    <h2 class="t-h"><span>${escapeHtml(title)}</span>${note ? `<small>${note}</small>` : ""}</h2>
    ${body}
  </section>`;
}

// ---------------------------------------------------------------------------------------------- header
function engineState(doc: DeskDoc): { cls: string; text: string } {
  const ageMin = (Date.now() - Date.parse(doc.generated_at)) / 60000;
  if (!Number.isFinite(ageMin)) return { cls: "warn", text: "stato del motore n/d" };
  const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const dow = ny.getDay();
  const hm = ny.getHours() * 60 + ny.getMinutes();
  const weekend = dow === 6 || (dow === 5 && hm >= 18 * 60 + 30) || (dow === 0 && hm < 17 * 60 + 30);
  if (ageMin <= 50) return { cls: "ok", text: "motore attivo" };
  if (weekend) return { cls: "dim", text: "mercati chiusi: riparte domenica 17:30 New York" };
  if (ageMin <= 180) return { cls: "warn", text: `motore in ritardo (ultimo giro ${relative(doc.generated_at)})` };
  return { cls: "bad", text: `motore fermo da ${relative(doc.generated_at).replace(" fa", "")}` };
}

function dataState(doc: DeskDoc): { cls: string; text: string } {
  const h = doc.health;
  if (h.overall === "green") return { cls: "ok", text: `dati ok (${h.n_green}/${h.n_sources})` };
  if (h.overall === "yellow") return { cls: "ok", text: `dati ok, ${h.n_sources - h.n_green} fonti secondarie parziali` };
  if (h.overall === "red") return { cls: "bad", text: "una fonte critica non risponde: nessuna nuova decisione" };
  return { cls: "dim", text: "stato dei dati n/d" };
}

function header(doc: DeskDoc, origin: string | null): string {
  const eng = engineState(doc);
  const dat = dataState(doc);
  const from = origin === "live" ? "" : origin === "bundled" ? " · copia inclusa nel sito" : origin === "sample" ? " · DATI DI ESEMPIO" : "";
  return `<div class="t-status">
    <span>aggiornato ${escapeHtml(clock(doc.generated_at))} ${escapeHtml(zoneName())} · ${escapeHtml(
      relative(doc.generated_at),
    )}${escapeHtml(from)}</span>
    <span class="t-dot ${eng.cls}">${escapeHtml(eng.text)}</span>
    <span class="t-dot ${dat.cls}">${escapeHtml(dat.text)}</span>
  </div>`;
}

// ---------------------------------------------------------------------------------------------- market
function quoteCell(q: Quote | undefined): string {
  if (!q) return "";
  const stale = q.asof ? (Date.now() - Date.parse(q.asof)) / 3_600_000 > 6 : true;
  return `<div class="t-quote">
    <div class="k">${escapeHtml(q.name)} <span class="t-dim">${escapeHtml(q.symbol)}</span></div>
    <div class="v">${q.price === null ? EMPTY : num(q.price, 2)} ${toneSpan(q.change, signedPercent(q.change, 1))}</div>
    <div class="s${stale ? " warn" : ""}">${q.asof ? escapeHtml(clock(q.asof)) : "nessun prezzo"}</div>
  </div>`;
}

function market(doc: DeskDoc): string {
  const m = doc.market;
  const fc = doc.forecast.BNO;
  const cells = [quoteCell(m.brent), quoteCell(m.wti), quoteCell(m.bno)];
  if (m.ovx) {
    cells.push(`<div class="t-quote">
      <div class="k">OVX <span class="t-dim">vol. implicita</span></div>
      <div class="v">${num(m.ovx.value, 1)}</div>
      <div class="s">${escapeHtml(day(m.ovx.asof))}</div>
    </div>`);
  }
  if (fc && fc.slope !== null) {
    const back = fc.slope > 0;
    cells.push(`<div class="t-quote">
      <div class="k">Curva Brent <span class="t-dim">${escapeHtml(fc.slope_pair)}${fc.slope_approx ? " ≈" : ""}</span></div>
      <div class="v">${signedPercent(fc.slope, 1)}<span class="t-dim">/anno</span></div>
      <div class="s">${back ? "backwardation: il vicino vale più del lontano" : "contango: il lontano vale più del vicino"}</div>
    </div>`);
  }
  if (m.hormuz) {
    const h = m.hormuz;
    cells.push(`<div class="t-quote">
      <div class="k">Hormuz <span class="t-dim">petroliere/giorno</span></div>
      <div class="v">${num(h.tankers_7d, 1)} ${span(h.closed ? "down" : "", `${percent(h.ratio, 0)} del normale`)}</div>
      <div class="s">media 7 giorni al ${escapeHtml(day(h.asof))} · norma ${num(h.baseline, 0)}</div>
    </div>`);
  }
  return section(
    "Mercato",
    `<div class="t-quotes">${cells.join("")}</div>`,
    "prezzi Yahoo in ritardo di 10-15 minuti · Hormuz: IMF PortWatch (AIS), settimanale",
  );
}

// ---------------------------------------------------------------------------------------------- forecast
function forecastTable(doc: DeskDoc): string {
  const cols: { key: string; title: string; f: ForecastBlock }[] = [];
  if (doc.forecast.BNO) cols.push({ key: "BNO", title: "Brent · BNO", f: doc.forecast.BNO });
  if (doc.forecast.MCL) cols.push({ key: "MCL", title: "WTI · /MCL", f: doc.forecast.MCL });
  if (!cols.length) return section("Previsione", `<div class="t-empty">Nessuna previsione: mancano le serie giornaliere.</div>`);
  const row = (label: string, hint: string, pick: (f: ForecastBlock) => string): string =>
    `<tr><th scope="row">${escapeHtml(label)}<small>${escapeHtml(hint)}</small></th>${cols
      .map((c) => `<td class="n">${pick(c.f)}</td>`)
      .join("")}</tr>`;
  const fc = (v: number | null): string => toneSpan(v, signed(v, 1));
  const body = `<div class="t-scroll"><table class="t-table t-forecast">
    <thead><tr><th></th>${cols.map((c) => `<th class="n">${escapeHtml(c.title)}</th>`).join("")}</tr></thead>
    <tbody>
      ${row("Trend", "quattro medie mobili, 8-32 fino a 64-256 giorni", (f) => fc(f.trend))}
      ${row("Carry", "pendenza della curva: +10 in backwardation", (f) => fc(f.carry))}
      ${row("Carry-momentum", "pendenza sopra o sotto la sua media a 20 giorni", (f) => fc(f.carry_momentum))}
      <tr class="t-total"><th scope="row">Previsione<small>scala da ${MINUS}20 a +20; +10 è una convinzione normale</small></th>${cols
        .map(
          (c) =>
            `<td class="n"><b>${fc(c.f.combined)}</b> <span class="t-dim">ieri ${signed(c.f.combined_prev, 1)}</span></td>`,
        )
        .join("")}</tr>
      ${row("Volatilità", "annua, ultimi due mesi circa", (f) => percent(f.vol, 0))}
      ${row("Dato del", "ultima seduta nel calcolo", (f) => `${escapeHtml(day(f.day))}${f.approx ? " ≈" : ""}`)}
    </tbody></table></div>`;
  return section("Previsione", body, "la stessa per tutti i libri su quello strumento: cambia solo quanta ne comprano");
}

// ---------------------------------------------------------------------------------------------- books
const STATUS: Record<string, { cls: string; text: string }> = {
  active: { cls: "ok", text: "attivo" },
  halted_breaker: { cls: "warn", text: "fermo fino a domani (perdita giornaliera)" },
  halted_stale: { cls: "warn", text: "fermo: dati non aggiornati" },
  dead: { cls: "bad", text: "azzerato: serve un reset" },
};

function positionText(b: Book): string {
  if (b.kind === "options") {
    const n = b.structures?.length ?? 0;
    if (!n) return span("t-dim", "nessuna struttura aperta");
    return `${n} spread di put · rischio massimo ${usd(b.max_loss_open ?? null, 0)} (${percent(b.max_loss_open_pct ?? null, 1)})`;
  }
  const parts: string[] = [];
  for (const p of b.positions ?? []) {
    parts.push(
      `${p.side} ${num(Math.abs(p.units), 0)} ${escapeHtml(p.unit)} ${escapeHtml(p.symbol)} a ${num(p.avg_price, 2)} ${toneSpan(
        p.unrealized,
        signedUsd(p.unrealized, 0),
      )}`,
    );
  }
  for (const o of b.pending ?? []) {
    parts.push(span("warn", `ordine in coda: ${signed(o.units, 0)} ${escapeHtml(o.symbol)}`));
  }
  return parts.length ? parts.join(" · ") : span("t-dim", "in contanti");
}

function capText(b: Book): string {
  if (b.kind === "options") return "rischio definito";
  const cap = b.cap_today ?? (b.rules.max_leverage as number | undefined) ?? null;
  return `${leverage(cap)}${b.cap_reduced_today ? " <span class='warn'>prima della chiusura</span>" : ""}`;
}

function booksTable(doc: DeskDoc): string {
  const rows = doc.books
    .map((b) => {
      const st = STATUS[b.status] ?? { cls: "dim", text: b.status };
      return `<tr>
        <th scope="row"><span class="t-key" style="--sw:${bookColor(b.id)}"></span>${escapeHtml(b.name)}${
          b.experimental ? ' <span class="t-dim">sperimentale</span>' : ""
        }<small>${escapeHtml(b.vehicle)}</small></th>
        <td class="n" data-l="Equity $"><b>${num(b.equity, 2)}</b></td>
        <td class="n" data-l="Oggi">${toneSpan(b.pnl_day, signedPercent(b.pnl_day_pct, 2))}</td>
        <td class="n" data-l="Da inizio">${toneSpan(b.pnl_total, signedPercent(b.pnl_total_pct, 2))}</td>
        <td class="n" data-l="Dal massimo">${b.drawdown ? `${MINUS}${percent(b.drawdown, 1)}` : "0%"}</td>
        <td class="n" data-l="Leva">${leverage(b.leverage)}</td>
        <td class="n" data-l="Tetto">${capText(b)}</td>
        <td class="w" data-l="Posizione">${positionText(b)}</td>
        <td class="s"><span class="t-dot ${st.cls}">${escapeHtml(st.text)}</span></td>
      </tr>`;
    })
    .join("");
  const t = doc.total;
  const body = `<div class="t-scroll"><table class="t-table t-books">
    <thead><tr>
      <th>Libro</th><th class="n">Equity $</th><th class="n">Oggi</th><th class="n">Da inizio</th>
      <th class="n">Dal massimo</th><th class="n">Leva</th><th class="n">Tetto</th><th>Posizione</th><th>Stato</th>
    </tr></thead>
    <tbody>${rows}</tbody>
    <tfoot><tr><th scope="row">Totale</th><td class="n" data-l="Equity $"><b>${num(t.equity, 2)}</b></td><td class="x"></td>
      <td class="n" data-l="Da inizio">${toneSpan(t.pnl, signedPercent(t.pnl_pct, 2))}</td><td colspan="5" class="t-dim w">quattro
      conti separati da ${num(doc.books[0]?.initial_capital ?? 10000, 0)} $ ciascuno: il totale è solo una somma</td></tr></tfoot>
  </table></div>
  <div class="t-chart" id="t-equity"></div><div id="t-equity-legend"></div>`;
  return section("Libri", body, "paper trading: nessun ordine reale, nessun broker collegato");
}

function ruleLine(b: Book): string {
  const r = b.rules;
  if (b.kind === "options") {
    const dte = (r.dte as number[] | undefined) ?? [];
    return `Vende uno spread di put solo con previsione ≥ +${num(r.forecast_threshold as number, 0)}; put venduta a delta ${num(
      r.short_delta as number,
      2,
    )}, scadenza a ${dte[1] ?? "30"} giorni circa; perdita massima ${percent(r.risk_per_structure as number, 0)} del conto per
    struttura, ${r.max_open ?? 2} strutture al massimo; gambe quotate entro il ${percent(r.max_leg_spread as number, 0)} del prezzo,
    eseguite a un quarto dello spread dal lato peggiore.`;
  }
  const weekend = r.weekend_max_leverage as number | null | undefined;
  return `Obiettivo di volatilità ${percent(r.vol_target as number, 0)} · tetto di leva ${leverage(r.max_leverage as number)}${
    weekend !== null && weekend !== undefined ? ` (${leverage(weekend)} prima di un fine settimana o di una festa)` : ""
  } · ${r.long_only ? "solo long" : "long e short"} · margine ${percent(r.margin_rate as number, 0)} · decisione alle ${escapeHtml(
    String(r.decision_time_ny ?? ""),
  )} di New York · stop giornaliero a ${MINUS}${percent(r.daily_loss_breaker as number, 0)}.`;
}

function bookDetails(doc: DeskDoc): string {
  return doc.books
    .map((b) => {
      const d = b.last_decision;
      const costs = b.costs
        ? `spread e slippage ${usd(b.costs.spread_and_slippage, 2)} · commissioni ${usd(b.costs.commission, 2)} · interessi ${usd(
            b.costs.financing,
            2,
          )}`
        : "";
      const liq =
        b.liquidation_price !== null && b.liquidation_price !== undefined
          ? `<p>Prezzo di liquidazione forzata: <b>${num(b.liquidation_price, 2)}</b> (il broker chiude quando il margine non basta più).</p>`
          : "";
      const structures = (b.structures ?? [])
        .map(
          (s) =>
            `<p>${escapeHtml(s.underlying)} put ${num(s.short.strike, 0)}/${num(s.long.strike, 0)} scadenza ${escapeHtml(
              day(s.expiry),
            )} × ${s.contracts}: incassati ${num(s.credit, 2)}, ora vale ${num(s.mark, 2)}${s.mark_stale ? " (prezzo fermo)" : ""};
            guadagno massimo ${usd(s.max_gain, 0)}, perdita massima ${usd(s.max_loss, 0)}, pareggio a ${num(s.breakeven, 2)}${
              s.awaiting_close ? " · scaduta, in attesa del prezzo di chiusura ufficiale" : ""
            }.</p>`,
        )
        .join("");
      return `<details class="t-details">
        <summary><span class="t-key" style="--sw:${bookColor(b.id)}"></span><b>${escapeHtml(b.name)}</b>
          <span class="t-dim">${escapeHtml(b.robinhood ?? b.vehicle)}</span></summary>
        <p>${escapeHtml(b.description)}</p>
        <p class="t-dim">${ruleLine(b)}</p>
        ${structures}
        ${d ? `<p><span class="t-dim">Ultima decisione, ${escapeHtml(clock(d.ts))}:</span> ${escapeHtml(d.rationale ?? "")}</p>` : ""}
        ${liq}
        ${b.last_roll ? `<p class="t-dim">Ultimo roll: ${escapeHtml(b.last_roll)}</p>` : ""}
        ${costs ? `<p class="t-dim">Costi pagati finora: ${costs}. Eseguiti: ${b.n_fills ?? 0}.</p>` : ""}
        ${b.note ? `<p class="t-dim">${escapeHtml(b.note)}</p>` : ""}
      </details>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------------------------- activity
function decisions(doc: DeskDoc): string {
  if (!doc.decisions.length) {
    return section("Decisioni", `<div class="t-empty">Nessuna decisione ancora: la prima arriva al primo giro dopo le 15:00 di New York.</div>`);
  }
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  const rows = doc.decisions
    .slice(0, 16)
    .map((d) => {
      const acted = Boolean(d.order_units);
      return `<tr>
        <td class="t-nowrap">${escapeHtml(clock(d.ts))}</td>
        <td class="t-nowrap"><span class="t-key" style="--sw:${bookColor(d.book)}"></span>${escapeHtml(names.get(d.book) ?? d.book)}</td>
        <td class="w">${acted ? "" : '<span class="t-dim">nessun ordine · </span>'}${escapeHtml(d.text ?? "")}</td>
      </tr>`;
    })
    .join("");
  return section(
    "Decisioni",
    `<div class="t-scroll"><table class="t-table t-log t-stack"><tbody>${rows}</tbody></table></div>`,
    "una al giorno per libro, scritta anche quando non segue nessun ordine",
  );
}

function fills(doc: DeskDoc): string {
  if (!doc.fills.length) {
    return section("Eseguiti", `<div class="t-empty">Nessun eseguito ancora. Un ordine si esegue sulla prima barra da 30 minuti che inizia dopo la decisione.</div>`);
  }
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  const REASON: Record<string, string> = {
    signal: "ingresso",
    rebalance: "ribilanciamento",
    roll: "roll",
    margin_call: "margine",
    liquidation: "liquidazione",
    circuit_breaker: "stop giornaliero",
    reset: "reset",
    apertura: "apertura",
    scadenza: "scadenza",
  };
  const rows = doc.fills
    .slice(0, 20)
    .map(
      (f) => `<tr>
        <td class="t-nowrap">${escapeHtml(clock(f.ts))}</td>
        <td class="t-nowrap"><span class="t-key" style="--sw:${bookColor(f.book)}"></span>${escapeHtml(names.get(f.book) ?? f.book)}</td>
        <td class="n t-nowrap">${toneSpan(f.units, signed(f.units, 0))} ${escapeHtml(f.unit)}</td>
        <td class="t-nowrap">${escapeHtml(f.symbol)}</td>
        <td class="n">${num(f.price, 2)}</td>
        <td class="n">${f.cost_bps === null ? "" : `${num(f.cost_bps, 1)} bp`}</td>
        <td>${escapeHtml(REASON[f.reason] ?? f.reason)}</td>
        <td class="n">${f.realized_pnl ? toneSpan(f.realized_pnl, signedUsd(f.realized_pnl, 2)) : ""}</td>
      </tr>`,
    )
    .join("");
  return section(
    "Eseguiti",
    `<div class="t-scroll"><table class="t-table t-log">
      <thead><tr><th>Quando</th><th>Libro</th><th class="n">Quantità</th><th>Strumento</th><th class="n">Prezzo</th>
      <th class="n">Costo</th><th>Motivo</th><th class="n">P&amp;L chiuso</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`,
    "prezzo dopo spread e slippage; il costo è la distanza dal prezzo di riferimento",
  );
}

// ---------------------------------------------------------------------------------------------- options
function optionRow(u: OptionUnderlying): string {
  const noChain = u.price === undefined || u.price === null;
  if (noChain) {
    return `<tr><th scope="row">${escapeHtml(u.symbol)}<small>${escapeHtml(u.label)}</small></th>
      <td colspan="7" class="t-dim">nessuna catena di opzioni archiviata</td></tr>`;
  }
  return `<tr>
    <th scope="row">${escapeHtml(u.symbol)}<small>${escapeHtml(u.label)}</small></th>
    <td class="n">${num(u.price, 2)}</td>
    <td class="n">${percent(u.atm_iv ?? u.iv30 ?? null, 0)}</td>
    <td class="n">${percent(u.realized_vol, 0)}</td>
    <td class="n">${u.straddle_pct === null || u.straddle_pct === undefined ? EMPTY : `±${percent(u.straddle_pct, 1)}`}<small>entro il ${escapeHtml(
      day(u.expiry),
    )}</small></td>
    <td class="n">${span(u.tradeable ? "" : "down", percent(u.median_rel_spread ?? null, 0))}<small>${
      u.tradeable ? "negoziabile" : "troppo largo"
    }</small></td>
    <td class="n">${toneSpan(u.forecast, signed(u.forecast, 1))}<small>soglia +${num(u.threshold, 0)}</small></td>
    <td class="w">${u.gate.open ? span("up", "condizioni soddisfatte") : escapeHtml(u.gate.reason)}</td>
  </tr>`;
}

function candidateLine(u: OptionUnderlying, open: boolean): string {
  const c = u.candidate;
  if (u.price === undefined || u.price === null) return ""; // no chain: the table row already says so
  if (!c) {
    return u.no_candidate_reason
      ? `<p class="t-dim">${escapeHtml(u.symbol)}: nessuna struttura negoziabile ${
          open ? "adesso" : "sulle ultime quotazioni della seduta"
        }. ${escapeHtml(u.no_candidate_reason)}.</p>`
      : "";
  }
  // after the close the chain holds the session's last quotes: nothing can be sold "now"
  const lead = open
    ? "la struttura che il libro venderebbe ora:"
    : "a mercato chiuso, sulle ultime quotazioni della seduta la struttura sarebbe:";
  return `<p><span class="t-dim">${escapeHtml(u.symbol)}, ${lead}</span> put ${num(
    c.short.strike,
    0,
  )}/${num(c.long.strike, 0)} scadenza ${escapeHtml(day(c.expiry))} (${c.dte} giorni) · incasso ${num(c.credit, 2)} $ per azione
    (${num(c.credit_mid, 2)} a metà prezzo) · perdita massima ${usd(c.max_loss_per_contract, 0)} · pareggio a ${num(c.breakeven, 2)},
    il ${percent(c.breakeven / c.underlying_price - 1, 1)} dal prezzo.</p>`;
}

function options(doc: DeskDoc): string {
  const book = doc.books.find((b) => b.kind === "options");
  const mon = book?.monitor;
  if (!book || !mon || !mon.underlyings.some((u) => u.price !== undefined && u.price !== null)) {
    return section(
      "Opzioni",
      `<div class="t-empty">Il libro delle opzioni non ha ancora letto una catena: le quotazioni si scaricano nelle ore di
      mercato di New York, e la decisione si prende una volta al giorno dopo le 15:00.</div>`,
    );
  }
  const body = `<div class="t-scroll"><table class="t-table t-options">
    <thead><tr><th>Sottostante</th><th class="n">Prezzo</th><th class="n">Vol. implicita</th><th class="n">Vol. realizzata</th>
      <th class="n">Mossa prezzata</th><th class="n">Denaro-lettera</th><th class="n">Previsione</th><th>Oggi</th></tr></thead>
    <tbody>${mon.underlyings.map(optionRow).join("")}</tbody></table></div>
    ${mon.underlyings.map((u) => candidateLine(u, mon.session_open !== false)).join("")}
    <p class="t-dim">«Mossa prezzata» è il costo di call più put alla pari: quanto deve muoversi il fondo, in su o in giù, perché
    comprare entrambe vada in pari. Dal 2007 quel prezzo è stato in media più alto della mossa che è seguita: comprare tutte e due
    le direzioni ha perso, e il libro non lo fa.</p>`;
  return section("Opzioni", body, "quotazioni Cboe in ritardo di 15 minuti · letto " + escapeHtml(clock(mon.generated_at)));
}

// ---------------------------------------------------------------------------------------------- backtest
function backtest(doc: DeskDoc): string {
  const bt = doc.backtest;
  if (!bt) {
    return section("Backtest", `<div class="t-empty">Il backtest viene calcolato al primo giro che ha i dati e poi ogni settimana: non è ancora stato pubblicato.</div>`);
  }
  const names = new Map(doc.books.map((b) => [b.id, b.name]));
  const rows = Object.entries(bt.books)
    .map(([id, b]) => {
      const s = b.stats;
      return `<tr>
        <th scope="row"><span class="t-key" style="--sw:${bookColor(id)}"></span>${escapeHtml(names.get(id) ?? id)}<small>${escapeHtml(
          b.vehicle,
        )} · ${num(s.years ?? null, 0)} anni</small></th>
        <td class="n">${signedPercent(s.cagr ?? null, 1)}</td>
        <td class="n">${percent(s.vol ?? null, 0)}</td>
        <td class="n">${num(s.sharpe ?? null, 2)}<small>t ${num(s.t_stat ?? null, 1)}</small></td>
        <td class="n down">${percent(s.max_drawdown ?? null, 0)}</td>
        <td class="n">${leverage(s.leverage_p95 ?? null)}<small>max ${leverage(s.leverage_max ?? null)}</small></td>
        <td class="n">${percent(b.ruin?.p_lose_half ?? null, 1)}</td>
        <td class="n">${num(b.double_cost_sharpe, 2)}</td>
      </tr>`;
    })
    .join("");
  const bench = Object.entries(bt.benchmarks)
    .map(([id, b]) => {
      const s = b.stats;
      return `<tr class="t-bench">
        <th scope="row">${escapeHtml(id)} comprato e tenuto<small>${num(s.years ?? null, 0)} anni, senza leva</small></th>
        <td class="n">${signedPercent(s.cagr ?? null, 1)}</td><td class="n">${percent(s.vol ?? null, 0)}</td>
        <td class="n">${num(s.sharpe ?? null, 2)}<small>t ${num(s.t_stat ?? null, 1)}</small></td>
        <td class="n down">${percent(s.max_drawdown ?? null, 0)}</td><td class="n">1,00x</td><td></td><td></td>
      </tr>`;
    })
    .join("");
  const opt = bt.options
    ? `<tr class="t-bench">
        <th scope="row">≈ Opzioni<small>modello, non storico di quotazioni</small></th>
        <td class="n">${signedPercent(bt.options.stats.cagr ?? null, 1)}</td><td class="n">${percent(bt.options.stats.vol ?? null, 0)}</td>
        <td class="n">${
          bt.options.sharpe_range ? `${num(bt.options.sharpe_range[0], 1)}–${num(bt.options.sharpe_range[1], 1)}` : EMPTY
        }<small>secondo smile e costi</small></td>
        <td class="n down">${percent(bt.options.stats.max_drawdown ?? null, 0)}</td><td></td><td></td><td></td>
      </tr>`
    : "";
  const body = `<div class="t-scroll"><table class="t-table t-backtest t-summary">
    <thead><tr><th>Libro</th><th class="n">Rend. annuo</th><th class="n">Volatilità</th><th class="n">Sharpe</th>
      <th class="n">Perdita max</th><th class="n">Leva 95°</th><th class="n">P(${MINUS}50% in 1 anno)</th>
      <th class="n">Sharpe a costi doppi</th></tr></thead>
    <tbody>${rows}${opt}${bench}</tbody></table></div>
    <p class="t-dim">Costi reali, ${escapeHtml(bt.fill_rule ?? "")}. Uno Sharpe di 0,4 su quindici anni dista un errore standard e mezzo
    da zero: è un indizio, non una certezza. <a href="#/backtest">Anno per anno, componenti e sensibilità →</a></p>`;
  return section("Backtest", body, `calcolato ${escapeHtml(relative(bt.generated_at))}`);
}

// ---------------------------------------------------------------------------------------------- health
function health(doc: DeskDoc): string {
  const h = doc.health;
  const bad = h.not_green
    .map(
      (s) => `<tr><td class="t-nowrap"><span class="t-dot ${s.status === "red" ? "bad" : "warn"}">${escapeHtml(s.source)}</span></td>
        <td class="w t-dim">${escapeHtml(s.message)}</td></tr>`,
    )
    .join("");
  const tick = h.last_tick
    ? `Ultimo giro ${escapeHtml(clock(h.last_tick.finished))}: ${escapeHtml(h.last_tick.message)}. `
    : "Nessun giro registrato. ";
  const notes = [...doc.data_notes, ...doc.data_missing.map((m) => `tabella mancante: ${m}`)];
  const body = `<p>${tick}${h.ticks_24h === 1 ? "Un giro" : `${h.ticks_24h} giri`} nelle ultime 24 ore${
    h.failed_24h ? `, ${h.failed_24h} falliti` : ""
  }. Il motore gira ogni 30 minuti da domenica sera a venerdì sera (ora di New York).</p>
    ${bad ? `<div class="t-scroll"><table class="t-table t-log t-stack"><tbody>${bad}</tbody></table></div>` : ""}
    ${notes.length ? `<p class="warn">${escapeHtml(notes.join(" · "))}</p>` : ""}
    <p class="t-dim">Solo i prezzi giornalieri di BNO e del WTI possono fermare le nuove decisioni dei libri. Le altre
    fonti, se mancano, spengono ciò che le legge e nient'altro.</p>`;
  return section("Stato", body, `${h.n_green} fonti su ${h.n_sources} a posto`);
}

function howTo(): string {
  return `<details class="t-details">
    <summary><b>Come si legge</b> <span class="t-dim">regole, costi, limiti</span></summary>
    <p><b>Una previsione, quattro libri.</b> Trend, carry e carry-momentum danno un numero fra ${MINUS}20 e +20. Ogni libro
    lineare compra <code>previsione / 10 × obiettivo di volatilità / volatilità di oggi</code> volte il proprio capitale, fino al
    suo tetto. La leva non è scelta: esce da quel rapporto. Quando il greggio è molto volatile anche il libro da 10x resta
    intorno a 1x; la leva sale solo quando il mercato si calma e la previsione è forte. Per non pagare costi a ogni
    ritocco c'è una <b>fascia di inerzia</b>: finché la misura voluta resta entro il 10% di una posizione normale non si
    tocca nulla, e quando ne esce si va al bordo della fascia, non al centro.</p>
    <p><b>Strumenti che esistono su Robinhood.</b> Il Brent si compra con il fondo BNO, senza leva o fino a 2x a margine. La
    leva vera passa dal future micro sul WTI (/MCL, 100 barili), perché lì un future sul Brent non c'è: il libro «spinto»
    porta quindi anche il rischio che Brent e WTI si muovano diversamente.</p>
    <p><b>Esecuzione.</b> Un ordine si esegue all'apertura della prima barra da 30 minuti che inizia dopo la decisione, con lo
    spread e le commissioni reali dello strumento. Mai al prezzo che ha generato il segnale.</p>
    <p><b>Che cosa non c'è.</b> Nessuna strategia intraday: sulle barre orarie di BNO, USO e dei contratti il momentum
    infragiornaliero non ha mostrato margine dopo i costi, e qui si tiene solo ciò che ha retto alla prova.
    <a href="${repoUrl()}/blob/main/docs/RESEARCH.md" target="_blank" rel="noopener">La ricerca, con i numeri e le fonti →</a></p>
  </details>`;
}

// ---------------------------------------------------------------------------------------------- page
function equitySeries(doc: DeskDoc): LineSeries[] {
  return doc.books.map((b) => ({
    id: b.id,
    label: b.name,
    color: bookColor(b.id),
    points: b.curve.map((p) => ({ t: Date.parse(p.t), v: p.v })).filter((p) => Number.isFinite(p.t)),
  }));
}

export async function renderTerminal(el: HTMLElement): Promise<void> {
  const loaded = await load<DeskDoc>("desk.json");
  const doc = loaded.data;
  if (!doc) {
    el.innerHTML = `<div class="term"><div class="t-empty">Dati non disponibili: <code>desk.json</code> non risponde${
      loaded.error ? ` (${escapeHtml(loaded.error)})` : ""
    }. Il motore lo pubblica a ogni giro; se è il primo avvio, riprova fra mezz'ora.</div></div>`;
    return;
  }
  el.innerHTML = `<div class="term">
    ${doc.sample ? '<div class="banner sample">DATI DI ESEMPIO: questa non è la situazione reale dei libri.</div>' : ""}
    ${header(doc, loaded.origin)}
    ${market(doc)}
    ${forecastTable(doc)}
    ${booksTable(doc)}
    ${bookDetails(doc)}
    ${decisions(doc)}
    ${fills(doc)}
    ${options(doc)}
    ${backtest(doc)}
    ${health(doc)}
    ${howTo()}
  </div>`;
  const chart = document.getElementById("t-equity");
  const legend = document.getElementById("t-equity-legend");
  if (chart && legend) {
    const series = equitySeries(doc);
    const dateFmt = (t: number): string => clock(new Date(t).toISOString());
    lineChart(chart, series, {
      ariaLabel: "Equity dei quattro libri dall'avvio",
      height: 170,
      baseline: doc.books[0]?.initial_capital ?? 10000,
      fmt: (v) => num(v, 0),
      dateFmt,
    });
    legend.innerHTML = series.some((s) => s.points.length >= 2) ? lineLegend(series, (v) => num(v, 0)) : "";
  }
}
