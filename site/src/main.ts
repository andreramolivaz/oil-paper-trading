import "./styles.css";
import "./term.css";
import { disposeCharts } from "./charts/base";
import { disposeLineCharts } from "./charts/lines";
import { clearCache } from "./data";
import { escapeHtml } from "./format";
import { renderArchive } from "./pages/archive";
import { renderBacktest } from "./pages/backtest";
import { renderHome } from "./pages/home";
import { renderMarket } from "./pages/market";
import { renderForecasts } from "./pages/forecasts";
import { renderReset } from "./pages/reset";
import { renderRisk } from "./pages/risk";
import { renderStrategies } from "./pages/strategies";
import { renderTerminal } from "./pages/terminal";
import { renderTrades } from "./pages/trades";

type Renderer = (el: HTMLElement) => Promise<void>;

interface Route {
  path: string;
  label: string;
  render: Renderer;
  /** Shown in the top bar. The pages of the previous system stay routed but are reached from the archive. */
  nav?: boolean;
}

const routes: Route[] = [
  { path: "/", label: "Terminale", render: renderTerminal, nav: true },
  { path: "/backtest", label: "Backtest", render: renderBacktest, nav: true },
  { path: "/reset", label: "Reset", render: renderReset, nav: true },
  { path: "/archivio", label: "Archivio", render: renderArchive, nav: true },
  { path: "/conto-storico", label: "Conto storico", render: renderHome },
  { path: "/previsioni", label: "Previsioni", render: renderForecasts },
  { path: "/strategie", label: "Strategie", render: renderStrategies },
  { path: "/mercato", label: "Mercato", render: renderMarket },
  { path: "/operazioni", label: "Operazioni", render: renderTrades },
  { path: "/rischio", label: "Rischio", render: renderRisk },
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
    .filter((r) => r.nav)
    .map((r) => `<a href="#${r.path}" data-path="${r.path}">${escapeHtml(r.label.toLowerCase())}</a>`)
    .join("");
  return `<div class="app">
    <div class="wrap">
      <header class="topbar">
        <div class="container">
          <a class="brand" href="#/">BRENT DESK<small>paper trading · dati reali</small></a>
          <nav class="tnav" aria-label="Navigazione principale">${nav}</nav>
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

/** The pages of the previous system light up "archivio": that is where they are reached from. */
function navPath(route: Route): string {
  return route.nav ? route.path : "/archivio";
}

function markActive(path: string): void {
  const active = navPath(matchRoute(path));
  for (const link of document.querySelectorAll<HTMLAnchorElement>(".tnav a")) {
    if (link.dataset.path === active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

async function navigate(): Promise<void> {
  const path = currentPath();
  const route = matchRoute(path);
  const view = document.getElementById("view");
  if (!view) return;
  disposeCharts();
  disposeLineCharts();
  markActive(path);
  document.title = route.path === "/" ? "Brent Desk — paper trading" : `${route.label} · Brent Desk`;
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
