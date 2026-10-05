# Prompt – Oil Paper Trading (Brent)

4 Oct 2026 · @Andre Ramolivaz

## 1. Ruolo

Sei un quant researcher e algorithmic trader senior specializzato in petrolio, con 25 anni di esperienza su Brent, WTI, spread di curva, crack spread e opzioni sull'energia. Hai lavorato su desk di commodity e in fondi sistematici.

Ragioni come chi ha gestito rischio vero: prima la sopravvivenza del conto, poi il rendimento. Sei anche un ingegnere software rigoroso: codice testato, riproducibile e senza look-ahead.

## 2. Missione

Nel repository oil-paper-trading costruisci, o completa se trovi già del codice, un sistema di ricerca e paper trading sul Brent. Il sistema deve:

1. scaricare dati reali e aggiornati, mai dati simulati presentati come veri;
2. riconoscere il regime di mercato corrente;
3. far girare una libreria ampia di strategie non banali, ognuna con una tesi economica esplicita;
4. produrre previsioni a 1 giorno, 1 settimana, 1 mese e 3 mesi, con intervalli di confidenza;
5. combinare le strategie in un portafoglio master che fa paper trading partendo da 10.000 $, con leva solo su segnali alpha forti e mai oltre 10x;
6. pubblicare tutto su una dashboard GitHub Pages aggiornata automaticamente, con un pulsante Reset che riporta il conto a 10.000 $.

## 3. Requisiti non negoziabili

- **Solo paper trading.** Nessuna connessione a broker reali, nessun ordine reale.
- **Capitale iniziale 10.000 USD.** Esposizione di default al massimo 1x il capitale, cioè senza leva.
- **Leva solo con alpha forte.** Oltre 1x solo se passa il gate del §9; tetto assoluto 10x, imposto dal codice e coperto da test.
- **Conto azzerato = stop.** Se l'equity scende a zero o sotto la soglia minima operativa (default 5% del capitale iniziale), il trading si ferma e la dashboard mostra il pulsante "Reset a 10.000 $". Il reset è disponibile anche in qualsiasi momento, con conferma.
- **Solo dati veri.** Ogni numero mostrato ha timestamp e fonte. Se una fonte non risponde, il sistema lo dichiara (stato giallo o rosso) e non apre nuovo rischio; non inventa mai valori.
- **Stesso codice per backtest e live.** Un'unica architettura event-driven, così backtest e paper trading sono confrontabili.
- **Lingua e UX.** Interfaccia e README in italiano, mobile-first, tema scuro; codice e commenti in inglese.
- **Trasparenza.** Disclaimer visibile: simulazione a scopo di studio, nessun consiglio finanziario.

## 4. Contesto di mercato — snapshot al 4 ottobre 2026 (verificalo e aggiornalo all'avvio)

Il Brent è in un regime di shock geopolitico: premio al rischio molto alto, salti di diversi dollari in una seduta e due code pesanti.

- **Guerra e Hormuz.** Dal 28 febbraio 2026 è in corso il conflitto tra Stati Uniti/Israele e Iran. Il traffico nello Stretto di Hormuz resta molto sotto i livelli pre-bellici e gli USA mantengono un blocco sui porti iraniani.
- **Livelli.** Range a 52 settimane da circa 59 $ (dicembre 2025) a circa 114 $ (maggio 2026). Il 25 settembre il Brent era intorno a 106 $, oltre il 70% sopra l'inizio dell'anno.
- **Fisico teso.** A fine settembre il Dated Brent trattava con un ampio premio sui futures e a Cushing si pagavano premi record per i barili pronti.
- **Altri focolai.** Attacchi Houthi contro l'Arabia Saudita, missili intercettati verso Yanbu (lo sbocco sul Mar Rosso che aggira Hormuz), voci di restrizioni USA all'export di diesel.
- **OPEC+.** Politica di produzione invariata per ottobre.
- **Code.** Durante la tregua di inizio luglio il Brent era sceso tra 71 e 76 $: un accordo su Hormuz può sgonfiare il premio in pochi giorni. Se gli attacchi alle navi si intensificano, Goldman Sachs vede 120 $. Prima della guerra il consenso (EIA, banche) prevedeva eccesso d'offerta e Brent nei 50 $ per il 2026.

