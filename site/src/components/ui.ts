/** Small HTML builders shared by the pages. Everything is a string: the pages render into innerHTML. */
import { dateTime, escapeHtml, relative } from "../format";
import type { DataOrigin, Loaded, SourceHealth } from "../types";

/** The provenance chip the brief demands next to every number: source plus timestamp. */
export function src(source?: string | null, asof?: string | null, approx = false): string {
  if (!source && !asof) return "";
  const title = [source ? `Fonte: ${source}` : null, asof ? `Dato del ${dateTime(asof)}` : null]
    .filter(Boolean)
    .join(" · ");
  const label = approx ? "≈ fonte" : "fonte";
  return `<span class="src${approx ? " approx" : ""}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">ⓘ ${label}</span>`;
}

export function tile(label: string, value: string, sub?: string, cls = ""): string {
  return `<div class="tile">
    <div class="label">${escapeHtml(label)}</div>
    <div class="value ${cls}">${value}</div>
    ${sub ? `<div class="sub">${sub}</div>` : ""}
  </div>`;
}

export function statusChip(status: string | null | undefined): string {
  const map: Record<string, { cls: string; text: string }> = {
    green: { cls: "good", text: "dati verdi" },
    yellow: { cls: "warn", text: "dati gialli" },
    red: { cls: "bad", text: "dati rossi" },
  };
  const s = map[String(status ?? "")] ?? { cls: "", text: "stato dati n/d" };
  return `<span class="chip ${s.cls}">${s.text}</span>`;
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

export function empty(message = "Nessun dato disponibile."): string {
  return `<div class="empty">${escapeHtml(message)}</div>`;
}

export function card(title: string, body: string, headerExtra = ""): string {
  return `<section class="card">
    <header><h2>${escapeHtml(title)}</h2>${headerExtra}</header>
    ${body}
  </section>`;
}

export function legend(items: { label: string; color: string; dashed?: boolean }[]): string {
  return `<div class="legend">${items
    .map(
      (i) =>
        `<span class="key"><span class="swatch${i.dashed ? " dash" : ""}" style="${
          i.dashed ? `color:${i.color};background:none` : `background:${i.color}`
        }"></span>${escapeHtml(i.label)}</span>`,
    )
    .join("")}</div>`;
}

export function healthTable(sources: SourceHealth[] | undefined): string {
  if (!sources || !sources.length) return empty("Nessuno stato fonte disponibile.");
  const rows = sources
    .map((s) => {
      const key = s.key ?? s.source ?? "n/d";
      const cls = s.status === "green" ? "good" : s.status === "yellow" ? "warn" : "bad";
      return `<tr>
        <td>${escapeHtml(key)}</td>
        <td><span class="chip ${cls}">${escapeHtml(s.status ?? "n/d")}</span></td>
        <td class="num">${s.rows ?? "—"}</td>
        <td>${s.data_asof ? dateTime(s.data_asof) : "—"}</td>
        <td class="wrap-text">${escapeHtml([s.fallback_used ? `fallback: ${s.fallback_used}` : "", s.message ?? ""].filter(Boolean).join(" · "))}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>Fonte</th><th>Stato</th><th>Righe</th><th>Dato del</th><th>Note</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

export function pageTitle(title: string, subtitle?: string): string {
  return `<div style="margin-bottom:12px">
    <h1>${escapeHtml(title)}</h1>
    ${subtitle ? `<p class="muted">${escapeHtml(subtitle)}</p>` : ""}
  </div>`;
}
