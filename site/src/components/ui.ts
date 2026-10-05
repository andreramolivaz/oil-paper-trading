/** Small HTML builders shared by the pages. Everything is a string: the pages render into innerHTML. */
import { EMPTY, dateTime, escapeHtml, relative, stamp } from "../format";
import type { DataOrigin, Loaded, SourceHealth } from "../types";

/** The provenance chip the brief demands next to every number: source plus timestamp.
 *
 * The asof is SHOWN, not parked in a title= that a phone can never surface. The source name stays in the
 * tooltip because it is the less urgent half, and the date is rendered with stamp(), which never converts a
 * UTC daily datum into a local clock time (that used to print tomorrow's date for a 22:00Z observation).
 */
export function src(source?: string | null, asof?: string | null, approx = false): string {
  if (!source && !asof) return "";
  const title = [source ? `Fonte: ${source}` : null, asof ? `Dato del ${dateTime(asof)}` : null]
    .filter(Boolean)
    .join(" · ");
  const shown = asof ? stamp(asof) : (source ?? "");
  return `<span class="src${approx ? " approx" : ""}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${
    approx ? "≈" : "ⓘ"
  } <b>${escapeHtml(shown)}</b></span>`;
}

/** A stat tile. A value that does not exist renders as a muted dash: absence should read as designed,
 *  not as a broken readout at full figure weight. */
export function tile(label: string, value: string, sub?: string, cls = "", why?: string): string {
  const missing = value === EMPTY || value === "";
  const shown = missing ? "—" : value;
  const title = missing && why ? ` title="${escapeHtml(why)}"` : "";
  return `<div class="tile">
    <div class="label">${escapeHtml(label)}</div>
    <div class="value ${missing ? "na" : cls}"${title}>${shown}</div>
    ${sub ? `<div class="sub">${sub}</div>` : ""}
  </div>`;
}

/** A label/figure pair for the hero delta row and the market strip. */
export function stat(label: string, value: string, cls = ""): string {
  return `<div class="stat"><span class="k">${escapeHtml(label)}</span><span class="v ${cls}">${value}</span></div>`;
}

/**
 * A chart and everything that explains it, as ONE unit: readout strip, canvas, legend, provenance — or,
 * when there is nothing to draw, the empty state ALONE. Building them separately is how the pages ended up
 * showing a colour legend for series that were never drawn.
 */
export function chartBlock(opts: {
  id: string;
  hasData: boolean;
  emptyMessage: string;
  emptyHint?: string;
  legendItems?: { label: string; color: string; dashed?: boolean; block?: boolean }[];
  source?: string | null;
  asof?: string | null;
  approx?: boolean;
  size?: "" | "short" | "tall";
  readout?: boolean;
}): string {
  const provenance = opts.source || opts.asof ? `<div style="margin-top:8px">${src(opts.source, opts.asof, opts.approx)}</div>` : "";
  if (!opts.hasData) {
    return `${empty(opts.emptyMessage, opts.emptyHint)}${provenance}`;
  }
  const strip = opts.readout === false ? "" : `<div class="readout" id="${escapeHtml(opts.id)}-readout"></div>`;
  const keys = opts.legendItems?.length ? legend(opts.legendItems) : "";
  return `${strip}<div id="${escapeHtml(opts.id)}" class="chart ${opts.size ?? ""}"></div>${keys}${provenance}`;
}

/** One vocabulary for source health, used by the chip AND the table. A colour name is not a state:
 *  "dati gialli" forces the reader to know a legend, "Dati parziali" says it outright. */
const STATUS: Record<string, { cls: string; text: string }> = {
  green: { cls: "good", text: "dati ok" },
  yellow: { cls: "warn", text: "dati parziali" },
  red: { cls: "bad", text: "dati non affidabili" },
};

export function statusClass(status: string | null | undefined): string {
  return STATUS[String(status ?? "")]?.cls ?? "";
}

