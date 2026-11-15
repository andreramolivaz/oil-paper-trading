/** The radar (phase 22): the top of the desk page.
 *
 * Three things, in the order a person asks them: where is oil now, is anything unusual, and what would the next
 * big move do to my books. Every tile is the engine's own sentence (`desk.json` -> `radar.tiles`) with one figure
 * set large and a dot: amber when a declared rule flags it, quiet otherwise. Nothing here is computed in the
 * browser except the layout of the options' range.
 */
import { src } from "../components/ui";
import type { Book, DeskDoc, RadarQuantile, RadarTile } from "../desk";
import { section } from "../deskview";
import { escapeHtml, longDay, num, percent, shortDay, signed, signedPercent, signedUsd, tone, usd } from "../format";

const pts = (x: number | null | undefined, digits = 1): string =>
  x == null || !Number.isFinite(x) ? "n/a" : `${signed(x * 100, digits)} pts`;

function quote(label: string, q: { price: number | null; change: number | null; symbol?: string } | undefined): string {
  if (!q || q.price == null) return "";
  return `<div class="r-quote">
    <span class="r-qlabel">${escapeHtml(label)}</span>
    <span class="r-qprice">${usd(q.price, 2)}</span>
    <span class="r-qchg ${tone(q.change)}">${signedPercent(q.change, 1)} today</span>
  </div>`;
}

/** Where oil is now: the two quotes the desk trades on, large. */
export function marketStrip(doc: DeskDoc): string {
  const m = doc.market;
  const body = `${quote("Brent", m.brent)}${quote("WTI", m.wti)}`;
  return body ? `<div class="r-market">${body}</div>` : "";
}

/** The figure a tile sets large: the one number a glance should take away. */
function figure(t: RadarTile): { value: string; sub: string } {
  switch (t.id) {
    case "kalshi":
      return { value: t.value == null ? "n/a" : percent(t.value, 0), sub: `${pts(t.change_week)} in a week` };
    case "options": {
      const q05 = t.quantiles?.find((q) => q.level === "q05");
      const q95 = t.quantiles?.find((q) => q.level === "q95");
      if (q05?.brent != null && q95?.brent != null)
        return { value: `${usd(q05.brent, 0)} to ${usd(q95.brent, 0)}`, sub: `Brent by ${shortDay(t.expiry)}, 9 chances in 10` };
      return { value: t.state === "two" ? "Two states" : "One state", sub: t.expiry ? `by ${shortDay(t.expiry)}` : "" };
    }
    case "weekend":
      return {
        value: t.change == null ? "n/a" : pts(t.change),
        sub: t.phase === "in progress" ? "since Friday's close, still open" : t.phase === "over" ? "last weekend" : "",
      };
    case "reopening":
      return {
        value: t.median ? longDay(t.median).replace(/^\d+\s/, "") : "Beyond 2028",
        sub: "Kalshi's median date",
      };
    case "term":
      return {
        value: t.gap == null ? "n/a" : `${signed(t.gap * 100, 0)} pts`,
        sub: t.near && t.far ? `${t.near.dte}-day against ${t.far.dte}-day volatility` : "",
      };
    case "spread":
      return {
        value: t.value == null ? "n/a" : `${signed(t.value, 2)} $`,
        sub: t.z == null ? "Brent minus WTI" : `week's move: ${num(Math.abs(t.z), 1)} sd`,
      };
    case "system":
      return { value: t.flag ? "Look" : t.asleep ? "Asleep" : "OK", sub: t.asleep ? "weekend" : "engine and data" };
    default:
      return { value: "", sub: "" };
  }
}

/** The options' range as a bar: the middle half dark, the 90 % range light, today's price as a tick. */
function rangeBar(t: RadarTile): string {
  const qs = t.quantiles ?? [];
  const at = (level: string): RadarQuantile | undefined => qs.find((q) => q.level === level);
  const [a, b, mid, c, d] = ["q05", "q25", "q50", "q75", "q95"].map(at);
  if (!a || !b || !c || !d || a.move == null || d.move == null || b.move == null || c.move == null) return "";
  const lo = Math.min(a.move, -0.05);
  const hi = Math.max(d.move, 0.05);
  const x = (move: number): number => ((move - lo) / (hi - lo)) * 100;
  const label = (q: RadarQuantile | undefined, where: string): string =>
    q?.brent == null ? "" : `<span class="r-rlab ${where}" style="left:${x(q.move ?? 0).toFixed(1)}%">${usd(q.brent, 0)}</span>`;
  return `<div class="r-range" role="img" aria-label="Brent by ${escapeHtml(shortDay(t.expiry))} as the options price it: 5 % below ${usd(
    a.brent ?? null,
    0,
  )}, half between ${usd(b.brent ?? null, 0)} and ${usd(c.brent ?? null, 0)}, 5 % above ${usd(d.brent ?? null, 0)}">
    <div class="r-rtrack">
      <span class="r-r90" style="left:${x(a.move).toFixed(1)}%;right:${(100 - x(d.move)).toFixed(1)}%"></span>
      <span class="r-r50" style="left:${x(b.move).toFixed(1)}%;right:${(100 - x(c.move)).toFixed(1)}%"></span>
      <span class="r-rnow" style="left:${x(0).toFixed(1)}%"></span>
    </div>
    <div class="r-rlabs">${label(a, "lo")}${label(mid, "mid")}${label(d, "hi")}</div>
    <p class="r-rkey"><i class="k50"></i> 1 chance in 2 <i class="k90"></i> 9 in 10 <i class="know"></i> today's forward</p>
  </div>`;
}

