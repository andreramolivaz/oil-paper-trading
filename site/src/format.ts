/** Italian formatting helpers. Every number the user sees goes through here.
 *
 * Three rules keep a column of figures aligned, which is the whole point of a terminal readout:
 *   - `useGrouping: true`. The it-IT default is "min2", so 1 000 loses its separator while 10 000 keeps
 *     it: a figure would gain and lose a glyph as it crossed 10 000.
 *   - one minus sign, U+2212, never the hyphen Intl emits. They have different widths.
 *   - a narrow no-break space (U+202F) between a figure and its unit, so "113,96 $" can never wrap in two.
 */

const MINUS = "\u2212";
const NBSP = "\u202f"; // narrow no-break space: joins the figure to its unit

const fix = (s: string) => s.replace("-", MINUS);

const nf = (min: number, max: number) =>
  new Intl.NumberFormat("it-IT", { minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: true });
const pct = (digits: number) =>
  new Intl.NumberFormat("it-IT", {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: true,
  });

export const EMPTY = "n/d";
/** What a tile shows in place of a figure that does not exist. Absence should not shout. */
export const DASH = "\u2014";

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return fix(nf(digits, digits).format(value));
}

export function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return `${fix(nf(digits, digits).format(value))}${NBSP}$`;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return fix(pct(digits).format(value));
}

/** Precision is a property of the QUANTITY, not of the call site. These are the only four that exist. */
export const money = (v: number | null | undefined) => usd(v, 2); // equity, P&L: cents matter
export const notional = (v: number | null | undefined) => usd(v, 0); // notional, margin: cents are noise
export const price = (v: number | null | undefined) => usd(v, 2); // $/bbl
export const contracts = (v: number | null | undefined) => num(v, 0);

/** The sign colour of a figure. Zero is NEITHER up nor down: a flat account is not a gain. */
export function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return "";
  return value > 0 ? "up" : "down";
}

/** Splits the unit out of a formatted figure so the markup can set it smaller and muted. */
export function withUnit(formatted: string): string {
  const i = formatted.lastIndexOf(NBSP);
  if (i < 0) return formatted;
  return `${formatted.slice(0, i)}<span class="unit">${formatted.slice(i + 1)}</span>`;
}

export function signedPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const s = percent(value, digits);
  return value > 0 ? `+${s}` : s;
}

export function signedUsd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const s = usd(value, digits);
  return value > 0 ? `+${s}` : s;
}

export function leverage(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return `${fix(nf(2, 2).format(value))}x`;
}

export function barrels(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const s = nf(0, 0).format(Math.abs(value));
  return `${value < 0 ? MINUS : ""}${s}${NBSP}bbl`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("it-IT", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Rome" }).format(t);
}

export function dateOnly(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso.length <= 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("it-IT", { dateStyle: "medium", timeZone: "UTC" }).format(t);
}

/** Provenance stamp: a date-only or midnight-UTC datum keeps its date; an instant keeps its UTC clock.
 *  Forcing a daily figure through Europe/Rome used to render 2026-10-05T22:00:00Z as "06/10/26, 00:00". */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso.length <= 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(t)) return EMPTY;
  const d = new Date(t);
  const dateOnlyInput = iso.length <= 10;
  const atUtcMidnight = d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0;
  const day = new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "short", timeZone: "UTC" }).format(t);
  if (dateOnlyInput || atUtcMidnight) return day;
  const time = new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" }).format(t);
  return `${day} ${time} UTC`;
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return EMPTY;
  const minutes = Math.round((Date.now() - t) / 60000);
  if (minutes < 1) return "adesso";
  if (minutes < 60) return `${minutes} min fa`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h fa`;
  return `${Math.round(hours / 24)} g fa`;
}

export function direction(value: string | null | undefined): string {
  switch (value) {
    case "long":
      return "Long";
    case "short":
      return "Short";
    case "flat":
      return "Flat";
    default:
      return EMPTY;
  }
}

export function lifecycle(value: string | null | undefined): string {
  switch (value) {
    case "active":
      return "Attiva";
    case "incubation":
      return "Incubazione";
    case "research":
      return "Ricerca";
    case "retired":
      return "Ritirata";
    default:
      return EMPTY;
  }
}

export function family(value: string | null | undefined): string {
  switch (value) {
    case "trend":
      return "Trend";
    case "carry":
      return "Carry e curva";
    case "relative_value":
      return "Relative value";
    case "fundamental":
      return "Fondamentali";
    case "event":
      return "Eventi";
    case "volatility":
      return "Volatilità";
    case "ml":
      return "Machine learning";
    default:
      return value ?? EMPTY;
  }
}

export function reason(value: string | null | undefined): string {
  const map: Record<string, string> = {
    signal: "Segnale",
    rebalance: "Ribilanciamento",
    stop_loss: "Stop loss",
    take_profit: "Take profit",
    trailing_stop: "Trailing stop",
    roll: "Roll",
    circuit_breaker: "Circuit breaker",
    margin_call: "Margin call",
    liquidation: "Liquidazione",
    reset: "Reset",
    stale_data_delever: "Dati stantii: riduzione leva",
    expiry: "Scadenza",
  };
  return value ? (map[value] ?? value) : EMPTY;
}

export function escapeHtml(text: unknown): string {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
