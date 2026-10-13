import "./styles.css";
import "./term.css";
import { disposeLineCharts } from "./charts/lines";
import { clearCache } from "./data";
import { escapeHtml } from "./format";
import { renderArchive } from "./pages/archive";
import { renderBacktest } from "./pages/backtest";
import { renderReset } from "./pages/reset";
import { renderTerminal } from "./pages/terminal";

type Renderer = (el: HTMLElement) => Promise<void>;

interface Route {
  path: string;
  label: string;
  render: Renderer;
}

/** Four pages: the terminal, its backtest, the reset, and the archive of the first system. Nothing else. */
const routes: Route[] = [
  { path: "/", label: "Terminal", render: renderTerminal },
  { path: "/backtest", label: "Backtest", render: renderBacktest },
  { path: "/reset", label: "Reset", render: renderReset },
  { path: "/archive", label: "Archive", render: renderArchive },
];
/** The old Italian paths of the archive and of the first system's pages all land on the archive. */
const LEGACY_PATHS = ["/archivio", "/conto-storico", "/previsioni", "/strategie", "/mercato", "/operazioni", "/rischio"];

const DISCLAIMER = "Study simulation on real data. No financial advice, no real orders, no broker connected.";

function currentPath(): string {
  const hash = window.location.hash.replace(/^#/, "") || "/";
  return hash.split("?")[0] ?? "/";
}

function matchRoute(path: string): Route {
  const exact = routes.find((r) => r.path === path);
  if (exact) return exact;
  if (LEGACY_PATHS.some((p) => path.startsWith(p))) return routes[3]!;
  const prefix = routes.find((r) => r.path !== "/" && path.startsWith(r.path));
  return prefix ?? routes[0]!;
}

function shell(): string {
  const nav = routes
    .map((r) => `<a href="#${r.path}" data-path="${r.path}">${escapeHtml(r.label.toLowerCase())}</a>`)
    .join("");
  return `<div class="app">
    <div class="wrap">
      <header class="topbar">
        <div class="container">
          <a class="brand" href="#/">BRENT DESK<small>paper trading · real data</small></a>
          <nav class="tnav" aria-label="Main navigation">${nav}</nav>
          <div class="spacer"></div>
          <button class="icon-btn" id="btn-refresh" title="Reload the data from the data branch" aria-label="Reload the data">↻</button>
          <button class="icon-btn" id="btn-theme" title="Light/dark theme" aria-label="Switch theme">◐</button>
        </div>
      </header>
      <div id="disclaimer-slot"></div>
      <main id="view" tabindex="-1"></main>
      <footer class="site">
        <div class="container">
          ${escapeHtml(DISCLAIMER)}
          <br />The engine and the data are on GitHub; every number shown carries its source and time.
        </div>
      </footer>
    </div>
  </div>`;
}

function renderDisclaimer(): void {
  const slot = document.getElementById("disclaimer-slot");
  if (!slot) return;
  let dismissed = false;
  try {
    dismissed = sessionStorage.getItem("opt_disclaimer_dismissed") === "1";
  } catch {
    /* no session storage: the banner simply stays */
  }
  if (dismissed) {
    slot.innerHTML = "";
    return;
  }
  slot.innerHTML = `<div class="container" style="padding-top:12px">
    <div class="banner warn">
      <span>${escapeHtml(DISCLAIMER)}</span>
      <button class="btn secondary sm" id="btn-dismiss">Got it</button>
    </div>
  </div>`;
  document.getElementById("btn-dismiss")?.addEventListener("click", () => {
    try {
      sessionStorage.setItem("opt_disclaimer_dismissed", "1");
    } catch {
      /* nothing to remember: the banner comes back on the next load */
    }
    renderDisclaimer();
  });
}

function markActive(path: string): void {
  const active = matchRoute(path).path;
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
  disposeLineCharts();
  markActive(path);
  document.title = route.path === "/" ? "Brent Desk — paper trading" : `${route.label} · Brent Desk`;
  view.setAttribute("aria-busy", "true");
  view.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    await route.render(view);
  } catch (err) {
    view.innerHTML = `<div class="banner bad">The page could not be rendered: ${escapeHtml(
      err instanceof Error ? err.message : String(err),
    )}</div>`;
  } finally {
    view.setAttribute("aria-busy", "false");
  }
}

function applyStoredTheme(): void {
  try {
    const stored = localStorage.getItem("opt_theme");
    if (stored === "light" || stored === "dark") document.documentElement.setAttribute("data-theme", stored);
  } catch {
    /* no local storage: the default theme stands */
  }
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
    try {
      localStorage.setItem("opt_theme", next);
    } catch {
      /* the choice holds for this page only */
    }
    void navigate();
  });
  window.addEventListener("hashchange", () => void navigate());
  void navigate();
}

boot();
