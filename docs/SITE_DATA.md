# Contratto JSON motore → dashboard (`site-data/`)

Il motore scrive questi file (`python -m engine.cli export-site`) sul branch `data`, cartella `site-data/`.
La dashboard li legge da `https://raw.githubusercontent.com/<owner>/<repo>/data/site-data/<file>` con fallback
alla copia inclusa nel build. Ogni file ha `generated_at` (UTC ISO) e ogni numero ha `source` e `asof`.
Valori approssimati portano `approx: true`. Nessun campo viene mai riempito con valori inventati: se manca è `null`.

| File | Contenuto | Aggiornato da |
|---|---|---|
| `summary.json` | Home: prezzo Brent (valore, asof, source, variazione 1g), OVX, regime (label, conf, prob), stato dati (`overall`, per fonte), conto (equity, cash, pnl giorno/totale, drawdown, posizione, leva con componenti e motivo, prezzo di liquidazione, status, epoch), ultimo run (job, ts, ritardo cron), disclaimer | update, eod |
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
