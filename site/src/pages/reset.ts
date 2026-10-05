/** Reset: stato dell'epoca corrente, token opzionale nel browser, dispatch del workflow e storico delle epoche.
 *
 * Questa pagina ARCHIVIA il conto, quindi la parte interattiva non è stata toccata nel merito: il reset resta un
 * workflow GitHub (mai un'azione del browser), il pulsante resta disattivato finché il campo non contiene
 * esattamente RESET, e senza token si apre la pagina del workflow invece di inventare una scorciatoia. Qui sono
 * cambiate solo la geometria del form (campi e pulsanti del nuovo sistema) e la provenienza dei numeri.
 */
import { config, dispatchUrl, workflowUrl } from "../config";
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
import type { EpochRow, EpochsDoc, SummaryDoc } from "../types";

const TOKEN_KEY = "opt_gh_token";
/** Il testo di conferma è una costante del workflow, non un parametro dell'interfaccia. */
const CONFIRM = "RESET";

export async function renderReset(el: HTMLElement): Promise<void> {
  const [summary, epochs] = await Promise.all([load<SummaryDoc>("summary.json"), load<EpochsDoc>("epochs.json")]);
  const acct = summary.data?.account ?? {};
  const initial = acct.initial_capital ?? 10000;
  const dead = Boolean(acct.dead);
  const token = readToken();

  // Lo storico arriva in ordine cronologico: l'epoca più recente va in cima.
  const epochRows = (epochs.data?.epochs ?? []).slice().reverse();
  const current = epochRows.find((e) => e.current);

  // Senza summary.json non si può dichiarare il conto operativo: l'assenza va detta, non riempita.
  const status =
    summary.data === null
      ? `<div class="banner warn">Stato del conto non disponibile: <code>summary.json</code> non risponde${
          summary.error ? ` (${escapeHtml(summary.error)})` : ""
        }. Il reset resta comunque possibile: è un workflow su GitHub e non dipende da questa pagina.</div>`
      : dead
        ? `<div class="banner bad">Il conto è azzerato: il trading è fermo e riparte solo dopo il reset.</div>`
        : `<div class="banner info">Il conto è operativo (equity ${money(acct.equity)}). Il reset è disponibile in qualsiasi
           momento, con conferma.</div>`;

  const stateStrip = `<div class="strip">
    ${stat("Epoca", contracts(acct.epoch))}
    ${stat("Equity", withUnit(money(acct.equity)))}
    ${stat("P&L epoca", withUnit(signedUsd(acct.pnl_total)), tone(acct.pnl_total))}
    ${stat("Capitale iniziale", withUnit(notional(initial)))}
    ${stat("Operazioni", contracts(current?.n_trades))}
    ${stat("Aperta il", stamp(current?.started_ts))}
  </div>`;

  const tokenState = `<span class="chip ${token ? "good" : ""}" id="token-state">${
    token ? "token salvato" : "nessun token"
  }</span>`;

  const form = `<p style="margin-top:0">Con un token fine-grained salvato in questo browser (permesso <em>Actions: write</em> solo su questo
     repository) il pulsante avvia il workflow direttamente. Senza token, il pulsante apre la pagina del workflow
     su GitHub, dove parte con un clic.</p>
    <div style="display:grid;gap:8px;margin-top:14px">
      <label class="label" for="token">Token fine-grained</label>
      <p class="muted" style="margin:0">Resta solo nel tuo browser, non viene mai inviato altrove.</p>
      <input id="token" type="password" placeholder="github_pat_…" value="${escapeHtml(token)}" autocomplete="off"
        spellcheck="false" />
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn secondary sm" id="btn-save-token">Salva token</button>
        <button class="btn secondary sm" id="btn-clear-token">Rimuovi token</button>
      </div>
    </div>
    <div style="display:grid;gap:8px;margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
      <label class="label" for="confirm">Scrivi ${CONFIRM} per confermare</label>
      <p class="muted" style="margin:0">Il pulsante resta disattivato finché il campo non contiene esattamente
        <code>${CONFIRM}</code>.</p>
      <input id="confirm" type="text" placeholder="${CONFIRM}" autocomplete="off" spellcheck="false" />
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:2px">
        <button class="btn danger" id="btn-reset" disabled>Reset a ${notional(initial)}</button>
        <a class="btn secondary" href="${escapeHtml(workflowUrl())}" target="_blank" rel="noopener">Apri il workflow su GitHub</a>
      </div>
    </div>
    <div id="reset-result" style="margin-top:12px" aria-live="polite"></div>`;

  el.innerHTML =
    pageTitle("Reset del conto", `Riporta il conto a ${notional(initial)} archiviando l'epoca corrente`) +
    originBanner(epochs, epochs.data?.generated_at) +
    status +
    card("Epoca corrente", stateStrip, src("engine/broker", acct.asof)) +
    card(
      "Come funziona",
      `<p style="margin-top:0">Il reset è un workflow GitHub (<code>reset.yml</code>), non un'azione del browser: così lo stato
       ufficiale resta uno solo ed è tracciato. Il workflow archivia l'epoca corrente in <code>epochs.json</code>,
       chiude le posizioni e riparte da ${notional(initial)}.</p>
       <p class="muted" style="margin:8px 0 0">Lo storico non viene riscritto: ogni epoca archiviata conserva equity iniziale,
       massima, minima e finale e il numero di operazioni, e resta nella tabella qui sotto.</p>
       <p class="muted" style="margin:8px 0 0">Servono i permessi di scrittura dei workflow nel repository
       ${escapeHtml(config.owner)}/${escapeHtml(config.repo)}.</p>`,
    ) +
    card("Avvia il reset", form, tokenState) +
    card("Storico delle vite del conto", epochTable(epochRows), src("engine/live", epochs.data?.generated_at));

  const confirmInput = document.getElementById("confirm") as HTMLInputElement | null;
  const resetBtn = document.getElementById("btn-reset") as HTMLButtonElement | null;
  const tokenInput = document.getElementById("token") as HTMLInputElement | null;
  const tokenChip = document.getElementById("token-state");
  const result = document.getElementById("reset-result");

  confirmInput?.addEventListener("input", () => {
    if (resetBtn) resetBtn.disabled = confirmInput.value.trim() !== CONFIRM;
  });
  document.getElementById("btn-save-token")?.addEventListener("click", () => {
    if (tokenInput && tokenInput.value.trim()) {
      writeToken(tokenInput.value.trim());
      paintTokenChip(tokenChip, true);
      if (result) result.innerHTML = note("Token salvato in questo browser.");
    }
  });
  document.getElementById("btn-clear-token")?.addEventListener("click", () => {
    writeToken(null);
    if (tokenInput) tokenInput.value = "";
    paintTokenChip(tokenChip, false);
    if (result) result.innerHTML = note("Token rimosso.");
  });
  resetBtn?.addEventListener("click", async () => {
    const stored = readToken() || (tokenInput?.value.trim() ?? "");
    if (!stored) {
      window.open(workflowUrl(), "_blank", "noopener");
      if (result)
        result.innerHTML = note(
          `Nessun token: ho aperto la pagina del workflow su GitHub, dove puoi avviarlo con un clic
           (input di conferma: ${CONFIRM}).`,
          "warn",
        );
      return;
    }
    resetBtn.disabled = true;
    if (result) result.innerHTML = note("Invio della richiesta…");
    try {
      const res = await fetch(dispatchUrl(), {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${stored}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs: { confirm: CONFIRM } }),
      });
      if (res.status === 204) {
        if (result)
          result.innerHTML = note(
            `Reset avviato. Il workflow archivia l'epoca e riparte da ${notional(initial)}; la dashboard si
             aggiorna al prossimo ciclo.`,
            "",
            "var(--good)",
          );
      } else {
        const text = await res.text();
        if (result)
          result.innerHTML = note(`GitHub ha risposto ${res.status}: ${escapeHtml(text.slice(0, 300))}`, "bad");
      }
    } catch (err) {
      if (result)
        result.innerHTML = note(
          `Richiesta non riuscita: ${escapeHtml(err instanceof Error ? err.message : String(err))}`,
          "bad",
        );
    } finally {
      resetBtn.disabled = false;
    }
  });
}

