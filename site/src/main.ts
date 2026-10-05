import "./styles.css";
import { disposeCharts } from "./charts/base";
import { clearCache } from "./data";
import { escapeHtml } from "./format";
import { renderHome } from "./pages/home";
import { renderMarket } from "./pages/market";
import { renderForecasts } from "./pages/forecasts";
import { renderReset } from "./pages/reset";
import { renderRisk } from "./pages/risk";
import { renderStrategies } from "./pages/strategies";
import { renderTrades } from "./pages/trades";

type Renderer = (el: HTMLElement) => Promise<void>;

interface Route {
  path: string;
  label: string;
  icon: string;
  render: Renderer;
}

/** One icon set, one stroke weight, one box. Text glyphs came from four Unicode blocks and one of them
 *  (⚠) rendered as a colour emoji, which broke the monochrome row. */
const ICONS: Record<string, string> = {
  home: '<path d="M3 10.5 10 4l7 6.5" /><path d="M5.5 9.5V16h9V9.5" />',
  fan: '<path d="M10 16V7" /><path d="M4.5 16c0-4 2.5-7 5.5-7s5.5 3 5.5 7" />',
  list: '<path d="M4 6h12M4 10h12M4 14h8" />',
  wave: '<path d="M3 12c2.5 0 2.5-5 5-5s2.5 7 5 7 2.5-4 4-4" />',
  swap: '<path d="M6 4v12M6 16l-2.5-2.5M6 16l2.5-2.5" /><path d="M14 16V4M14 4l-2.5 2.5M14 4l2.5 2.5" />',
  shield: '<path d="M10 3.5 16 6v4.5c0 3.2-2.4 5.3-6 6.5-3.6-1.2-6-3.3-6-6.5V6z" /><path d="M10 8v3.5" /><path d="M10 13.6v.1" />',
  reset: '<path d="M16 10a6 6 0 1 1-1.9-4.4" /><path d="M16.2 3.4V7h-3.6" />',
};

function icon(name: string): string {
  return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${ICONS[name] ?? ""}</svg>`;
}

const routes: Route[] = [
  { path: "/", label: "Home", icon: "home", render: renderHome },
  { path: "/previsioni", label: "Previsioni", icon: "fan", render: renderForecasts },
  { path: "/strategie", label: "Strategie", icon: "list", render: renderStrategies },
  { path: "/mercato", label: "Mercato", icon: "wave", render: renderMarket },
  { path: "/operazioni", label: "Operazioni", icon: "swap", render: renderTrades },
  { path: "/rischio", label: "Rischio", icon: "shield", render: renderRisk },
  { path: "/reset", label: "Reset", icon: "reset", render: renderReset },
];

const DISCLAIMER =
  "Simulazione a scopo di studio su dati reali. Nessun consiglio finanziario, nessun ordine reale, nessun broker collegato.";

function currentPath(): string {
  const hash = window.location.hash.replace(/^#/, "") || "/";
  return hash.split("?")[0] ?? "/";
}

function matchRoute(path: string): Route {
  const exact = routes.find((r) => r.path === path);
  if (exact) return exact;
  const prefix = routes.find((r) => r.path !== "/" && path.startsWith(r.path));
  return prefix ?? routes[0]!;
}

function shell(): string {
  const nav = routes
    .map(
      (r) =>
        `<a href="#${r.path}" data-path="${r.path}"><span class="ico">${icon(r.icon)}</span>${escapeHtml(r.label)}</a>`,
    )
    .join("");
  // The topbar, the disclaimer and the footer share main's container, otherwise the brand sat 89px to the
  // left of the first card edge on a wide screen.
  return `<div class="app">
    <nav class="nav-bottom" aria-label="Navigazione principale">${nav}</nav>
    <div class="wrap">
      <header class="topbar">
        <div class="container">
          <div class="brand">Oil Paper Trading<small>Brent · paper trading</small></div>
          <div class="spacer"></div>
          <button class="icon-btn" id="btn-refresh" title="Ricarica i dati dal branch dati" aria-label="Ricarica i dati">↻</button>
          <button class="icon-btn" id="btn-theme" title="Tema chiaro/scuro" aria-label="Cambia tema">◐</button>
        </div>
      </header>
      <div id="disclaimer-slot"></div>
      <main id="view" tabindex="-1"></main>
      <footer class="site">
        <div class="container">
          ${escapeHtml(DISCLAIMER)}
          <br />Il motore e i dati sono su GitHub; ogni numero mostrato riporta fonte e orario.
        </div>
      </footer>
    </div>
  </div>`;
}

function renderDisclaimer(): void {
  const slot = document.getElementById("disclaimer-slot");
  if (!slot) return;
  if (sessionStorage.getItem("opt_disclaimer_dismissed") === "1") {
    slot.innerHTML = "";
    return;
  }
  slot.innerHTML = `<div class="container" style="padding-top:12px">
    <div class="banner warn">
      <span>${escapeHtml(DISCLAIMER)}</span>
      <button class="btn secondary sm" id="btn-dismiss">Ho capito</button>
    </div>
  </div>`;
  document.getElementById("btn-dismiss")?.addEventListener("click", () => {
    sessionStorage.setItem("opt_disclaimer_dismissed", "1");
    renderDisclaimer();
  });
}

function markActive(path: string): void {
  const active = matchRoute(path);
  for (const link of document.querySelectorAll<HTMLAnchorElement>(".nav-bottom a")) {
    if (link.dataset.path === active.path) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

async function navigate(): Promise<void> {
  const path = currentPath();
  const route = matchRoute(path);
  const view = document.getElementById("view");
  if (!view) return;
  disposeCharts();
  markActive(path);
  document.title = route.path === "/" ? "Oil Paper Trading — Brent" : `${route.label} · Oil Paper Trading`;
  view.setAttribute("aria-busy", "true");
  view.innerHTML = `<div class="empty">Caricamento…</div>`;
  try {
    await route.render(view);
  } catch (err) {
    view.innerHTML = `<div class="banner bad">Errore nel rendering della pagina: ${escapeHtml(
      err instanceof Error ? err.message : String(err),
    )}</div>`;
  } finally {
    view.setAttribute("aria-busy", "false");
  }
}

function applyStoredTheme(): void {
  const stored = localStorage.getItem("opt_theme");
  if (stored === "light" || stored === "dark") document.documentElement.setAttribute("data-theme", stored);
}

function boot(): void {
  const app = document.getElementById("app");
  if (!app) return;
  app.innerHTML = shell();
  app.setAttribute("aria-busy", "false");
  applyStoredTheme();
  renderDisclaimer();
  document.getElementById("btn-refresh")?.addEventListener("click", () => {
    clearCache();
    void navigate();
  });
  document.getElementById("btn-theme")?.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("opt_theme", next);
    void navigate();
  });
  window.addEventListener("hashchange", () => void navigate());
  void navigate();
}

boot();
