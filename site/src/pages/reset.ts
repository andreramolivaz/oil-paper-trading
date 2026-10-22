/** Reset: the state of the current epoch, an optional token kept in the browser, the workflow dispatch and the
 * history of the epochs.
 *
 * This page ARCHIVES an account, so the interactive part is deliberately plain: the reset stays a GitHub
 * workflow (never a browser action), the button stays disabled until the field holds exactly RESET, and
 * without a token it opens the workflow page instead of inventing a shortcut.
 */
import { TOKEN_KEY, config, dispatchUrl, readToken, workflowUrl } from "../config";
import { load } from "../data";
import {
  DASH,
  EMPTY,
  contracts,
  dateTime,
  escapeHtml,
  money,
  notional,
  num,
  signedUsd,
  stamp,
  tone,
  withUnit,
} from "../format";
import { card, empty, originBanner, pageTitle, src, stat } from "../components/ui";
import type { DeskDoc } from "../desk";
import type { EpochRow, EpochsDoc, SummaryDoc } from "../types";

/** The confirmation text is a constant of the workflow, not a parameter of the interface. */
const CONFIRM = "RESET";

export async function renderReset(el: HTMLElement): Promise<void> {
  const [summary, epochs, desk] = await Promise.all([
    load<SummaryDoc>("summary.json"),
    load<EpochsDoc>("epochs.json"),
    load<DeskDoc>("desk.json"),
  ]);
  const books = desk.data?.books ?? [];
  const acct = summary.data?.account ?? {};
  const initial = acct.initial_capital ?? 10000;
  const dead = Boolean(acct.dead);
  const token = readToken();

  // The history arrives in chronological order: the most recent epoch goes on top.
  const epochRows = (epochs.data?.epochs ?? []).slice().reverse();
  const current = epochRows.find((e) => e.current);

  // Without summary.json the account cannot be declared operational: absence is said, not filled.
  const status =
    summary.data === null
      ? `<div class="banner warn">Account state unavailable: <code>summary.json</code> does not answer${
          summary.error ? ` (${escapeHtml(summary.error)})` : ""
        }. The reset is still possible: it is a GitHub workflow and does not depend on this page.</div>`
      : dead
        ? `<div class="banner bad">The account is wiped out: trading is stopped and resumes only after a reset.</div>`
        : `<div class="banner info">The account is operational (equity ${money(acct.equity)}). The reset is available at any
           time, with confirmation.</div>`;

  const stateStrip = `<div class="strip">
    ${stat("Epoch", contracts(acct.epoch))}
    ${stat("Equity", withUnit(money(acct.equity)))}
    ${stat("P&L this epoch", withUnit(signedUsd(acct.pnl_total)), tone(acct.pnl_total))}
    ${stat("Initial capital", withUnit(notional(initial)))}
    ${stat("Trades", contracts(current?.n_trades))}
    ${stat("Opened", stamp(current?.started_ts))}
  </div>`;

  const tokenState = `<span class="chip ${token ? "good" : ""}" id="token-state">${token ? "token saved" : "no token"}</span>`;

  const form = `<p style="margin-top:0">With a fine-grained token saved in this browser (permission <em>Actions: write</em> on this
     repository only) the button starts the workflow directly. Without a token, the button opens the workflow page on
     GitHub, where it starts with one click.</p>
    <div style="display:grid;gap:8px;margin-top:14px">
      <label class="label" for="token">Fine-grained token</label>
      <p class="muted" style="margin:0">It stays in your browser only and is never sent anywhere else.</p>
      <input id="token" type="password" placeholder="github_pat_…" value="${escapeHtml(token)}" autocomplete="off"
        spellcheck="false" />
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn secondary sm" id="btn-save-token">Save token</button>
        <button class="btn secondary sm" id="btn-clear-token">Remove token</button>
      </div>
    </div>
    <div style="display:grid;gap:8px;margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
      <label class="label" for="book">What to bring back to ${notional(initial)}</label>
      <select id="book">
        <option value="all">Everything: every book and the first system's account</option>
        ${books
          .map(
            (b) =>
              `<option value="${escapeHtml(b.id)}">Only the ${escapeHtml(b.name)} book (${escapeHtml(b.vehicle)})${
                b.initial_capital !== initial ? `, back to its own ${notional(b.initial_capital)}` : ""
              }</option>`,
          )
          .join("")}
      </select>
      <label class="label" for="confirm" style="margin-top:8px">Type ${CONFIRM} to confirm</label>
      <p class="muted" style="margin:0">The button stays disabled until the field holds exactly <code>${CONFIRM}</code>.</p>
      <input id="confirm" type="text" placeholder="${CONFIRM}" autocomplete="off" spellcheck="false" />
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:2px">
        <button class="btn danger" id="btn-reset" disabled>Bring back to ${notional(initial)}</button>
        <a class="btn secondary" href="${escapeHtml(workflowUrl())}" target="_blank" rel="noopener">Open the workflow on GitHub</a>
      </div>
    </div>
    <div id="reset-result" style="margin-top:12px" aria-live="polite"></div>`;

  el.innerHTML =
    pageTitle("Reset", `Bring a book, or everything, back to ${notional(initial)}, archiving its current life`) +
    originBanner(epochs, epochs.data?.generated_at) +
    card("The books", bookTable(books), src("engine/desk", desk.data?.generated_at)) +
    status +
    card("The first system's account", stateStrip, src("engine/broker", acct.asof)) +
    card(
      "How it works",
      `<p style="margin-top:0">The reset is a GitHub workflow (<code>reset.yml</code>), not a browser action: that way the
       official state stays one and is tracked. The workflow archives the current life of the chosen book (or of all of
       them), closes the positions at the last price and starts again from ${notional(initial)}. The engine sees it on the
       next tick.</p>
       <p class="muted" style="margin:8px 0 0">The history is not rewritten: every archived epoch keeps its initial, highest,
       lowest and final equity and its number of trades, and stays in the table below.</p>
       <p class="muted" style="margin:8px 0 0">Write permission on the workflows of
       ${escapeHtml(config.owner)}/${escapeHtml(config.repo)} is required.</p>`,
    ) +
    card("Start the reset", form, tokenState) +
    card("History of the account's lives", epochTable(epochRows), src("engine/live", epochs.data?.generated_at));

  // within this page: while the same page is drawn again (the reload button) the old copy is still in the document
  const confirmInput = el.querySelector<HTMLInputElement>("#confirm");
  const bookSelect = el.querySelector<HTMLSelectElement>("#book");
  const resetBtn = el.querySelector<HTMLButtonElement>("#btn-reset");
  const tokenInput = el.querySelector<HTMLInputElement>("#token");
  const tokenChip = el.querySelector<HTMLElement>("#token-state");
  const result = el.querySelector<HTMLElement>("#reset-result");

  confirmInput?.addEventListener("input", () => {
    if (resetBtn) resetBtn.disabled = confirmInput.value.trim() !== CONFIRM;
  });
  el.querySelector("#btn-save-token")?.addEventListener("click", () => {
    if (tokenInput && tokenInput.value.trim()) {
      writeToken(tokenInput.value.trim());
      paintTokenChip(tokenChip, true);
      if (result) result.innerHTML = note("Token saved in this browser.");
    }
  });
  el.querySelector("#btn-clear-token")?.addEventListener("click", () => {
    writeToken(null);
    if (tokenInput) tokenInput.value = "";
    paintTokenChip(tokenChip, false);
    if (result) result.innerHTML = note("Token removed.");
  });
  resetBtn?.addEventListener("click", async () => {
    const stored = readToken() || (tokenInput?.value.trim() ?? "");
    if (!stored) {
      window.open(workflowUrl(), "_blank", "noopener");
      if (result)
        result.innerHTML = note(
          `No token: the workflow page on GitHub is open, where you can start it with one click
           (confirmation: ${CONFIRM}; book: ${escapeHtml(bookSelect?.value ?? "all")}).`,
          "warn",
        );
      return;
    }
    resetBtn.disabled = true;
    if (result) result.innerHTML = note("Sending the request…");
    try {
      const res = await fetch(dispatchUrl(), {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${stored}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs: { confirm: CONFIRM, book: bookSelect?.value ?? "all" } }),
      });
      if (res.status === 204) {
        if (result)
          result.innerHTML = note(
            `Reset started. The workflow archives the epoch and starts again from ${notional(initial)}; the page updates
             on the next tick.`,
            "",
            "var(--good)",
          );
      } else {
        const text = await res.text();
        if (result) result.innerHTML = note(`GitHub answered ${res.status}: ${escapeHtml(text.slice(0, 300))}`, "bad");
      }
    } catch (err) {
      if (result)
        result.innerHTML = note(`The request failed: ${escapeHtml(err instanceof Error ? err.message : String(err))}`, "bad");
    } finally {
      resetBtn.disabled = false;
    }
  });
}