export function statusLabel(status: string | null | undefined): string {
  return STATUS[String(status ?? "")]?.text ?? "stato n/d";
}

export function statusChip(status: string | null | undefined, extra?: string): string {
  return `<span class="chip ${statusClass(status)}">${statusLabel(status)}${extra ? ` · ${escapeHtml(extra)}` : ""}</span>`;
}

export function originBanner(loaded: Loaded<unknown>, generatedAt?: string | null): string {
  if (loaded.data === null) {
    return `<div class="banner bad">⚠ Dati non disponibili. ${escapeHtml(loaded.error ?? "")}</div>`;
  }
  const parts: string[] = [];
  if (loaded.origin === "bundled") parts.push("copia inclusa nel sito (il branch dati non risponde)");
  if (loaded.origin === "sample") parts.push("DATI DI ESEMPIO");
  if (generatedAt) parts.push(`aggiornato ${relative(generatedAt)}`);
  if (!parts.length) return "";
  const cls = loaded.origin === "sample" ? "sample" : loaded.origin === "bundled" ? "warn" : "";
  return `<div class="banner ${cls}">${escapeHtml(parts.join(" · "))}</div>`;
}

export function originChip(origin: DataOrigin | null): string {
  if (origin === "live") return `<span class="chip good">live</span>`;
  if (origin === "bundled") return `<span class="chip warn">copia locale</span>`;
  if (origin === "sample") return `<span class="chip bad">esempio</span>`;
  return `<span class="chip bad">non disponibile</span>`;
}

export function empty(message = "Nessun dato disponibile.", hint?: string): string {
  return `<div class="empty"><span>${escapeHtml(message)}</span>${
    hint ? `<span class="hint">${escapeHtml(hint)}</span>` : ""
  }</div>`;
}

export function card(title: string, body: string, headerExtra = ""): string {
  return `<section class="card">
    <header><h2>${escapeHtml(title)}</h2>${headerExtra}</header>
    ${body}
  </section>`;
}

/** The swatch colour travels as a custom property. The old inline `background:none` shorthand wiped the
 *  stylesheet's background-image, so every dashed series had an invisible key. */
export function legend(items: { label: string; color: string; dashed?: boolean; block?: boolean }[]): string {
  return `<div class="legend">${items
    .map(
      (i) =>
        `<span class="key"><span class="swatch${i.dashed ? " dash" : ""}${i.block ? " block" : ""}" style="--sw:${
          i.color
        }"></span>${escapeHtml(i.label)}</span>`,
    )
    .join("")}</div>`;
}

export function healthTable(sources: SourceHealth[] | undefined): string {
  if (!sources || !sources.length) return empty("Nessuno stato fonte disponibile.");
  const rows = sources
    .map((s) => {
      const key = s.key ?? s.source ?? "n/d";
      // The engine already puts "| fallback: x" inside the message, so prepending it printed it twice.
      const message = (s.message ?? "").replace(/\s*\|\s*fallback:.*$/i, "").trim();
      const note = [s.fallback_used ? `fallback: ${s.fallback_used}` : "", message].filter(Boolean).join(" · ");
      return `<tr>
        <td>${escapeHtml(key)}</td>
        <td><span class="chip ${statusClass(s.status)}">${escapeHtml(statusLabel(s.status))}</span></td>
        <td class="num">${s.rows ?? "—"}</td>
        <td class="num">${s.data_asof ? stamp(s.data_asof) : "—"}</td>
        <td class="wrap-text">${escapeHtml(note || "—")}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap tall"><table class="data">
    <thead><tr><th>Fonte</th><th>Stato</th><th class="num">Righe</th><th class="num">Dato del</th><th class="wrap-text">Note</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

export function pageTitle(title: string, subtitle?: string): string {
  return `<div style="margin-bottom:12px">
    <h1>${escapeHtml(title)}</h1>
    ${subtitle ? `<p class="muted" style="margin-top:2px">${escapeHtml(subtitle)}</p>` : ""}
  </div>`;
}
