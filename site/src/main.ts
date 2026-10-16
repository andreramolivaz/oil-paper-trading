import "@fontsource-variable/atkinson-hyperlegible-next/wght.css";
import "./styles.css";
import "./term.css";
import "./desk.css";
import { disposeLineCharts } from "./charts/lines";
import { clearCache } from "./data";
import { escapeHtml } from "./format";
import { renderArchive } from "./pages/archive";
import { renderBacktest } from "./pages/backtest";
import { renderBook } from "./pages/book";
import { renderDesk } from "./pages/desk";
import { renderReset } from "./pages/reset";
import { renderSignals } from "./pages/signals";

type Renderer = (el: HTMLElement) => Promise<void>;

interface Route {
  path: string;
  label: string;
  title: string;
  render: Renderer;
}

/** Five pages in the bar: the desk (what the books hold), the signals (what they read), the backtest, the reset and
 *  the archive of the first system. A book's own page (`#/book/<id>`) opens from the desk. */
const routes: Route[] = [
  { path: "/", label: "Desk", title: "Brent Desk, paper trading", render: renderDesk },
  { path: "/signals", label: "Signals", title: "Signals · Brent Desk", render: renderSignals },
  { path: "/backtest", label: "Backtest", title: "Backtest · Brent Desk", render: renderBacktest },
  { path: "/reset", label: "Reset", title: "Reset · Brent Desk", render: renderReset },
  { path: "/archive", label: "Archive", title: "Archive · Brent Desk", render: renderArchive },
];
const BOOK_PREFIX = "/book/";
/** The old Italian paths of the archive and of the first system's pages all land on the archive. */
const LEGACY_PATHS = ["/archivio", "/conto-storico", "/previsioni", "/strategie", "/mercato", "/operazioni", "/rischio"];

const DISCLAIMER = "Study simulation on real data. No financial advice, no real orders, no broker connected.";

function currentPath(): string {
  const hash = window.location.hash.replace(/^#/, "") || "/";
  return hash.split("?")[0] || "/";
}

function currentQuery(): URLSearchParams {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "");
}

function decoded(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text; // a malformed escape in the address: the book page says there is no such book
  }
}

function matchRoute(path: string): { route: Route; render: Renderer; nav: string } {
  if (path.startsWith(BOOK_PREFIX)) {
    const id = decoded(path.slice(BOOK_PREFIX.length));
    return { route: routes[0]!, render: (el) => renderBook(el, id), nav: "/" };
  }
  const exact = routes.find((r) => r.path === path);
  if (exact) return { route: exact, render: exact.render, nav: exact.path };
  if (LEGACY_PATHS.some((p) => path.startsWith(p))) return { route: routes[4]!, render: routes[4]!.render, nav: "/archive" };
  const prefix = routes.find((r) => r.path !== "/" && path.startsWith(r.path)) ?? routes[0]!;
  return { route: prefix, render: prefix.render, nav: prefix.path };
}

const ICON_REFRESH = `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M13 8a5 5 0 1 1-1.6-3.7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M11.6 1.8v2.9H8.7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const ICON_THEME = `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="5.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 2.4a5.6 5.6 0 0 1 0 11.2z" fill="currentColor"/></svg>`;

function shell(): string {
  const nav = routes.map((r) => `<a href="#${r.path}" data-path="${r.path}">${escapeHtml(r.label)}</a>`).join("");
  return `<div class="app">
    <div class="wrap">
      <header class="topbar">
        <div class="container">
          <a class="brand" href="#/">Brent Desk<small>paper trading on real data</small></a>
          <nav class="tnav" aria-label="Main navigation">${nav}</nav>
          <div class="spacer"></div>
          <button class="icon-btn" id="btn-refresh" title="Reload the data from the data branch" aria-label="Reload the data">${ICON_REFRESH}</button>
          <button class="icon-btn" id="btn-theme" title="Light or dark theme" aria-label="Switch between the light and the dark theme">${ICON_THEME}</button>
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

function markActive(nav: string): void {
  for (const link of document.querySelectorAll<HTMLAnchorElement>(".tnav a")) {
    if (link.dataset.path === nav) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

/** Where each page was scrolled to - kept as the reader scrolls, because on Back the browser may move the page
 *  before the hash changes - so that going back to the desk from a book lands where the reader left it; a link
 *  to another page opens it at the top. */
const scrolled = new Map<string, number>();
let shownPath = "";
let followedLink = false;
/** Each navigation renders into a container of its own: a page whose data arrives after the reader has moved
 *  on writes into a container no longer on the screen, never over the page the address names. */
let navigation = 0;
/** While a page is being swapped in, the scroll the browser reports is the transition's, not the reader's. */
let settling = false;

async function navigate(): Promise<void> {
  const path = currentPath();
  const { route, render, nav } = matchRoute(path);
  const view = document.getElementById("view");
  if (!view) return;
  const mine = ++navigation;
  const restore = scrolled.get(path) ?? 0; // read before the swap: a shorter page clamps the scroll and reports it
  settling = true;
  const samePage = path === shownPath;
  const toTop = followedLink && !samePage;
  followedLink = false;
  shownPath = path;
  disposeLineCharts();
  markActive(nav);
  document.title = route.title;
  view.setAttribute("aria-busy", "true");
  const container = document.createElement("div");
  container.className = "page";
  if (samePage && view.firstElementChild) {
    // the same page again (the reload button, the theme): render beside the old one, swap when it is ready
    container.hidden = true;
    view.append(container);
  } else {
    container.innerHTML = `<div class="empty">Loading…</div>`;
    view.replaceChildren(container);
  }
  try {
    await render(container);
  } catch (err) {
    container.innerHTML = `<div class="banner bad">The page could not be rendered: ${escapeHtml(
      err instanceof Error ? err.message : String(err),
    )}</div>`;
  }
  if (mine !== navigation) return; // the reader went elsewhere meanwhile: that navigation owns the view
  if (container.hidden) {
    container.hidden = false;
    for (const old of [...view.children]) if (old !== container) old.remove();
    window.dispatchEvent(new Event("resize")); // its charts were drawn hidden: redraw them at their real width
  }
  view.setAttribute("aria-busy", "false");
  settling = false;
  if (currentQuery().get("at")) return; // the page scrolled itself to the section asked for
  window.scrollTo(0, toTop ? 0 : restore);
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
  // the page restores its own scroll (below): the browser's would move it before the new page is drawn
  try {
    history.scrollRestoration = "manual";
  } catch {
    /* an old browser: its own restoration stands */
  }
  window.addEventListener(
    "scroll",
    () => {
      if (shownPath && !settling) scrolled.set(shownPath, window.scrollY);
    },
    { passive: true },
  );
  // a click on a link to another page of the site opens it at the top; back and forward restore where the
  // reader was. The book page's "Desk" link goes back when the reader came from the desk, filter and place kept.
  document.addEventListener(
    "click",
    (ev) => {
      const a = (ev.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#/"]');
      if (!a) return;
      if (a.dataset.back !== undefined && cameFromDesk) {
        ev.preventDefault();
        history.back();
        return;
      }
      if (a.getAttribute("href") !== window.location.hash) followedLink = true;
    },
    true,
  );
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
  window.addEventListener("hashchange", (ev: HashChangeEvent) => {
    const from = new URL(ev.oldURL).hash.replace(/^#/, "").split("?")[0] || "/";
    cameFromDesk = from === "/" && currentPath().startsWith(BOOK_PREFIX);
    void navigate();
  });
  void navigate();
}

/** True while the book page on screen was opened from the desk: its "Desk" link then goes back. */
let cameFromDesk = false;

boot();
