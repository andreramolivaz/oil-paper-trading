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

const routes: Route[] = [
  { path: "/", label: "Home", icon: "◉", render: renderHome },
  { path: "/previsioni", label: "Previsioni", icon: "◇", render: renderForecasts },
  { path: "/strategie", label: "Strategie", icon: "☰", render: renderStrategies },
  { path: "/mercato", label: "Mercato", icon: "∿", render: renderMarket },
  { path: "/operazioni", label: "Operazioni", icon: "⇅", render: renderTrades },
  { path: "/rischio", label: "Rischio", icon: "⚠", render: renderRisk },
  { path: "/reset", label: "Reset", icon: "↺", render: renderReset },
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
        `<a href="#${r.path}" data-path="${r.path}"><span class="ico" aria-hidden="true">${r.icon}</span>${escapeHtml(r.label)}</a>`,
    )
    .join("");
  return `<div class="app">
    <nav class="nav-bottom" aria-label="Navigazione principale">${nav}</nav>
    <div class="wrap">
      <header class="topbar">
        <div class="brand">Oil Paper Trading<small>Brent · paper trading</small></div>
        <div class="spacer"></div>
        <button class="icon-btn" id="btn-refresh" title="Ricarica i dati dal branch dati">↻</button>
        <button class="icon-btn" id="btn-theme" title="Tema chiaro/scuro">◐</button>
      </header>
      <div id="disclaimer-slot"></div>
      <main id="view" tabindex="-1"></main>
      <footer class="site">
        ${escapeHtml(DISCLAIMER)}
        <br />Il motore e i dati sono su GitHub; ogni numero mostrato riporta fonte e orario.
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
  slot.innerHTML = `<div style="padding:0 var(--gutter);padding-top:12px">
    <div class="banner warn">
      <span>${escapeHtml(DISCLAIMER)}</span>
      <button class="icon-btn" id="btn-dismiss">Ho capito</button>
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