function tileCard(t: RadarTile): string {
  const f = figure(t);
  const extra = t.id === "options" ? rangeBar(t) : "";
  const note = t.mark_note ? `<p class="r-note">${escapeHtml(t.mark_note)}.</p>` : "";
  return `<article class="r-tile${t.flag ? " flag" : ""}" id="radar-${escapeHtml(t.id)}">
    <header><span class="r-dot" aria-hidden="true"></span><h3>${escapeHtml(t.label)}</h3>${
      t.flag ? '<span class="r-badge">Look</span>' : ""
    }</header>
    <p class="r-value">${escapeHtml(f.value)}</p>
    ${f.sub ? `<p class="r-sub">${escapeHtml(f.sub)}</p>` : ""}
    ${extra}
    <p class="r-text">${escapeHtml(t.text ?? "")}</p>
    ${note}
    <p class="r-src">${src(t.source, t.asof, !!t.approx)}</p>
  </article>`;
}

/** What the options' one-in-twenty move either way would do to each oil book, in dollars. */
function scenarios(doc: DeskDoc): string {
  const rows = doc.books.filter((b: Book) => b.scenarios?.tail && Math.abs(b.exposure ?? 0) > 0);
  const any = doc.books.find((b) => b.scenarios?.tail_moves);
  if (!any?.scenarios?.tail_moves) return "";
  const m = any.scenarios.tail_moves;
  const head = `If oil makes the options' 1-in-20 move by ${shortDay(any.scenarios.expiry)}: ${signedPercent(m.low, 0)} down or ${signedPercent(m.high, 0)} up`;
  if (!rows.length)
    return `<p class="r-scn-none"><b>${escapeHtml(head)}</b>, no book would feel it: none holds oil today.</p>`;
  const body = rows
    .map(
      (b) => `<tr><th scope="row"><a href="#/book/${encodeURIComponent(b.id)}">${escapeHtml(b.name)}</a></th>
        <td class="n">${num(b.exposure ?? null, 2)}x</td>
        <td class="n ${tone(b.scenarios?.tail?.low)}">${signedUsd(b.scenarios?.tail?.low ?? null, 0)}</td>
        <td class="n ${tone(b.scenarios?.tail?.high)}">${signedUsd(b.scenarios?.tail?.high ?? null, 0)}</td></tr>`,
    )
    .join("");
  return `<div class="r-scn">
    <p class="r-scn-h">${escapeHtml(head)}</p>
    <div class="t-scroll"><table class="t-table r-scn-t">
      <thead><tr><th>Book</th><th class="n">Holds</th><th class="n">Oil ${signedPercent(m.low, 0)}</th><th class="n">Oil ${signedPercent(
        m.high,
        0,
      )}</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>
    <p class="d-fine">A straight line on today's size: no stop, no resizing on the way. A model of delayed option quotes (≈).</p>
  </div>`;
}

export function radarSection(doc: DeskDoc): string {
  const r = doc.radar;
  if (!r || !r.tiles?.length) return "";
  const n = r.flagged?.length ?? 0;
  const summary = `<p class="r-summary${n ? " flag" : ""}"><span class="r-dot" aria-hidden="true"></span>${escapeHtml(r.summary ?? "")}</p>`;
  const order = ["kalshi", "options", "weekend", "reopening", "term", "spread", "system"];
  const tiles = [...r.tiles].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  return section(
    "Radar: what moves before the price",
    `${summary}<div class="r-grid">${tiles.map(tileCard).join("")}</div>${scenarios(doc)}
     <p class="d-fine">${escapeHtml(r.note ?? "")}</p>`,
    "",
    "radar",
  );
}