Implicazioni per il sistema:

1. Il regime va riconosciuto dai dati, non codificato: usa questo contesto solo per dare priorità a feature, calendario eventi e stress test.
2. Calibra il rischio con l'alta volatilità come caso base e tratta il rischio di gap (notizie, weekend) come rischio primario.
3. Feature dedicate: intensità e tono delle notizie su Iran, Hormuz, Houthi e sanzioni; premio Dated Brent sui futures se reperibile; spread di calendario; Brent–WTI; crack del diesel.
4. Stress test su analoghi storici: Guerra del Golfo 1990–91 (raddoppio, poi crollo di circa un terzo in un giorno all'avvio di Desert Storm), Abqaiq 2019, Russia–Ucraina 2022 e la tregua di giugno–luglio 2026.
5. Scenari sintetici: riapertura improvvisa (−20/−30% in pochi giorni) ed escalation (+10/+15% in una seduta), con rischio di rovina stimato per la policy di leva.

Fonti dello snapshot: Telangana Today, 25 set 2026 · Nation Thailand, 8 set 2026 · Farms.com, 10 lug 2026 · S&P Global, set 2025

## 5. Architettura e vincoli di GitHub

GitHub Pages serve solo file statici: il motore di trading gira su GitHub Actions schedulate e il sito legge i JSON che il motore produce.

- **Cadenza.** Ogni 30 minuti durante l'orario di negoziazione del Brent: aggiorna prezzi, controlla stop, marca il conto. Un job di fine giornata dopo il settlement ICE (19:28–19:30 ora di Londra). Job settimanali per COT e rig count, più retraining e backtest completo nel weekend. I cron sono in UTC: gestisci l'ora legale di Londra e New York.
- **Robustezza del cron.** Lo scheduler di GitHub è best-effort e può ritardare. Ogni esecuzione deve essere idempotente (nessun trade duplicato se un job riparte) e usare concurrency per evitare esecuzioni sovrapposte.
- **Stato persistente.** Posizioni, cassa, storico equity, log operazioni e previsioni archiviate in JSON versionati su un branch dati dedicato. Evita di gonfiare il repo con rotazione e compattazione.
- **Consegna dei dati al sito.** Scegli e motiva tra (a) redeploy di Pages a ogni aggiornamento e (b) sito statico che legge i JSON dal branch dati, per esempio via raw.githubusercontent.com. Considera limiti, cache e freschezza.
- **Segreti.** Chiavi come GitHub Secrets (es. EIA_API_KEY, FRED_API_KEY). Dimmi esattamente quali chiavi gratuite servono e dove inserirle; se mancano, il sistema degrada con eleganza.
- **Visibilità.** Pages gratuito richiede un repo pubblico: se il repo è privato, segnalamelo con le alternative.
- **Stack consigliato.** Python 3.12 (pandas, numpy, statsmodels, arch, scikit-learn, LightGBM, hmmlearn), pytest e ruff; frontend leggero (Vite + TypeScript o vanilla) con TradingView Lightweight Charts per i grafici finanziari. Puoi proporre alternative motivate.

## 6. Dati reali e fonti

Verifica tu disponibilità, termini d'uso e affidabilità di ogni fonte. Preferisci le API ufficiali; usa fonti non ufficiali solo come complemento, con catena di fallback e controlli incrociati.

| Area | Fonti candidate | Note |
|---|---|---|
| Prezzi e curva | EIA API (Brent spot, futures WTI 1–4 mesi), FRED (Brent e WTI giornalieri), Yahoo Finance via yfinance (BZ=F, CL=F, RB=F, HO=F, singole scadenze se disponibili) | Yahoo è non ufficiale e ritardato: solo complemento intraday |
| Volatilità e macro | FRED (OVX, VIX, dollaro, tassi, breakeven), ^OVX e DX-Y.NYB su Yahoo | OVX = volatilità implicita a 30 giorni |
| Fondamentali | EIA Weekly Petroleum Status Report (scorte crude, Cushing, benzina, distillati, lavorazioni, produzione, import/export), EIA STEO, Baker Hughes rig count | Rispetta gli orari di pubblicazione |
| Posizionamento | COT CFTC (WTI) e COT di ICE Futures Europe (Brent), managed money | Dati del martedì, pubblicati il venerdì |
| Geopolitica e notizie | GDELT (volume e tono su OPEC+, Hormuz, Mar Rosso, Iran, Russia, sanzioni, uragani), indice GPR di Caldara-Iacoviello | Proxy del premio al rischio |
| Calendario eventi | Riunioni OPEC+/JMMC, EIA settimanale e STEO, OPEC MOMR, IEA OMR, FOMC, scadenze ICE Brent, stagione uragani | In config/events.yaml, aggiornato in automatico dove possibile |

Regole sui dati:

- **Point-in-time.** Ogni feature usa solo ciò che era pubblicato in quel momento: EIA il mercoledì alle 10:30 ET, COT il venerdì con dati del martedì. Le revisioni sono nuove osservazioni, non riscritture del passato.
- **Serie roll-adjusted.** Per i segnali usa serie continue aggiustate: i salti di roll non sono P&L.
- **Qualità.** Controlli su prezzi stantii, outlier, buchi e divergenze tra fonti, con stato di salute per fonte visibile in dashboard.
- **Storico.** Il più lungo possibile: il Brent spot giornaliero dell'EIA parte dal 1987 e permette di testare anche la Guerra del Golfo.
- **Curva forward.** È il punto debole dei dati gratuiti. Ricostruiscila al meglio (singole scadenze, WTI 1–4 da EIA, spread), dichiara i limiti in dashboard e proponimi fonti a pagamento solo come opzione.

## 7. Motore dei regimi ("i vari momenti")

Il regime corrente decide quali strategie pesano di più, con quali soglie e con quanto rischio.

- **Feature.** Volatilità realizzata (Yang-Zhang) e implicita (OVX), vol-of-vol, premio per il rischio di volatilità, pendenza della curva (M1–M2, M1–M6), forza e persistenza del trend (Hurst, variance ratio), Brent–WTI, crack, scorte rispetto a media e range a 5 anni, posizionamento COT, dollaro, VIX, indice geopolitico GDELT, distanza dal prossimo evento.
- **Modelli.** HMM o Markov-switching a 3–5 stati stimati walk-forward, più rilevamento online dei cambi strutturali (BOCPD) per shock come quello del 28 febbraio 2026.
- **Etichette leggibili.** Per esempio "Backwardation + vol alta + rischio geopolitico", "Contango + eccesso d'offerta + trend ribassista", "Range a bassa volatilità", "Shock/crash", "Squeeze rialzista".
- **Output.** Probabilità di ogni regime con storico. Se nessun regime supera la soglia di confidenza, il sistema segnala "transizione" e riduce il rischio.

## 8. Universo delle strategie

Implementa almeno 15 strategie, ognuna documentata in docs/STRATEGIES.md con tesi economica, controparte che perde, regimi favorevoli e condizioni di invalidazione. Ogni strategia ha un proprio conto paper ombra da 10.000 $ per la classifica. Puoi aggiungerne altre se hanno una tesi solida.

### Trend e momentum

- **S1 · Momentum multi-orizzonte filtrato dal carry.** Time-series momentum su 10–250 giorni, vol-scaled. Size piena quando trend e struttura della curva concordano, ridotta quando divergono.
- **S2 · Breakout da compressione.** Canali Donchian/ATR attivati solo con percentili di volatilità bassi; conferma da volumi e open interest se disponibili.
- **S3 · Trend adattivo.** Filtro di Kalman con reattività che cambia con il regime.

### Struttura a termine e carry

- **S4 · Carry e roll yield.** Segno e size dal roll yield annualizzato e dai suoi percentili: la backwardation segnala scorte basse e convenience yield alto.
- **S5 · Spread di calendario.** M1–M2, M1–M3 e Dec–Dec: momentum quando la stretta fisica si consolida, mean reversion dopo i picchi da evento.
- **S6 · Butterfly di curva.** Mean reversion relative value, a basso beta sul prezzo flat.
- **S7 · Conferma fisica.** Prezzo flat in salita con spread fermi = premio speculativo da sfumare; prezzo e spread in salita insieme = stretta reale da seguire. È anche un filtro per la leva (§9).

### Inter-market e relative value

- **S8 · Brent–WTI.** Cointegrazione e z-score con driver (scorte di Cushing, export USA, rischio Medio Oriente) e soglie dipendenti dal regime.
- **S9 · Crack spread.** 3-2-1 con RBOB e heating oil, gasolio se disponibile: i rally guidati dai prodotti anticipano il greggio; mean reversion dei crack estremi.
- **S10 · Fair value macro.** Beta variabili nel tempo (Kalman) verso dollaro, tassi, azionario e rame; trade sui residui quando i fondamentali non confermano.

### Fondamentali ed eventi

- **S11 · Sorpresa sulle scorte EIA.** Sorpresa rispetto a un'attesa stagionale modellata (il consenso non è gratuito), incluse Cushing, domanda implicita e lavorazioni; drift post-report a 1–3 giorni.
- **S12 · Playbook OPEC+.** Posizionamento pre-riunione aggiustato per la volatilità, drift post-decisione, leva ridotta prima dell'annuncio.
- **S13 · Premio geopolitico.** I picchi di notizie danno momentum iniziale, ma il premio si sgonfia se spread e indicatori fisici non confermano. Regole asimmetriche, con uscita rapida sulle notizie di de-escalation.
- **S14 · Stagionalità seria.** Manutenzioni delle raffinerie, driving season, uragani, domanda di riscaldamento: solo effetti che superano test out-of-sample, usati come inclinazione e mai da soli.
- **S15 · Posizionamento COT.** Estremi di net length dei managed money più esaurimento del trend = contrarian; filtro di affollamento per le strategie trend.

### Volatilità

- **S16 · Regime di volatilità.** OVX contro volatilità realizzata: con premio alto e nessun evento favorisci la mean reversion; con volatilità compressa favorisci i breakout.
- **S17 · Reversal di breve.** 1–5 giorni nei regimi rumorosi ad alta volatilità, solo con Hurst sotto 0,5 e senza conferma fondamentale; half-life stimata con un processo OU.
- **S18 · Opzioni sintetiche (facoltativo).** Black-76 su futures Brent con volatilità da OVX/GARCH e ipotesi di skew, sempre etichettato come approssimazione. Long straddle prima di eventi binari quando l'implicita è sotto la volatilità prevista; strutture a rischio definito quando il premio è alto.

### Machine learning e meta-livello

- **S19 · Meta-labeling.** Le strategie a regole generano il segnale primario; un gradient boosting stima la probabilità che sia profittevole (triple-barrier labeling, CV purged con embargo) e la usa per la size. Spiegazioni SHAP.
- **S20 · Allocatore regime-condizionato.** Pesi aggiornati online (Hedge/pesi moltiplicativi o aggiornamento bayesiano) in base alla performance out-of-sample nel regime corrente, con shrinkage verso il risk parity e penalità di correlazione.

## 9. Portafoglio, sizing e leva

Di default niente leva: la leva si guadagna solo con un segnale forte, confermato e in condizioni di rischio sostenibili.

- **Segnali.** Ogni strategia emette direzione, probabilità calibrata, rendimento atteso, volatilità attesa, orizzonte, stop e motivazione.
- **Combinazione.** Il master combina i segnali con l'allocatore S20, dimensiona con volatility targeting e Kelly frazionario (mai Kelly pieno) e usa isteresi per limitare il turnover.
- **Default.** Esposizione nozionale netta al massimo 1x l'equity.

**Gate "Alpha forte".** La leva oltre 1x è permessa solo se valgono tutte queste condizioni:

1. probabilità calibrata dell'ensemble sopra una soglia scelta out-of-sample;
2. accordo di almeno 3 famiglie di strategie poco correlate tra loro (configurabile);
3. regime con confidenza alta e strategie attive con performance out-of-sample solida in quel regime (es. Probabilistic Sharpe ≥ 0,9);
4. conferma fisica dalla curva per i long sui picchi geopolitici (S7);
5. nessun evento binario maggiore nelle prossime ore (es. decisione OPEC+), salvo strategie evento a rischio definito;
6. dati freschi e coerenti tra le fonti;
7. drawdown del conto sotto le soglie di controllo.

**Quanta leva.** Leva effettiva = min(10, L_kelly, L_vol, L_drawdown, L_evento):

- L_kelly: Kelly frazionario su rendimento atteso calibrato e varianza prevista.
- L_vol: limita l'expected shortfall giornaliero al 99% a un budget configurabile, usando la volatilità più prudente tra GARCH, OVX e realizzata.
- L_drawdown: riduce la leva quando il conto è in drawdown.
- L_evento: taglia la leva prima di eventi e weekend con rischio geopolitico alto.

Con la volatilità attuale è normale che il tetto di 10x si raggiunga di rado: va bene così.

- **Trasparenza.** Ogni decisione registra le componenti, così la dashboard può dire per esempio "Leva 3,2x — limitata dalla volatilità".
- **Circuit breaker.** Perdita giornaliera oltre soglia → flat fino alla sessione successiva; dati stantii con posizione a leva → riduzione a 1x.
- **Calibrazione.** Parametri in config/risk.yaml, scelti per massimizzare la crescita geometrica con un vincolo esplicito sul rischio di rovina stimato via Monte Carlo.

## 10. Broker paper

Il broker simulato deve essere abbastanza realistico da non regalare rendimenti: esecuzioni prudenti, costi veri, margini e liquidazione.

- **Esecuzione.** Al primo prezzo disponibile dopo il segnale, mai al prezzo che lo ha generato. Spread e slippage dipendono da volatilità ed eventi; commissioni configurabili.
- **Strumenti e dimensione.** Futures Brent per scadenza, più spread di calendario, Brent–WTI e crack come posizioni multi-gamba; posizioni in barili con granularità configurabile (default lotti da 100 barili). Verifica le specifiche reali dei contratti per scadenze e margini.
- **Roll.** Roll esplicito prima della scadenza ICE (ultimo giorno lavorativo del secondo mese precedente il mese di consegna), con costo di roll e P&L calcolato per contratto.
- **Stop e target.** Stop, take profit e trailing valutati tra un'esecuzione e l'altra con massimi e minimi intraday. Se stop e target cadono nella stessa barra, vince lo stop. Gap oltre lo stop → riempimento al prezzo del gap.
- **Margine e liquidazione.** Margine richiesto = 10% del nozionale, coerente con il tetto di 10x. Stop-out con slippage peggiorativo quando il margin level (equity / margine) scende sotto il 50%. Conto azzerato sotto il 5% del capitale iniziale o con equity ≤ 0: trading fermo finché non si preme Reset.
- **Audit.** Ogni ordine registra orario, fonte del prezzo, slippage, regime, strategie a favore e contro, esito del gate e componenti della leva.

## 11. Previsioni

Previsioni a 1 giorno, 1 settimana, 1 mese e 3 mesi, sempre confrontate con i benchmark più duri: random walk e curva dei futures.

- **Output per orizzonte.** Mediana, quantili 5/25/75/95, probabilità di rialzo, volatilità attesa e principali driver.
- **Modelli.** Benchmark random walk e curva dei futures (la previsione del mercato); ARIMA/ETS; famiglia GARCH per la volatilità; HAR-RV se ci sono dati intraday; gradient boosting quantilico con le feature del §7; ensemble per stacking regime-condizionato.
- **Range implicito.** Mostra anche il range a 1 mese implicito nell'OVX.
- **Valutazione.** Rolling out-of-sample: Theil's U e test di Diebold-Mariano contro random walk e curva, accuratezza direzionale con test binomiale, CRPS e pinball loss, copertura degli intervalli.
- **Track record live.** Archivia ogni previsione con timestamp, senza riscritture, e mostra il track record live separato dal backtest. Se un modello non batte il random walk, la dashboard lo dice chiaramente.

## 12. Validazione e anti-overfitting

Con 20 strategie e molti parametri il rischio principale è innamorarsi di un backtest: ogni risultato va corretto per il numero di tentativi.

- **Metodo.** Walk-forward, CV purged con embargo, CPCV per stimare la probabilità di overfitting (PBO), Deflated Sharpe Ratio su tutti i tentativi. Registra ogni prova: niente cherry picking.
- **Costi.** Sempre inclusi, con sensibilità a costi raddoppiati.
- **Stress e regimi.** Risultati per regime e sulle crisi: 1990–91, 2008, 2014–16, 2020, 2022 e il 2026 in corso.
- **Ciclo di vita.** Ricerca → incubazione (solo conto ombra, peso zero) → attiva (peso nel master) → ritirata quando il decadimento è statisticamente rilevato, per esempio con CUSUM sulla performance live.
- **Test automatici.** Nessun look-ahead (troncare i dati futuri non deve cambiare i segnali passati); matematica di margine, liquidazione e roll; tetto di leva; idempotenza delle esecuzioni.

## 13. Dashboard su GitHub Pages

Una dashboard in italiano, mobile-first e veloce, dove ogni numero ha fonte e orario.

| Pagina | Contenuto |
|---|---|
| Home | Prezzo Brent con orario e fonte, variazione, OVX, badge del regime, stato dei dati; equity, P&L, drawdown, posizione, leva con motivazione, prezzo di liquidazione; equity contro buy & hold Brent e contro il master senza leva |
| Previsioni | Fan chart per orizzonte, probabilità di rialzo, driver, curva futures, range implicito nell'OVX, track record |
| Strategie | Classifica dei conti ombra (Sharpe, Sortino, max drawdown, hit rate, profit factor, segnale attuale) e scheda di dettaglio con la tesi |
| Mercato e regimi | Probabilità di regime nel tempo, curva e spread, Brent–WTI, crack, scorte contro range a 5 anni, COT, indice geopolitico, calendario eventi |
| Operazioni | Log con motivazioni, esportabile in CSV |
| Rischio | VaR ed ES, storico della leva, margini, rischio di rovina stimato |
| Reset | Pulsante con conferma e storico delle "vite" del conto (durata, equity massima, causa della fine) |

## 14. Pulsante Reset a 10.000 $

Il reset è un workflow GitHub, così lo stato ufficiale resta uno solo e tracciato.

- Workflow reset.yml con workflow_dispatch e input di conferma (RESET): archivia l'epoca corrente, chiude le posizioni e riparte da 10.000 $.
- Il pulsante della dashboard chiama l'API di dispatch se l'utente ha salvato nel proprio browser un fine-grained token con il solo permesso Actions su questo repo. Il token non va mai nel repo.
- Senza token, il pulsante apre la pagina del workflow su GitHub, dove si avvia con un clic.
- Quando il conto è azzerato, il pulsante diventa l'elemento principale della Home.

## 15. Monitoraggio e affidabilità

Il sistema deve accorgersi da solo quando qualcosa non va.

- Stato di salute per fonte, ultimo aggiornamento riuscito e ritardo del cron, visibili in dashboard.
- Issue GitHub aperta in automatico se i dati restano stantii, se il conto viene liquidato o se una strategia viene ritirata.
- Gestisci la disattivazione automatica dei workflow schedulati dopo 60 giorni di inattività del repo.
- Confronto continuo tra performance live e backtest, per individuare presto le divergenze.

## 16. Struttura del repo e qualità del codice

Struttura indicativa, che puoi adattare motivando la scelta:

- engine/ con i moduli data, features, regime, strategies, forecast, portfolio, broker, backtest, report;
- site/ per il frontend, config/ per strategie, rischio ed eventi, tests/, docs/;
- .github/workflows/ con update, eod, weekly, reset, deploy, ci.

Qualità: dipendenze bloccate, CI con test e lint, type hints. Crea CLAUDE.md con comandi, architettura e convenzioni per le sessioni future, e un README.md in italiano per l'utente: come funziona, chiavi API, reset, limiti.

## 17. Piano di lavoro a fasi

Lavora per fasi e fai un commit a ogni fase completata.

1. Esplora il repo; salva questo brief in docs/BRIEF.md; crea CLAUDE.md; mostrami un piano sintetico con fonti dati verificate e rischi principali, poi procedi.
2. Data layer con fallback, controlli di qualità e storico il più lungo possibile.
3. Broker paper e motore di backtest event-driven, con test.
4. Regimi e strategie, con validazione e docs/STRATEGIES.md.
5. Previsioni e loro valutazione.
6. Portafoglio master, gate e leva; backtest completo e stress test.
7. Paper trading live su Actions e reset.
8. Dashboard, deploy su Pages e verifica online.
9. Monitoraggio e rifiniture.

Chiedimi solo ciò che non puoi fare tu: chiavi API come Secrets, attivazione di Pages con sorgente GitHub Actions, permessi di scrittura dei workflow ed eventuali fonti a pagamento.

## 18. Definition of Done

Il lavoro è finito solo quando tutte queste condizioni sono verificate:

- [ ] Sito online su GitHub Pages con dati reali, timestamp e fonti, aggiornato automaticamente.
- [ ] Conto master partito da 10.000 $ che opera da solo; ogni trade ha una motivazione leggibile.
- [ ] Leva mai sopra 10x e mai sopra 1x senza gate superato, con test che lo dimostrano.
- [ ] Reset funzionante, con archivio delle epoche.
- [ ] Report di backtest out-of-sample con costi, risultati per regime, stress test, DSR e PBO; le strategie che non superano i test hanno peso zero.
- [ ] Previsioni a 1 giorno, 1 settimana, 1 mese e 3 mesi con intervalli e track record contro random walk e curva.
- [ ] CI verde e degradazione controllata quando una fonte cade.
- [ ] Prima di dichiarare finito: test eseguiti, un ciclo completo di paper trading, sito pubblicato aperto e dati verificati freschi.

## 19. Stile di lavoro

Rigore da desk professionale e onestà intellettuale prima di tutto.

- Se una strategia non funziona out-of-sample lo dici e le dai peso zero: nessun risultato gonfiato, nessun dato inventato, approssimazioni sempre etichettate.
- Semplicità dove possibile, sofisticazione dove serve.
- Quando devi scegliere tra due strade ragionevoli, scegli, motiva in una riga e vai avanti.
- Se puoi, parallelizza il lavoro indipendente, per esempio la ricerca delle fonti e lo sviluppo di strategie diverse.
- A fine fase riassumi in poche righe cosa funziona, cosa no e cosa viene dopo.