/** Lo storico delle epoche. Le unità stanno nelle intestazioni: le celle portano la cifra nuda, così la
 *  colonna resta allineata. L'equity è denaro, quindi due decimali anche quando finiscono in ",00". */
function epochTable(rows: EpochRow[]): string {
  if (!rows.length)
    return empty(
      "Nessuna epoca archiviata: il conto è alla sua prima vita.",
      "La tabella si apre al primo reset: ogni epoca conserva inizio, fine, equity iniziale, massima, minima e finale, le operazioni e la causa della chiusura.",
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
        <td>${e.current ? '<span class="chip info">in corso</span>' : escapeHtml(endReason(e.end_reason))}</td>
      </tr>`,
    )
    .join("");
  return `<div class="table-wrap tall"><table class="data">
    <thead><tr>
      <th class="num">Epoca</th>
      <th>Inizio <span class="unit">Europa/Roma</span></th>
      <th>Fine <span class="unit">Europa/Roma</span></th>
      <th class="num">Equity iniziale <span class="unit">$</span></th>
      <th class="num">Equity massima <span class="unit">$</span></th>
      <th class="num">Equity minima <span class="unit">$</span></th>
      <th class="num">Equity finale <span class="unit">$</span></th>
      <th class="num">Operazioni</th>
      <th>Esito</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}

/** I messaggi del form vivono dentro la card: non portano il margine inferiore del banner di pagina. */
function note(html: string, cls = "", accent = ""): string {
  const style = `margin-bottom:0${accent ? `;border-left-color:${accent}` : ""}`;
  return `<div class="banner ${cls}" style="${style}">${html}</div>`;
}

function paintTokenChip(chip: HTMLElement | null, saved: boolean): void {
  if (!chip) return;
  chip.className = `chip ${saved ? "good" : ""}`;
  chip.textContent = saved ? "token salvato" : "nessun token";
}

/** localStorage può lanciare (Safari in navigazione privata): un token irraggiungibile vale come assente,
 *  e in quel caso il pulsante apre GitHub invece di fallire in silenzio. */
function readToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeToken(value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(TOKEN_KEY);
    else localStorage.setItem(TOKEN_KEY, value);
  } catch {
    /* niente da salvare: il flusso senza token resta valido */
  }
}

/** In tabella una cifra assente è un trattino, non la "n/d" delle tile. */
function cell(formatted: string): string {
  return formatted === EMPTY ? DASH : formatted;
}

function endReason(value: string | null | undefined): string {
  if (value === "reset_manual") return "Reset manuale";
  if (value === "dead") return "Conto azzerato";
  return value ?? DASH;
}
