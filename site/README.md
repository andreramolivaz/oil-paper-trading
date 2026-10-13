# Dashboard (GitHub Pages)

Sito statico **in inglese** (richiesta del proprietario del 13 ottobre 2026; prima era in italiano), mobile-first,
tema scuro. Non contiene logica di trading: legge i JSON che il motore pubblica e li mostra con fonte e orario
accanto a ogni numero. Quattro pagine, nessuna libreria di grafici.

## Comandi

```bash
npm ci
npm run dev        # server di sviluppo su http://localhost:5173 (usa dev-sample/, banner rosso "SAMPLE DATA")
npm run typecheck  # tsc --noEmit, strict
npm run build      # build statica in dist/
npm run preview    # anteprima della build
```

## Da dove arrivano i dati

1. **Live**: `https://raw.githubusercontent.com/<owner>/<repo>/data/site-data/<file>` con un parametro
   anti-cache a bucket di 5 minuti (la CDN di raw ha una cache di pochi minuti, coerente con il ciclo da 30).
2. **Copia inclusa**: `./site-data/<file>` dentro la build, copiata dal branch dati dal workflow di deploy. Serve
   quando il branch dati non risponde; la pagina lo dichiara con un banner.
3. **Esempio (solo in sviluppo)**: `dev-sample/<file>`. Questi file hanno `"sample": true` e la pagina li dichiara con il banner
   «SAMPLE DATA»; stanno **fuori** da `public/`, quindi non possono finire nella build di produzione.

Se tutte le fonti mancano la pagina scrive «Data unavailable»: non mostra mai un numero inventato.

Override a build time: `VITE_OWNER`, `VITE_REPO`, `VITE_DATA_BRANCH`, `VITE_DATA_DIR`, `VITE_BASE`.

## Pagine

| Rotta | Contenuto |
|---|---|
| `#/` | **Il terminale**: mercato (Brent, WTI, BNO, OVX, rame e dollaro, pendenza della curva, Hormuz), previsione per fonte, i libri con equity, leva, tetto, posizione e **perdita su un gap del −20 %** (`stress`), i libri candidati in un gruppo a parte con il loro totale, decisioni ed eseguiti recenti, catene di opzioni lette, riassunto del backtest con i libri insieme (`portfolio`), stato di dati e motore, come leggere. Legge solo `desk.json`. |
| `#/backtest` | Risultato per libro (candidati separati), **i libri insieme** a capitale uguale (nucleo, nucleo sulla finestra dei candidati, tutti) con anno per anno, curve su scala logaritmica contro lo strumento comprato e tenuto, rischio di rovina, sensibilità a costi ed esecuzione, i segnali uno alla volta, anno per anno, simulazione delle opzioni (≈), provenienza dei dati, ciò che è stato scartato (fase 11 e fase 12). Legge `desk_backtest.json`. |
| `#/reset` | Stato dei libri, scelta di che cosa azzerare, token facoltativo nel browser, avvio del workflow, storico delle vite. |
| `#/archive` | **Una pagina** sul primo sistema: conto master (vivo e nel backtest walk-forward), il verdetto della validazione per ognuna delle strategie (Sharpe, a costi doppi, DSR, operazioni), l'etichetta di regime con il suo limite noto, i documenti. Legge `summary.json`, `validation.json`, `strategies.json`. Le sei pagine italiane del primo sistema (`#/conto-storico`, `#/strategie`, `#/previsioni`, `#/mercato`, `#/operazioni`, `#/rischio`) e `#/archivio` reindirizzano qui. |

Il terminale è testo in carattere monospaziato: tabelle senza riquadri, cifre tabulari, il colore usato solo
per il segno (guadagno o perdita) e per lo stato (a posto, attenzione, guasto). Sul telefono la tabella dei
libri diventa un blocco per libro e le righe del registro vanno su due linee. I grafici a linee sono SVG
disegnati alla larghezza reale del contenitore (`src/charts/lines.ts`), senza libreria.

I nomi delle strategie del primo sistema e le etichette di regime arrivano in italiano dal motore (sono
costanti del primo sistema, che non si tocca): la pagina archivio li traduce con una mappa per id
(`pages/archive.ts`). I testi del desk (motivazioni, stati, note) arrivano già in inglese dal motore.

## Convenzioni di presentazione

- Ogni valore porta fonte e data del dato (nel terminale nella nota di sezione, nelle altre pagine la chip `ⓘ`).
- Le approssimazioni (curva proxy, opzioni su modello) sono marcate `≈` e spiegate.
- I numeri sono formattati in inglese (`Intl.NumberFormat('en-US')`): punto decimale, virgola per le migliaia,
  segno meno U+2212, spazio stretto fra cifra e unità. Le date in `en-GB` (giorno mese), gli orari del
  terminale nel fuso del lettore con il nome del fuso accanto, quelli delle tabelle del reset in ora di New York.
- La palette dei grafici è quella validata dallo skill di data visualization: contrasto e separazione per
  daltonismo verificati con lo script del validatore sulla superficie scura effettiva del sito; il settimo
  colore (`--series-7`, agricoltura) è stato aggiunto nella fase 12 con lo stesso criterio.
- Nessuna libreria di grafici: TradingView Lightweight Charts è stata rimossa con le pagine del primo sistema
  (il bundle è passato da circa 300 a 73 kB).

## Pubblicazione

Il workflow `.github/workflows/deploy.yml` costruisce il sito e lo pubblica su GitHub Pages. Perché funzioni:

1. **Settings → Pages → Source: GitHub Actions** (una sola volta, va fatto a mano).
2. Repository **pubblico**, altrimenti Pages richiede GitHub Pro e `raw.githubusercontent.com` richiederebbe un
   token anche per la lettura dei JSON.
