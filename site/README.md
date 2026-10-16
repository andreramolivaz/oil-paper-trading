# Dashboard (GitHub Pages)

Sito statico **in inglese** (richiesta del proprietario del 13 ottobre 2026; prima era in italiano), mobile-first,
tema scuro. Non contiene logica di trading: legge i JSON che il motore pubblica e li mostra con fonte e orario
accanto a ogni numero. Cinque pagine e una pagina per libro, nessuna libreria di grafici.

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
| `#/` | **Desk** (dalla fase 16): una frase su quanti libri hanno una posizione e sul P&L di ogni gruppo, le posizioni aperte (esposizione come barra centrata sullo zero, valore, P&L aperto, da quando), gli ordini in coda, i libri in gruppi - «Core desk», «Candidates», «Kalshi books», «Claude books» (fase 17, con la lettura del giorno dell'analista in una riga) - con un filtro da cliccare (`?show=`), il P&L di ogni gruppo dall'inizio, gli ultimi eseguiti e, a richiesta, tutti. Legge `desk.json` e, per «tutti gli eseguiti», `books/<id>.json`. |
| `#/book/<id>` | **La pagina di un libro**: cifre, che cosa tiene ora con l'ultima decisione citata (e, per un libro Claude, la lettura dell'analista sul suo mercato con ragioni e rischio), l'equity, tutta la vita corrente in schede (eseguiti, decisioni con le parole del libro, vite), regole e costi. Legge `desk.json` e `books/<id>.json`. |
| `#/signals` | **Che cosa leggono i libri**: mercato, previsione per fonte, indici di Kalshi e i libri Oracle, **l'analista Claude** (`?at=claude`: lo stato, la lettura per mercato con fiducia, ragioni, rischio e libro, il riassunto e le fonti, il costo e la spesa del mese, la regola), allocatore, opzioni, mercati a eventi con le allerte, riassunto del backtest, stato di dati e motore, come leggere. `?at=<sezione>` apre la pagina su una sezione. Legge `desk.json`. |
| `#/backtest` | Risultato per libro (candidati separati; i libri speculativi in righe senza numeri, con il motivo), **i libri insieme** a capitale uguale con anno per anno, curve su scala logaritmica contro lo strumento comprato e tenuto, rischio di rovina, sensibilità a costi ed esecuzione, i segnali uno alla volta, simulazione delle opzioni (≈), provenienza dei dati, ciò che è stato scartato. Legge `desk_backtest.json`. |
| `#/reset` | Stato dei libri, scelta di che cosa azzerare, token facoltativo nel browser, avvio del workflow, storico delle vite. |
| `#/archive` | **Una pagina** sul primo sistema: conto master (vivo e nel backtest walk-forward), il verdetto della validazione per ognuna delle strategie, l'etichetta di regime con il suo limite noto, i documenti. Legge `summary.json`, `validation.json`, `strategies.json`. Le sei pagine italiane del primo sistema e `#/archivio` reindirizzano qui. |

Dalla fase 16 le pagine del desk usano un solo carattere, Atkinson Hyperlegible Next (incluso nel sito,
`@fontsource-variable`), con cifre tabulari nelle colonne: tabelle senza riquadri, il colore usato solo per il segno
(guadagno o perdita), per lo stato (a posto, attenzione, guasto) e per ciò che si clicca (magenta, `--accent`). Sul
telefono le tabelle diventano un blocco per riga, ogni cifra sotto la sua etichetta. I grafici a linee sono SVG
disegnati alla larghezza reale del contenitore (`src/charts/lines.ts`), senza libreria. Il testo che scrive un
modello di linguaggio (il riassunto, le ragioni, le fonti dell'analista Claude) è un dato: sempre in escape, solo
titoli, elenchi e grassetto del riassunto diventano markup, e si collegano solo indirizzi http(s).

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
  colore (`--series-7`, agricoltura) è stato aggiunto nella fase 12 con lo stesso criterio. I colori dei gruppi
  della pagina «Desk» sono i primi quattro, nell'ordine fisso dei gruppi (il quarto, «Claude books», dalla fase 17),
  validati sui due temi.
- Nessuna libreria di grafici: TradingView Lightweight Charts è stata rimossa con le pagine del primo sistema
  (il bundle è passato da circa 300 a 73 kB).

## Pubblicazione

Il workflow `.github/workflows/deploy.yml` costruisce il sito e lo pubblica su GitHub Pages. Perché funzioni:

1. **Settings → Pages → Source: GitHub Actions** (una sola volta, va fatto a mano).
2. Repository **pubblico**, altrimenti Pages richiede GitHub Pro e `raw.githubusercontent.com` richiederebbe un
   token anche per la lettura dei JSON.
