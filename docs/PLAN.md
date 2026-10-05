# Piano di lavoro — oil-paper-trading

Aggiornato: 2026-10-05. Stato per fase in fondo.

## Decisioni di architettura (con motivazione in una riga)

| Tema | Scelta | Perché |
|---|---|---|
| Linguaggio/stack | Python 3.12 (pandas 3, statsmodels, arch, LightGBM, hmmlearn, shap), pytest, ruff, mypy | Stack suggerito dal brief; verificato installabile e compatibile (lock in `requirements.lock`). |
| Motore unico | `TradingSession` event-driven in `engine/backtest`, usato identico da `engine/live` | Un solo codice per backtest e live: i risultati sono confrontabili e i test coprono entrambi. |
| Stato persistente | Branch `data` con JSON/JSONL + parquet per i raw, rotazione mensile e compattazione settimanale | Mantiene `main` leggero e lo storico versionato; nessuna riscrittura del passato. |
| Consegna al sito | **(b)** sito statico su Pages che legge i JSON dal branch `data` via `raw.githubusercontent.com`, più snapshot incluso nel build come fallback | Pages si redeploya solo quando cambia il frontend; 48 aggiornamenti/giorno non consumano build; cache CDN ~5 min è coerente con la cadenza di 30 min; un errore del motore non abbatte il sito. Richiede repo **pubblico** (raw anonimo + CORS). |
| Cadenza intraday | Un solo cron `*/30 * * * *` con guardia in-job sull'orario ICE (01:00–23:00 Londra, DST via `zoneinfo`) | Più semplice e robusto di cron multipli per ora legale; le run fuori orario escono in pochi secondi. |
| Fine giornata | Cron 18:40 e 19:40 UTC con guardia "è dopo le 19:30 a Londra e non ho ancora fatto l'EOD di oggi" | Copre BST e GMT; idempotente per costruzione. |
| Idempotenza | Chiave `sha1(epoch, decision_ts, instrument, strategy_set)` su ogni ordine; `concurrency` per workflow | Il cron GitHub può ritardare o duplicare; nessun trade doppio. |
| Frontend | Vite + TypeScript vanilla + TradingView Lightweight Charts, tema scuro, mobile-first, italiano | Leggero, nessun framework da mantenere; grafici finanziari nativi. |
| Reset | `reset.yml` con `workflow_dispatch` e input `confirm=RESET`; pulsante del sito con token fine-grained nel `localStorage` oppure link alla pagina del workflow | Stato ufficiale unico e tracciato in `epochs.json`. |
| Strumento master | Future Brent front-month in barili (lotto 100 bbl), roll esplicito 3 giorni prima della scadenza ICE; spread come posizioni multi-gamba | Specifiche ICE verificate; 100 bbl × ~100 $ ≈ capitale iniziale → granularità sensata per 10 000 $. |
| Storico lungo | Brent spot EIA 1987+ per regimi/stress (Golfo 1990–91), BZ=F Yahoo 2007+ per il future, curva archiviata da oggi + proxy WTI C1–C4 EIA | Il più lungo possibile dalle fonti gratuite, con i proxy etichettati. |

## Fonti verificate (dettaglio in `docs/DATA_SOURCES.md`)

Funzionano senza chiave: Yahoo (prezzi, scadenze, OVX, VIX, dollaro, prodotti, intraday 1h×2 anni), CFTC Socrata,
ICE COT CSV (2011+), GPR giornaliero (1985+), GDELT file grezzi, EIA XLS/CSV. Con chiave gratuita: EIA API (spot
1987+, WPSR 1982+), FRED API. Degradate dal container ma attese ok sui runner: FRED CSV, GDELT DOC API, Baker
Hughes. Scartate: Stooq, Nasdaq CHRIS, OPEC XML. La serie futures EIA è cessata ad aprile 2024.

## Rischi principali e mitigazioni

1. **Repo privato** → Pages gratuito non disponibile e `raw.githubusercontent.com` richiede token. Mitigazione:
   rendere il repo pubblico (consigliato: Actions illimitate e Pages gratis); alternative: GitHub Pro, oppure
   Cloudflare Pages/Netlify (gratuiti anche su repo privati) con lo stesso build statico.
2. **Curva forward storica assente** → strategie S4–S7 validate su proxy WTI (1983–2024) e su Brent reale solo dal
   go-live; dichiarato in dashboard; pesi del master prudenti finché il track record live non conferma.
3. **Overfitting con 20 strategie** → walk-forward, CPCV/PBO, Deflated Sharpe su tutti i tentativi registrati;
   le strategie che non superano i test restano in incubazione a peso zero.
4. **Rate limit e fonti instabili** (Yahoo non ufficiale, GDELT 429, Baker Hughes 503) → catena di fallback,
   stato di salute per fonte, nessun nuovo rischio con dati stantii, riduzione a 1x se c'è leva.
5. **Gap risk** (weekend, notizie Hormuz) → L_evento taglia la leva prima di weekend/eventi, stop con riempimento
   al gap, scenari sintetici ±15–30% nel Monte Carlo del rischio di rovina.
6. **Cron GitHub best-effort** → idempotenza, concurrency, guardie orarie, monitor del ritardo in dashboard,
   keepalive contro la disattivazione a 60 giorni.
7. **Snapshot del brief non aggiornato** → i dati reali (29/9) mostrano Brent spot 113,96 $ e future Dec-26 ≈101 $,
   sopra i ~106 $ del brief: il sistema legge sempre i dati, mai il contesto scritto.

## Cosa serve da te (solo ciò che non posso fare io)

1. Decidere la **visibilità del repo** (pubblico consigliato) e attivare **Pages** con sorgente "GitHub Actions"
   (Settings → Pages). Il proxy di questa sessione non permette di toccare le impostazioni Pages/Actions.
2. Inserire i secret `EIA_API_KEY` e `FRED_API_KEY` (gratuiti; il sistema funziona anche senza, in stato giallo).
3. Verificare che in Settings → Actions → General i workflow abbiano **permessi di lettura e scrittura**
   (servono per il branch `data`, le Issue automatiche e il keepalive).
4. Fonti a pagamento: nessuna necessaria; opzionali per Dated Brent e curva completa (Platts/Argus/ICE Data).

## Stato delle fasi

- [x] 1. Esplorazione, brief, CLAUDE.md, piano
- [ ] 2. Data layer
- [ ] 3. Broker paper e backtest
- [ ] 4. Regimi e strategie
- [ ] 5. Previsioni
- [ ] 6. Portafoglio, gate, leva, validazione
- [ ] 7. Live su Actions e reset
- [ ] 8. Dashboard e deploy
- [ ] 9. Monitoraggio e rifiniture
