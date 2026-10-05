/** Reset: spiegazione, token opzionale nel browser, dispatch del workflow e storico delle epoche. */
import { config, dispatchUrl, workflowUrl } from "../config";
import { load } from "../data";
import { dateTime, escapeHtml, num, usd } from "../format";
import { card, empty, pageTitle } from "../components/ui";
import type { EpochsDoc, SummaryDoc } from "../types";

const TOKEN_KEY = "opt_gh_token";

export async function renderReset(el: HTMLElement): Promise<void> {
  const [summary, epochs] = await Promise.all([load<SummaryDoc>("summary.json"), load<EpochsDoc>("epochs.json")]);
  const acct = summary.data?.account ?? {};
  const initial = acct.initial_capital ?? 10000;
  const dead = Boolean(acct.dead);
  const token = localStorage.getItem(TOKEN_KEY) ?? "";

  const status = dead
    ? `<div class="banner bad">Il conto è azzerato: il trading è fermo e riparte solo dopo il reset.</div>`
    : `<div class="banner">Il conto è operativo (equity ${usd(acct.equity)}). Il reset è disponibile in qualsiasi
       momento, con conferma.</div>`;

  const epochRows = (epochs.data?.epochs ?? []).slice().reverse();
  const epochTable = epochRows.length
    ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>Epoca</th><th>Inizio</th><th>Fine</th><th>Equity iniziale</th><th>Equity massima</th><th>Equity finale</th><th>Operazioni</th><th>Causa</th></tr></thead>
        <tbody>${epochRows
          .map(
            (e) => `<tr>
              <td>${num(e.epoch, 0)}${e.current ? ' <span class="chip info">in corso</span>' : ""}</td>
              <td>${dateTime(e.started_ts)}</td>
              <td>${e.ended_ts ? dateTime(e.ended_ts) : "—"}</td>
              <td class="num">${usd(e.start_equity, 0)}</td>
              <td class="num">${usd(e.max_equity, 0)}</td>
              <td class="num">${e.end_equity == null ? "—" : usd(e.end_equity, 0)}</td>
              <td class="num">${num(e.n_trades, 0)}</td>
              <td>${escapeHtml(endReason(e.end_reason))}</td>
            </tr>`,
          )
          .join("")}</tbody></table></div>`
    : empty("Nessuna epoca archiviata: il conto è alla sua prima vita.");

  el.innerHTML =
    pageTitle("Reset del conto", `Riporta il conto a ${usd(initial, 0)} archiviando l'epoca corrente`) +
    status +
    card(
      "Come funziona",
      `<p>Il reset è un workflow GitHub (<code>reset.yml</code>), non un'azione del browser: così lo stato ufficiale
       resta uno solo ed è tracciato. Il workflow archivia l'epoca corrente in <code>epochs.json</code>, chiude le
       posizioni e riparte da ${usd(initial, 0)}.</p>
       <p class="muted">Servono i permessi di scrittura dei workflow nel repository
       ${escapeHtml(config.owner)}/${escapeHtml(config.repo)}.</p>`,
    ) +
    card(
      "Avvia il reset",
      `<p>Con un token fine-grained salvato in questo browser (permesso <em>Actions: write</em> solo su questo
       repository) il pulsante avvia il workflow direttamente. Senza token, il pulsante apre la pagina del workflow
       su GitHub, dove parte con un clic.</p>
       <label class="muted" for="token">Token fine-grained (resta solo nel tuo browser, non viene mai inviato altrove)</label>
       <input id="token" type="password" placeholder="github_pat_…" value="${escapeHtml(token)}" autocomplete="off" />
       <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
         <button class="btn secondary" id="btn-save-token">Salva token</button>
         <button class="btn secondary" id="btn-clear-token">Rimuovi token</button>
       </div>
       <hr style="border:none;border-top:1px solid var(--border);margin:14px 0" />
       <label class="muted" for="confirm">Scrivi RESET per confermare</label>
       <input id="confirm" type="text" placeholder="RESET" autocomplete="off" />
       <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
         <button class="btn danger" id="btn-reset" disabled>Reset a ${usd(initial, 0)}</button>
         <a class="btn secondary" href="${escapeHtml(workflowUrl())}" target="_blank" rel="noopener">Apri il workflow su GitHub</a>
       </div>
       <div id="reset-result" style="margin-top:10px"></div>`,
    ) +
    card("Storico delle vite del conto", epochTable);

  const confirmInput = document.getElementById("confirm") as HTMLInputElement | null;
  const resetBtn = document.getElementById("btn-reset") as HTMLButtonElement | null;
  const tokenInput = document.getElementById("token") as HTMLInputElement | null;
  const result = document.getElementById("reset-result");

  confirmInput?.addEventListener("input", () => {
    if (resetBtn) resetBtn.disabled = confirmInput.value.trim() !== "RESET";
  });
  document.getElementById("btn-save-token")?.addEventListener("click", () => {
    if (tokenInput && tokenInput.value.trim()) {
      localStorage.setItem(TOKEN_KEY, tokenInput.value.trim());
      if (result) result.innerHTML = `<div class="banner">Token salvato in questo browser.</div>`;
    }
  });
  document.getElementById("btn-clear-token")?.addEventListener("click", () => {
    localStorage.removeItem(TOKEN_KEY);
    if (tokenInput) tokenInput.value = "";
    if (result) result.innerHTML = `<div class="banner">Token rimosso.</div>`;
  });
  resetBtn?.addEventListener("click", async () => {
    const stored = localStorage.getItem(TOKEN_KEY) ?? tokenInput?.value.trim() ?? "";
    if (!stored) {
      window.open(workflowUrl(), "_blank", "noopener");
      if (result)
        result.innerHTML = `<div class="banner warn">Nessun token: ho aperto la pagina del workflow su GitHub,
          dove puoi avviarlo con un clic (input di conferma: RESET).</div>`;
      return;
    }
    resetBtn.disabled = true;
    if (result) result.innerHTML = `<div class="banner">Invio della richiesta…</div>`;
    try {
      const res = await fetch(dispatchUrl(), {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${stored}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs: { confirm: "RESET" } }),
      });
      if (res.status === 204) {
        if (result)
          result.innerHTML = `<div class="banner" style="border-color:var(--good)">Reset avviato. Il workflow
            archivia l'epoca e riparte da ${usd(initial, 0)}; la dashboard si aggiorna al prossimo ciclo.</div>`;
      } else {
        const text = await res.text();
        if (result)
          result.innerHTML = `<div class="banner bad">GitHub ha risposto ${res.status}: ${escapeHtml(text.slice(0, 300))}</div>`;
      }
    } catch (err) {
      if (result)
        result.innerHTML = `<div class="banner bad">Richiesta non riuscita: ${escapeHtml(
          err instanceof Error ? err.message : String(err),
        )}</div>`;
    } finally {
      resetBtn.disabled = false;
    }
  });
}

function endReason(value: string | null | undefined): string {
  if (value === "reset_manual") return "Reset manuale";
  if (value === "dead") return "Conto azzerato";
  return value ?? "—";
}
