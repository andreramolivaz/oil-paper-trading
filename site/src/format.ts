/** Italian formatting helpers. Every number the user sees goes through here. */

const nf = (min: number, max: number) => new Intl.NumberFormat("it-IT", { minimumFractionDigits: min, maximumFractionDigits: max });
const pct = (digits: number) =>
  new Intl.NumberFormat("it-IT", { style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits });

export const EMPTY = "n/d";

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return nf(digits, digits).format(value);
}

export function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return `${nf(digits, digits).format(value)} $`;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return pct(digits).format(value);
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
  return `${nf(2, 2).format(value)}x`;
}

export function barrels(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const s = nf(0, 0).format(Math.abs(value));
  return `${value < 0 ? "−" : ""}${s} bbl`;
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
