# Dashboard (GitHub Pages)

Sito statico in italiano, mobile-first, tema scuro. Non contiene logica di trading: legge i JSON che il motore
pubblica e li mostra con fonte e orario accanto a ogni numero.

## Comandi

```bash
npm ci
npm run dev        # server di sviluppo su http://localhost:5173 (usa dev-sample/, banner rosso "DATI DI ESEMPIO")
npm run typecheck  # tsc --noEmit, strict
npm run build      # build statica in dist/
npm run preview    # anteprima della build
```

## Da dove arrivano i dati

1. **Live**: `https://raw.githubusercontent.com/<owner>/<repo>/data/site-data/<file>` con un parametro
   anti-cache a bucket di 5 minuti (la CDN di raw ha una cache di pochi minuti, coerente con il ciclo da 30).
2. **Copia inclusa**: `./site-data/<file>` dentro la build, copiata dal branch dati dal workflow di deploy. Serve
   quando il branch dati non risponde; la pagina lo dichiara con un banner.
3. **Esempio (solo in sviluppo)**: `dev-sample/<file>`. Questi file hanno `"sample": true` e ogni titolo prefissato
   `ESEMPIO`; stanno **fuori** da `public/`, quindi non possono finire nella build di produzione.

Se tutte le fonti mancano la pagina scrive «Dati non disponibili»: non mostra mai un numero inventato.

Override a build time: `VITE_OWNER`, `VITE_REPO`, `VITE_DATA_BRANCH`, `VITE_DATA_DIR`, `VITE_BASE`.

## Pagine

| Rotta | Contenuto |
|---|---|
| `#/` | Prezzo Brent con fonte e orario, OVX, badge del regime, stato dati, equity, P&L, drawdown, posizione, leva con il motivo del limite, prezzo di liquidazione, equity contro buy & hold |
| `#/previsioni` | Fan chart a 1 giorno / 1 settimana / 1 mese / 3 mesi, quantili, probabilità di rialzo, driver, range implicito nell'OVX, track record contro random walk e curva |
| `#/strategie` | Classifica dei conti ombra (Sharpe, Sortino, max drawdown, hit rate, profit factor, segnale) e scheda per strategia |
| `#/mercato` | Curva dei futures, storico dei regimi, scorte contro il range a 5 anni, Brent−WTI, COT, indice geopolitico, calendario eventi |
| `#/operazioni` | Log delle operazioni con motivazione, slippage, gate e leva; export CSV |
| `#/rischio` | Margini, storico della leva, VaR ed ES, rischio di rovina Monte Carlo, stress test |
| `#/reset` | Spiegazione, token opzionale nel browser, avvio del workflow di reset, storico delle epoche |

## Convenzioni di presentazione

- Ogni valore porta una chip `ⓘ fonte` con fonte e data del dato.
- Le approssimazioni (curva proxy, opzioni sintetiche, range OVX) sono marcate `≈` e spiegate.
- I numeri sono formattati in italiano (`Intl.NumberFormat('it-IT')`), con la virgola decimale.
- La palette dei grafici è quella validata dallo skill di data visualization: contrasto e separazione per
  daltonismo verificati con lo script del validatore sulla superficie scura effettiva del sito.
- Grafici finanziari con TradingView Lightweight Charts v5; curva, fan chart, banda stagionale e storico dei
  regimi sono SVG scritti a mano (nessuna seconda libreria di grafici).

## Pubblicazione

Il workflow `.github/workflows/deploy.yml` costruisce il sito e lo pubblica su GitHub Pages. Perché funzioni:

1. **Settings → Pages → Source: GitHub Actions** (una sola volta, va fatto a mano).
2. Repository **pubblico**, altrimenti Pages richiede GitHub Pro e `raw.githubusercontent.com` richiederebbe un
   token anche per la lettura dei JSON.
