/** Small HTML builders shared by the pages. Everything is a string: the pages render into innerHTML. */
import { EMPTY, dateTime, escapeHtml, relative, stamp } from "../format";
import type { DataOrigin, Loaded } from "../types";

/** The provenance chip next to a number: source plus timestamp.
 *
 * The asof is SHOWN, not parked in a title= that a phone can never surface. The source name stays in the
 * tooltip because it is the less urgent half, and the date is rendered with stamp(), which never converts a
 * UTC daily datum into a local clock time (that used to print tomorrow's date for a 22:00Z observation).
 */
export function src(source?: string | null, asof?: string | null, approx = false): string {
  if (!source && !asof) return "";
  const title = [source ? `Source: ${source}` : null, asof ? `As of ${dateTime(asof)}` : null].filter(Boolean).join(" · ");
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

/** A label/figure pair for a strip of figures. */
export function stat(label: string, value: string, cls = ""): string {
  return `<div class="stat"><span class="k">${escapeHtml(label)}</span><span class="v ${cls}">${value}</span></div>`;
}

/** One vocabulary for source health, used by the chip AND the table. A colour name is not a state:
 *  "yellow data" forces the reader to know a legend, "partial data" says it outright. */
const STATUS: Record<string, { cls: string; text: string }> = {
  green: { cls: "good", text: "data ok" },
  yellow: { cls: "warn", text: "partial data" },
  red: { cls: "bad", text: "unreliable data" },
};

export function statusClass(status: string | null | undefined): string {
  return STATUS[String(status ?? "")]?.cls ?? "";
}

export function statusLabel(status: string | null | undefined): string {
  return STATUS[String(status ?? "")]?.text ?? "state n/a";
}

export function statusChip(status: string | null | undefined, extra?: string): string {
  return `<span class="chip ${statusClass(status)}">${statusLabel(status)}${extra ? ` · ${escapeHtml(extra)}` : ""}</span>`;
}

export function originBanner(loaded: Loaded<unknown>, generatedAt?: string | null): string {
  if (loaded.data === null) {
    return `<div class="banner bad">⚠ Data unavailable. ${escapeHtml(loaded.error ?? "")}</div>`;
  }
  const parts: string[] = [];
  if (loaded.origin === "bundled") parts.push("copy bundled with the site (the data branch does not answer)");
  if (loaded.origin === "sample") parts.push("SAMPLE DATA");
  if (generatedAt) parts.push(`updated ${relative(generatedAt)}`);
  if (!parts.length) return "";
  const cls = loaded.origin === "sample" ? "sample" : loaded.origin === "bundled" ? "warn" : "";
  return `<div class="banner ${cls}">${escapeHtml(parts.join(" · "))}</div>`;
}

export function originChip(origin: DataOrigin | null): string {
  if (origin === "live") return `<span class="chip good">live</span>`;
  if (origin === "bundled") return `<span class="chip warn">local copy</span>`;
  if (origin === "sample") return `<span class="chip bad">sample</span>`;
  return `<span class="chip bad">unavailable</span>`;
}

export function empty(message = "No data available.", hint?: string): string {
  return `<div class="empty"><span>${escapeHtml(message)}</span>${hint ? `<span class="hint">${escapeHtml(hint)}</span>` : ""}</div>`;
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

export function pageTitle(title: string, subtitle?: string): string {
  return `<div style="margin-bottom:12px">
    <h1>${escapeHtml(title)}</h1>
    ${subtitle ? `<p class="muted" style="margin-top:2px">${escapeHtml(subtitle)}</p>` : ""}
  </div>`;
}
