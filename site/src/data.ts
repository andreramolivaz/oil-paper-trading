/** Data loader: live JSON from the data branch, bundled snapshot as fallback, sample files in dev. */
import { config, rawBaseUrl } from "./config";
import type { DataOrigin, Loaded } from "./types";

const cache = new Map<string, Loaded<unknown>>();

/** Five-minute bucket: enough to beat the CDN cache without defeating it on every click. */
function bucket(): string {
  return String(Math.floor(Date.now() / (5 * 60 * 1000)));
}

async function fetchJson<T>(url: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal, cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function load<T>(file: string, opts: { force?: boolean } = {}): Promise<Loaded<T>> {
  if (!opts.force && cache.has(file)) return cache.get(file) as Loaded<T>;
  const attempts: { url: string; origin: DataOrigin }[] = [
    { url: `${rawBaseUrl()}/${file}?t=${bucket()}`, origin: "live" },
    { url: `${import.meta.env.BASE_URL}site-data/${file}`, origin: "bundled" },
  ];
  if (import.meta.env.DEV) attempts.push({ url: `/dev-sample/${file}`, origin: "sample" });

  const errors: string[] = [];
  for (const attempt of attempts) {
    try {
      const data = await fetchJson<T>(attempt.url, config.fetchTimeoutMs);
      const loaded: Loaded<T> = { data, origin: attempt.origin, fetchedAt: new Date().toISOString() };
      cache.set(file, loaded as Loaded<unknown>);
      return loaded;
    } catch (err) {
      errors.push(`${attempt.origin}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  const failed: Loaded<T> = {
    data: null,
    origin: null,
    fetchedAt: new Date().toISOString(),
    error: errors.join(" · "),
  };
  cache.set(file, failed as Loaded<unknown>);
  return failed;
}

export async function loadText(file: string): Promise<string | null> {
  for (const url of [`${rawBaseUrl()}/${file}?t=${bucket()}`, `${import.meta.env.BASE_URL}site-data/${file}`]) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) return await res.text();
    } catch {
      /* try the next source */
    }
  }
  return null;
}

export function clearCache(): void {
  cache.clear();
}

/** How stale the engine's output is, in hours, or null when we cannot tell. */
export function ageHours(generatedAt: string | null | undefined): number | null {
  if (!generatedAt) return null;
  const t = Date.parse(generatedAt);
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 3_600_000;
}

export function freshness(generatedAt: string | null | undefined): "ok" | "warn" | "error" | "unknown" {
  const age = ageHours(generatedAt);
  if (age === null) return "unknown";
  if (age > config.staleErrorHours) return "error";
  if (age > config.staleWarnHours) return "warn";
  return "ok";
}
