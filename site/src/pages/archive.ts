/** The previous system, kept reachable. Its master account never traded because no strategy passed validation;
 *  its pages still show the research (regimes, forecasts, the 21 shadow strategies) and are refreshed daily. */
import { escapeHtml } from "../format";

const PAGES: [string, string, string][] = [
  ["#/conto-storico", "Conto del sistema precedente", "equity, posizione e leva del conto «master»: fermo per costruzione"],
  ["#/strategie", "Le 21 strategie ombra", "classifica dei conti ombra e scheda di ogni strategia"],
  ["#/previsioni", "Previsioni di prezzo", "ventagli a 1 giorno, 1 settimana, 1 mese, 3 mesi e loro storico"],
  ["#/mercato", "Mercato", "curva dei futures, regimi, scorte, COT, indice geopolitico"],
  ["#/operazioni", "Operazioni del conto master", "registro con motivazioni ed export CSV"],
  ["#/rischio", "Rischio del conto master", "margini, VaR, rovina Monte Carlo, stress test"],
];

export async function renderArchive(el: HTMLElement): Promise<void> {
  el.innerHTML = `<div class="term">
    <section class="t-sec">
      <h2 class="t-h"><span>Archivio</span><small>il sistema precedente, ancora aggiornato ogni giorno</small></h2>
      <p>Il primo sistema faceva girare 21 strategie su conti ombra e ne promuoveva una al conto principale solo dopo sette
      condizioni di validazione. In diciotto anni e mezzo di storico nessuna le ha superate, quindi il conto principale è
      rimasto fermo: per costruzione, non per un guasto. I quattro libri del terminale lo sostituiscono; queste pagine restano
      per la ricerca che contengono.</p>
      <div class="t-scroll"><table class="t-table t-log"><tbody>
        ${PAGES.map(
          ([href, title, note]) =>
            `<tr><td class="t-nowrap"><a href="${href}">${escapeHtml(title)}</a></td><td class="w t-dim">${escapeHtml(note)}</td></tr>`,
        ).join("")}
      </tbody></table></div>
    </section>
  </div>`;
}
