/** Formatting helpers (English, en-US). Every number the user sees goes through here.
 *
 * Three rules keep a column of figures aligned, which is the whole point of a terminal readout:
 *   - `useGrouping: true`, so 1,000 and 10,000 are spelt the same way.
 *   - one minus sign, U+2212, never the hyphen Intl emits. They have different widths.
 *   - a narrow no-break space (U+202F) between a figure and its unit, so "113.96 $" can never wrap in two.
 */

const MINUS = "\u2212";
const NBSP = "\u202f"; // narrow no-break space: joins the figure to its unit

const fix = (s: string) => s.replace("-", MINUS);

const nf = (min: number, max: number) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: true });
const pct = (digits: number) =>
  new Intl.NumberFormat("en-US", {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: true,
  });

export const EMPTY = "n/a";
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
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "short", timeStyle: "short", timeZone: "America/New_York" }).format(t);
}

export function dateOnly(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso.length <= 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" }).format(t);
}

/** Provenance stamp: a date-only or midnight-UTC datum keeps its date; an instant keeps its UTC clock.
 *  Forcing a daily figure through a local zone used to render 2026-10-05T22:00:00Z as the next day. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso.length <= 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(t)) return EMPTY;
  const d = new Date(t);
  const dateOnlyInput = iso.length <= 10;
  const atUtcMidnight = d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0;
  const day = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }).format(t);
  if (dateOnlyInput || atUtcMidnight) return day;
  const time = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" }).format(t);
  return `${day} ${time} UTC`;
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return EMPTY;
  const minutes = Math.round((Date.now() - t) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
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
      return "Active";
    case "incubation":
      return "Incubation";
    case "research":
      return "Research";
    case "retired":
      return "Retired";
    default:
      return EMPTY;
  }
}

export function family(value: string | null | undefined): string {
  switch (value) {
    case "trend":
      return "Trend";
    case "carry":
      return "Carry and curve";
    case "relative_value":
      return "Relative value";
    case "fundamental":
      return "Fundamentals";
    case "event":
      return "Events";
    case "volatility":
      return "Volatility";
    case "ml":
      return "Machine learning";
    default:
      return value ?? EMPTY;
  }
}

export function reason(value: string | null | undefined): string {
  const map: Record<string, string> = {
    signal: "Signal",
    rebalance: "Rebalance",
    stop_loss: "Stop loss",
    take_profit: "Take profit",
    trailing_stop: "Trailing stop",
    roll: "Roll",
    circuit_breaker: "Circuit breaker",
    margin_call: "Margin call",
    liquidation: "Liquidation",
    reset: "Reset",
    stale_data_delever: "Stale data: leverage cut",
    expiry: "Expiry",
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

/** dd MMM HH:mm in the reader's own time zone (the page says which, with `zoneName`). */
export function clock(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(t);
}

/** The reader's time zone, as a short name ("PDT", "CEST"). */
export function zoneName(): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
      .formatToParts(new Date())
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? "local time";
  } catch {
    return "local time";
  }
}

/** dd MMM of a date, or of an instant in UTC (a daily datum never moves to the next day in a local zone). */
export function day(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso.length <= 10 ? `${iso}T12:00:00Z` : iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }).format(t);
}

/** A figure with its sign spelt out: +1.5, −1.5 (U+2212), 0.0. */
export function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const s = num(Math.abs(value), digits);
  return value > 0 ? `+${s}` : value < 0 ? `${MINUS}${s}` : s;
}

/** "8 October" for a sentence: a date read aloud, not a column. A day ("2026-10-08") is that day; an instant is
 *  the day it was in the reader's own time zone, as every clock time on the desk pages is. */
export function longDay(iso: string | null | undefined): string {
  return dayIn(iso, "long");
}

/** "9 Oct": the short form of `longDay`, for a column. */
export function shortDay(iso: string | null | undefined): string {
  return dayIn(iso, "short");
}

function dayIn(iso: string | null | undefined, month: "long" | "short"): string {
  if (!iso) return EMPTY;
  const dateOnly = iso.length <= 10;
  const t = Date.parse(dateOnly ? `${iso}T12:00:00Z` : iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month, ...(dateOnly ? { timeZone: "UTC" } : {}) }).format(t);
}

/** "Thu 15 Oct, 12:19" in the reader's own time zone: when a decision or a fill happened. */
export function when(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return EMPTY;
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(t);
}
