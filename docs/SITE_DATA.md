# Contratto JSON motore → dashboard (`site-data/`)

Il motore scrive questi file (`python -m engine.cli export-site`) sul branch `data`, cartella `site-data/`.
La dashboard li legge da `https://raw.githubusercontent.com/<owner>/<repo>/data/site-data/<file>` con fallback
alla copia inclusa nel build. Ogni file ha `generated_at` (UTC ISO) e ogni numero ha `source` e `asof`.
Valori approssimati portano `approx: true`. Nessun campo viene mai riempito con valori inventati: se manca è `null`.
Il JSON è **stretto**: NaN e Infinity diventano `null` prima della scrittura, perché `JSON.parse` li rifiuta e un
NaN silenzioso in un grafico è peggio di un buco. Un job di CI riesporta da uno stato vuoto e lo verifica.

La leva e l'esito del gate vengono registrati a **ogni** settlement, anche quando non ne segue alcun ordine:
un conto fermo ha comunque un motivo per esserlo, e la dashboard deve poterlo mostrare (`state/decisions-*.jsonl`).

| File | Contenuto | Aggiornato da |
|---|---|---|
| `summary.json` | Home: prezzo Brent (valore, asof, source, variazione 1g), OVX, spot, regime (label, conf, prob, approx), stato dati (`overall`, per fonte), conto (equity, cash, pnl giorno/totale, drawdown, margine, nozionale, posizioni, leva con componenti e motivo, **gate** con le sette condizioni e il loro esito, prezzo di liquidazione, status, epoch, `dead`), ultimo run (job, ts, stato, ritardo cron), blocco `reset` con l'URL del workflow, disclaimer | update, eod |
| `equity.json` | Serie `[{t, master, master_1x, buy_hold_brent, shadows: {S1: …}}]` campionata giornaliera + ultimi 30 giorni intraday | update, eod |
| `forecasts.json` | Per orizzonte `{h1d,h1w,h1m,h3m}`: mediana, q05/q25/q75/q95, p_up, vol attesa, driver, benchmark (random walk, curva), range OVX 1 mese; `track_record`: metriche live vs backtest per modello | eod |
| `strategies.json` | Classifica conti ombra: per strategia id, nome, famiglia, lifecycle, segnale corrente (direzione, prob, rationale), Sharpe, Sortino, maxDD, hit rate, profit factor, n trade, peso nel master, link scheda | eod, weekly |
| `market.json` | Curva (M1..M12 con codici, approx), spread M1-M2/M1-M6/Dec-Dec, Brent–WTI, crack, scorte vs range 5 anni (serie stagionale), COT (serie), indice geopolitico (serie), storico probabilità regime (serie), calendario eventi prossimi | eod, weekly |
| `trades.json` | Ultime 500 operazioni con motivazioni, slippage, regime, strategie pro/contro, gate, componenti leva; `csv_url` punta a `trades.csv` | update, eod |
| `trades.csv` | Export completo operazioni epoca corrente | update, eod |
| `risk.json` | VaR/ES 95/99 (1g), storico leva, margine usato/level, rischio di rovina Monte Carlo (prob, orizzonte, soglia), circuit breaker attivi, stress test (scenari e perdita stimata) | eod, weekly |
| `epochs.json` | Storico vite: epoch, inizio, fine, equity max/min/finale, causa fine, n trade | reset, eod |
| `health.json` | Per fonte: status, ultimo successo, asof dato, latenza, fallback usato, messaggio; `cron_lag_minutes`; `overall` | update, eod |
| `validation.json` | Report backtest OOS: per strategia Sharpe/DSR/PBO/costi x2/per regime/crisi; lifecycle; strategie a peso zero e perché | weekly |
| `desk.json` | **Il terminale.** `market` (Brent, WTI e BNO con prezzo, orario e variazione sulla chiusura precedente; OVX; Hormuz), `market` anche con `copper` e `dollar` (l'ultima chiusura confermata da uno scaricamento del giorno dopo, con la sua data e la fonte); `forecast` per strumento (i sette segnali `trend`, `accel`, `skew`, `carry`, `carry_momentum`, `copper`, `dollar`; `sources` con la media di ciascuna delle tre fonti `prezzo`, `curva`, `macro`; `macro_day` con la data della chiusura di rame e dollaro che la previsione ha letto, sempre precedente a `day`; `combined`, volatilità, pendenza e su quali contratti, `approx`), `books` (quattro: regole, con `short_via` e `short_note` quando il lato short passa da un fondo inverso; equity, P&L di oggi e totale, calo dal massimo, `leverage` ed `exposure` in esposizione al greggio (un fondo inverso conta per il suo multiplo, non per i dollari), tetto di oggi, posizioni con `side` riferito al greggio e `multiplier`, ordini in coda, costi pagati, ultima decisione con la motivazione, curva di equity; per il libro delle opzioni anche strutture aperte, rischio massimo e il blocco `monitor` con la catena letta e la struttura candidata), `decisions`, `fills`, `health` (stato dei dati, ultimo giro, giri nelle 24 ore), `backtest` (riassunto) | tick |
| `desk_backtest.json` | Copia di `state/desk/backtest.json`: per libro statistiche, anni, costi, rovina e curva; stessa cosa a costi doppi e con esecuzione alla stessa chiusura; `sleeves`, una tabella per ogni modo in cui un veicolo è negoziato (`BNO` solo long, `BNO + SCO` con lo short nel fondo inverso, `MCL`), con una riga per segnale (`kind: sleeve`), per fonte (`source`), per la previsione di prima (`before`) e per quella di adesso (`all`); `options` con la simulazione su modello (`approx: true`); provenienza dei rendimenti; `meta.model`, l'impronta delle regole con cui è stato calcolato, e `meta.missing`, le tabelle che mancavano | weekly, `desk-backtest`; un giro lo ricalcola se manca, se le regole sono cambiate o se è arrivata una tabella che mancava |

`decisions` porta per ogni decisione anche `legs` (il fondo inverso: prezzo, quote volute, quote tenute, ordine) e
`order_units`, che è l'ordine sul veicolo o, se si muove solo il lato short, quello sul fondo inverso.

`desk.json` pesa circa 25 kB e viene riscritto a ogni giro. Tutto ciò che il terminale mostra è lì dentro: la
pagina non calcola nulla.