/** The desk's books: what they are worth now and since when. It is what the reset wipes. */
function bookTable(books: DeskDoc["books"]): string {
  if (!books.length) return empty("Book state unavailable.", "desk.json does not answer: the reset is still possible.");
  const body = books
    .map(
      (b) => `<tr>
        <td><a href="#/book/${encodeURIComponent(b.id)}">${escapeHtml(b.name)}</a>${
          b.role === "candidate" ? ' <span class="muted">candidate</span>' : b.role === "lottery" ? ' <span class="muted">lottery</span>' : ""
        }</td>
        <td>${escapeHtml(b.vehicle)}</td>
        <td class="num">${cell(num(b.equity))}</td>
        <td class="num ${tone(b.pnl_total)}">${cell(signedUsd(b.pnl_total))}</td>
        <td class="num">${cell(contracts(b.epoch))}</td>
        <td>${b.started_at ? dateTime(b.started_at) : DASH}</td>
      </tr>`,
    )
    .join("");
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>Book</th><th>Instrument</th><th class="num">Equity <span class="unit">$</span></th>
      <th class="num">P&amp;L</th><th class="num">Life</th><th>Started <span class="unit">New York</span></th></tr></thead>
    <tbody>${body}</tbody></table></div>`;
}

/** The history of the epochs. Units sit in the headers: the cells carry the bare figure, so the column stays
 *  aligned. Equity is money, hence two decimals even when they end in ".00". */
function epochTable(rows: EpochRow[]): string {
  if (!rows.length)
    return empty(
      "No archived epoch: the account is in its first life.",
      "The table opens at the first reset: every epoch keeps its start, end, initial, highest, lowest and final equity, its trades and the reason it ended.",
    );
  const body = rows
    .map(
      (e) => `<tr>
        <td class="num">${cell(contracts(e.epoch))}</td>
        <td>${dateTime(e.started_ts)}</td>
        <td>${e.ended_ts ? dateTime(e.ended_ts) : DASH}</td>
        <td class="num">${cell(num(e.start_equity))}</td>
        <td class="num">${cell(num(e.max_equity))}</td>
        <td class="num">${cell(num(e.min_equity))}</td>
        <td class="num">${cell(num(e.end_equity))}</td>
        <td class="num">${cell(contracts(e.n_trades))}</td>
        <td>${e.current ? '<span class="chip info">current</span>' : escapeHtml(endReason(e.end_reason))}</td>
      </tr>`,
    )
    .join("");
  return `<div class="table-wrap tall"><table class="data">
    <thead><tr>
      <th class="num">Epoch</th>
      <th>Start <span class="unit">New York</span></th>
      <th>End <span class="unit">New York</span></th>
      <th class="num">Initial equity <span class="unit">$</span></th>
      <th class="num">Highest equity <span class="unit">$</span></th>
      <th class="num">Lowest equity <span class="unit">$</span></th>
      <th class="num">Final equity <span class="unit">$</span></th>
      <th class="num">Trades</th>
      <th>Outcome</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}

/** The form's messages live inside the card: they do not carry the page banner's bottom margin. */
function note(html: string, cls = "", accent = ""): string {
  const style = `margin-bottom:0${accent ? `;border-left-color:${accent}` : ""}`;
  return `<div class="banner ${cls}" style="${style}">${html}</div>`;
}

function paintTokenChip(chip: HTMLElement | null, saved: boolean): void {
  if (!chip) return;
  chip.className = `chip ${saved ? "good" : ""}`;
  chip.textContent = saved ? "token saved" : "no token";
}

/** localStorage can throw (Safari in private browsing): an unreachable token counts as absent (`readToken`), and in
 *  that case the button opens GitHub instead of failing silently. */
function writeToken(value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(TOKEN_KEY);
    else localStorage.setItem(TOKEN_KEY, value);
  } catch {
    /* nothing to save: the flow without a token still works */
  }
}

/** In a table a missing figure is a dash, not the "n/a" of the tiles. */
function cell(formatted: string): string {
  return formatted === EMPTY ? DASH : formatted;
}

function endReason(value: string | null | undefined): string {
  if (value === "reset_manual") return "Manual reset";
  if (value === "dead") return "Account wiped out";
  return value ?? DASH;
}
