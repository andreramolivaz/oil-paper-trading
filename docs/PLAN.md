# Piano di lavoro — oil-paper-trading

Aggiornato: 2026-10-08. Stato per fase in fondo.

## Che cosa è cambiato l'8 ottobre 2026 (fase 10)

Dopo tre giorni il conto era fermo e non aveva fatto nessuna operazione. Le cause erano quattro (dettaglio in
`docs/RESEARCH.md`, §1) e tre delle decisioni della tabella qui sotto si sono rivelate sbagliate *in esercizio*:

| Decisione originale | Che cosa è successo | Che cosa c'è adesso |
|---|---|---|
| Cron `*/30` con guardia sull'orario | GitHub lo ha eseguito circa quattro volte al giorno | Un lavoro `runner` che resta acceso e fa un giro ogni 30 minuti; il cron (`watchdog`) lo riaccende soltanto |
| Fine giornata su cron alle 18:40 e 19:40 UTC | Partiva dopo la mezzanotte di Londra, chiedeva «oggi» e usciva con «troppo presto» | Il giro stesso rifà la fine giornata dell'ultima data **già chiusa**, a qualunque ora parta |
| Qualsiasi fonte rossa blocca il nuovo rischio | Una tabella facoltativa mai scaricata ha fermato il conto | Solo le fonti in `critical_sources` possono farlo; le altre spengono ciò che le legge |
| Un conto che opera solo con strategie validate | Nessuna validata → conto fermo per costruzione | Quattro libri che comprano una previsione fatta di regole pubblicate (`engine/desk`). Dalla fase 11 una selezione c'è, quattro segnali tenuti su sedici provati, ed è dichiarata con tutti i suoi numeri |

Altre decisioni della fase 10, con il motivo:

- **Strumenti reali.** BNO per il Brent e /MCL per la leva, con lotti, margini e commissioni di Robinhood: su
  quell'intermediario un future sul Brent non esiste. Il vecchio conto operava su un future Brent a passi di
  10 barili che nessuno può comprare.
- **Serie per contratto.** Il prezzo continuo di Yahoo mescola due scadenze vicino al roll; i libri leggono il
  fondo o il singolo contratto, e la tabella dei contratti è cumulativa.
- **Leva come risultato.** Nessun gate: `previsione / 10 × obiettivo di volatilità / volatilità`. Gli
  obiettivi sono frazioni di Kelly, il tetto di 10x resta nel broker.
- **Storia del branch dati compressa ogni settimana.** A 48 giri al giorno la storia di git cresceva di
  decine di megabyte al giorno per file che nessuno rileggerà; il registro che conta sta *dentro* i file.
- **Il runner riparte a ogni push sul motore.** Un lavoro acceso da cinque ore gira sul codice di cinque ore
  prima.
- **I giri cadono ai minuti :18 e :48.** Il flusso dei prezzi è in ritardo di 10-15 minuti: tre minuti dopo la
  fine di una barra ne manca ancora un quarto d'ora. Una barra è «chiusa» quindici minuti dopo la sua fine.
- **Una decisione sostituisce l'ordine in coda.** Sui dati veri dell'8 ottobre due decisioni a mezz'ora di
  distanza avevano comprato due volte lo stesso obiettivo.
- **Si scarica solo ciò che la decisione legge.** Due aggiornamenti completi in un'ora (circa duecento
  richieste) bastano perché Yahoo rifiuti tutto per minuti, e un rifiuto costa al giro le sue barre. Ogni
  libro chiede le proprie tabelle al momento di decidere; tutto insieme una volta dopo la chiusura.
- **Sito a una pagina.** Il terminale sostituisce la home; le sette pagine del primo sistema restano
  raggiungibili dall'archivio. (Dalla fase 16 la home è la pagina «Desk», con una pagina per libro; il resto del
  terminale è la pagina «Signals».)

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
| Frontend | Vite + TypeScript vanilla, grafici SVG scritti a mano, tema scuro, mobile-first; in inglese dalla fase 12 (prima in italiano) | Leggero, nessun framework né libreria di grafici da mantenere; cinque pagine e una per libro, che formattano il JSON e non calcolano nulla (contano solo righe). |
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
5. Facoltativo, dalla fase 17: il secret `ANTHROPIC_API_KEY` (la chiave dell'API di Anthropic) accende l'analista
   Claude e i suoi quattro libri; senza, restano liquidi. Va aggiunto dal proprietario nelle impostazioni del
   repository (Settings → Secrets and variables → Actions), mai incollato in una conversazione o in un file.

## Stato delle fasi

- [x] 1. Esplorazione, brief, CLAUDE.md, piano
- [x] 2. Data layer: 10 adattatori con catena di fallback, store point-in-time con vintage, controlli di
  qualità, serie continua roll-adjusted, salute per fonte. Verificato in esecuzione reale: 23 voci su 25
  senza alcuna chiave API.
- [x] 3. Broker paper e motore event-driven: esecuzione alla barra successiva, costi, margine al 10%,
  stop-out, liquidazione, roll, idempotenza. Una sola `TradingSession` per backtest e live.
- [x] 4. Regimi (HMM walk-forward con filtro causale scritto a mano + BOCPD) e strategie S1-S18 con 18
  schede in `docs/STRATEGIES.md`.
- [x] 5. Previsioni a 1g/1s/1m/3m con 5 modelli, benchmark random walk e curva, valutazione (Theil U,
  Diebold-Mariano, CRPS, pinball, copertura) e archivio append-only.
- [x] 6. Portafoglio master: pesi S20, gate alpha a sette condizioni, componenti della leva, Monte Carlo del
  rischio di rovina, stress test storici e sintetici, report di backtest con DSR e PBO.
- [x] 7. Paper trading live su Actions: cinque job idempotenti, branch dati, reset con archivio delle epoche.
- [x] 8. Dashboard su Pages: sette pagine in italiano, tema scuro, mobile-first, grafici con palette validata.
- [x] 9. Monitoraggio: salute per fonte, ritardo del cron, issue automatiche, keepalive, README in italiano.
- [x] 10. Desk: serie investibili per contratto, tre libri lineari e un libro di opzioni su quotazioni reali,
  backtest con sensibilità a costi ed esecuzione, runner a giro continuo, terminale a una pagina, ricerca
  documentata con le fonti (`docs/RESEARCH.md`).
- [x] 11. Uno Sharpe più alto senza più leva: sedici segnali provati sulle serie dei libri, quattro tenuti
  (accelerazione, skew, trend di rame e dollaro) accanto ai tre che c'erano, combinati per fonte; lato short
  del libro dinamico comprando il fondo inverso SCO; tabelle per segnale e per fonte, prima e dopo, nel
  backtest; il backtest porta l'impronta delle regole e si ricalcola quando cambiano. Gli obiettivi di
  volatilità non sono stati toccati. Prima di andare in linea, una revisione indipendente ha trovato sette
  difetti nel funzionamento dal vivo in condizioni degradate (una tabella in ritardo, una gamba eseguita e
  l'altra no, la soglia di perdita valutata su una quotazione vecchia): corretti e fissati da test
  (`docs/VALIDATION.md`).
- [x] 12. Altri mercati e nuove fonti, dichiarati prima della prova (`docs/RESEARCH.md`, capitolo 8): quattro
  mercati satellite con i fondi di Robinhood sotto le regole del libro prudente (oro, rame, agricoltura
  partono come libri **candidati** accanto ai quattro; il gas non parte per il filtro dichiarato), due fonti
  nuove per il greggio (basis-momentum, indice di rischio geopolitico: nulla, scartate), lo spread di
  calendario misurato (premio di termine reale, non incassabile con questi strumenti). I quattro libri e il loro
  backtest sono identici prima e dopo. Il sito passa all'inglese su richiesta del proprietario, a quattro
  pagine: le sei pagine del primo sistema sono sostituite da una pagina di archivio e la libreria di grafici
  è rimossa. Allo Sharpe sopra 3 chiesto con una strategia sola si è risposto che non esiste su questi
  strumenti e che un backtest che lo mostrasse sarebbe la prova di un errore. Una revisione indipendente
  prima della messa in linea ha trovato i quattro libri identici byte per byte e un difetto che contava (le
  distribuzioni in contanti di un fondo contabilizzate come perdita: corretto, `docs/RESEARCH.md`), più tre
  cose piccole corrette subito (un veicolo che fallisce non ferma più il giro degli altri, le tabelle da mezz'ora
  dei satelliti giudicate come tali, l'impronta del backtest cieca ai testi) e due lasciate: il gruppo intraday
  è scaricato intero (tre richieste in più a giro su un desk senza candidati, che non è il caso in linea) e una
  tabella rossa di un candidato con una decisione in sospeso è ritentata a ogni giro.
- [x] 13. «Dinamico, in base a come va» (13-14 ottobre; `docs/RESEARCH.md`, capitolo 9, scritto prima di ogni
  prova). Sette libri candidati in più sotto le regole del libro prudente: Treasury (TLT) e azioni (SPY) dal
  capitolo 8 D, e cinque temi dell'elettrificazione e dei data center (uranio URA, utilities XLU, litio LIT,
  semiconduttori SMH, energia pulita ICLN); tredici libri insieme Sharpe 1,18 contro 0,93 degli otto, è
  diversificazione e lo si scrive. Un **allocatore** (`engine/desk/allocator.py`, `config/allocator.yaml`) che
  scala l'obiettivo di volatilità e il tetto del weekend di ogni libro, anche dei quattro, per regole
  pubblicate e misurate PRIMA di accenderle: interruttore della bolla (Greenwood-Shleifer-You) acceso, controllo
  del drawdown (Grossman-Zhou) misurato e spento, momentum di strategia scartato, regola dello stretto e ciclo
  di vita (promozione e ritiro sul record live) come politiche dichiarate; la regola dura della taglia diventa
  `previsione/10 × obiettivo × m / volatilità`, il backtest riporta sempre con e senza. Provati e scartati anche
  una previsione di volatilità migliore (HAR-RV, multifrattale) e i pesi con la matrice di correlazione pulita
  (capitolo 8 E). I **mercati a eventi** di Kalshi (`engine/data/adapters/kalshi.py`, `engine/desk/prediction.py`):
  letti una volta al giorno dall'API pubblica, archiviati in `state/desk/kalshi-*.jsonl`, mostrati sul terminale
  come scale di probabilità accanto a ciò che le opzioni implicano e a ciò che il desk misura; nessun libro li
  negozia (nessuna storia su cui provare una regola). Sito: sezioni «Allocator» e «Prediction markets» sul
  terminale, la scala del rischio nella colonna Cap, «With and without the allocator» sulla pagina del backtest.
  Aggiunta il 14 ottobre, su richiesta del proprietario («su Kalshi c'è molto insider trading»): l'allerta di
  attività anomala sui mercati a eventi (`engine/desk/watch.py`, capitolo 9 F), letture ogni ora, una allerta
  per evento e giorno con l'impronta delle operazioni, issue su GitHub; soglie dichiarate, tasso di falsi
  allarmi da misurare nelle settimane a venire; nel weekend un job a orario (`kalshi-weekend.yml`) legge solo
  i mercati mentre il runner dorme.
  Rimandato, con il motivo: il gas naturale con il future /MNG e tabelle per contratto (i fondi UNG/KOLD non
  reggono, capitolo 8); il lato short dei satelliti con SH e TBF (tabelle scaricate, non usate: da misurare); il
  gruppo intraday scaricato intero; una tabella rossa di un candidato ritentata a ogni giro; una regola che
  legga Kalshi, fra un anno, se l'archivio dirà che vale qualcosa.
- [x] 14. Un libro speculativo su Kalshi (15 ottobre 2026, su richiesta del proprietario: «una strategia basata
  su Kalshi a leva, altamente speculativa»; `docs/RESEARCH.md`, capitolo 10, scritto prima della prima
  decisione). «Oracle» (`engine/desk/oracle.py`, `config/books.yaml`): un candidato sul future micro WTI, long e
  short, obiettivo di volatilità 0,50 e tetto 10x, che legge la variazione in una settimana dell'indice di
  de-escalation dei mercati a eventi (lo stretto normale, un incontro USA-Iran, un accordo nucleare entro 90
  giorni, letti sull'archivio del desk la sera prima): −2 di previsione per punto. Un contratto Kalshi non si
  compra a leva: la leva sta sul petrolio, Kalshi dà il verso. Niente backtest (non c'è storia): fuori dal
  replay, dalle tabelle e dall'impronta, mai promosso (la promozione ora richiede il drawdown del backtest, per
  ogni libro), giudicato sui soli eseguiti, prior dichiarato di nessun vantaggio; la decisione registra ogni
  input per il replay sull'archivio fra un anno. Motore: `BookConfig.forecast` (`desk` | `kalshi`),
  `Desk.decide(book_forecasts=...)`, `live.oracle_forecasts`, `report.replayed_books`; sito: il blocco «Kalshi
  index · the Oracle book», l'etichetta «speculative · no backtest», la riga senza numeri nel backtest. I
  quattro libri, i dodici candidati e il loro backtest sono identici prima e dopo. Il pomeriggio del 15, sul
  «non funziona perché mancano dati» del proprietario: l'archivio ha un passato (`engine/desk/backfill.py`,
  `kalshi-backfill.yml`): le candele giornaliere di Kalshi dal giorno in cui ogni mercato è stato aperto
  riempiono i giorni prima che il desk leggesse quei mercati, le letture vive restano; la regola misurata una
  volta sulla storia (capitolo 10 H: Sharpe 1,19 ± 1,7 su 143 sedute, un mese che fa il risultato, la scala
  dichiarata regge) e lasciata com'è; il riesame della scala si ripete a dicembre sul record live.
- [x] 15. Kalshi come riferimento sugli altri mercati (15 ottobre 2026, sera, su «improve su altri mercati e anche
  speculative kalshi sempre come riferimento»; `docs/RESEARCH.md`, capitolo 11, registrato prima di misurare). Il
  catalogo cercato per tema e scaricato dal runner (quattro scarichi di candele): esistono con una storia due
  indici a evento utili agli altri mercati - il percorso della Fed (`KXFEDDECISION`: la variazione attesa del tasso
  sulle riunioni entro 90 giorni, in punti base) e la probabilità di recessione (`KXRECSSNBER`, i mercati annuali
  come scala a 365 giorni) - e nessuno con storia per rame, chip, litio, energia pulita, uranio, agricoltura.
  `oracle.py` è diventato un registro di indici con la stessa regola (lettura, riferimento a una settimana,
  componenti comuni, banda morta, tetto); un libro legge le maniche con un segno e ne fa la media. Quattro libri
  registrati e misurati una volta (solo long, 2x, obiettivo 0,50): equities (SPY) +1,6 ± 1,7 e utilities (XLU)
  +1,7 ± 2,7 partono; bonds (TLT) −1,5 e gold (GLD) −1,7 non partono, per il filtro dichiarato. Le scale di
  fedpath e recession portate da 1 e 2 a 2,5 e 4 con la regola di normalizzazione scritta prima (|F| medio 3,9 e
  4,7 contro 10). Rivisto prima della messa in linea da un agente che non lo aveva visto scrivere (capitolo 11 G:
  la scala annuale che si muoveva da sola, il giorno della riunione, la pagina sul blocco d'errore, l'impronta). Il giro scarica e il backfill riempie le due serie nuove; la pagina mostra un blocco per indice
  con i libri e il segno; i tredici libri replicabili e il backtest sono identici byte per byte. Rimandato,
  con il motivo: il lato short (SH, TBF: fondi inversi non qualificati), le scale sulle stampe mensili (CPI, buste
  paga: un altro tipo di indice, da registrare a parte), un libro sull'oro con la sola Fed (la manica da sola
  misura +1,9, ma il libro registrato era con la de-escalation e non si tolgono maniche dopo aver guardato).
- [x] 16. Il terminale più semplice (16 ottobre 2026, su «sistema il design più semplice, che mi faccia capire in tutto
  quali sono le posizioni aperte, i vari portafogli, con scelte che si cliccano, e vedi tutto lo storico del paper
  trading»). La home è la pagina «Desk»: una frase in alto (quanti libri hanno una posizione, il P&L di ogni gruppo),
  le posizioni aperte con l'esposizione come barra centrata sullo zero, i libri in tre gruppi («Core desk»,
  «Candidates», «Kalshi books») con un filtro da cliccare, il P&L di ogni gruppo dall'inizio, gli ultimi eseguiti e,
  a richiesta, tutti. Ogni libro ha la sua pagina (`#/book/<id>`): cifre, cosa tiene ora con l'ultima decisione,
  equity, e tutta la vita corrente in schede (eseguiti, decisioni con le parole del libro, vite). Ciò che i libri
  leggono è sulla pagina «Signals». Il motore esporta i gruppi con i loro totali, l'esposizione per posizione, il P&L
  aperto delle strutture e un file di storia per libro (`books/<id>.json`, `docs/SITE_DATA.md`); nessun numero di
  nessun libro cambia. Carattere Atkinson Hyperlegible Next, palette della sala nautica di notte, colori dei gruppi
  validati sui due temi.
- [x] 17. L'analista Claude (16 ottobre 2026, su «ho chiamate API gratis Claude 200 $ con la University of Southern
  California: come posso usarle per il progetto, e aprire e chiudere posizioni»; `docs/RESEARCH.md`, capitolo 12,
  registrato prima della prima lettura). Una volta al giorno, nei giorni lavorativi americani dopo le 13:00 UTC, il
  giro chiama l'API di Anthropic due volte (`engine/desk/analyst.py`, `config/analyst.yaml`): la prima cerca le
  notizie sul web e scrive un riassunto dei fatti con le fonti, la seconda legge quel riassunto e dà al greggio,
  all'oro, all'S&P 500 e ai Treasury lunghi una previsione da −20 a +20 per le cinque sedute successive. Quattro
  libri candidati la comprano con le regole di sempre (`claude_oil` sul /MCL long e short; `claude_gold`,
  `claude_equities`, `claude_bonds` su GLD, SPY e TLT solo long); nessun backtest, e nessuno può esistere (il
  modello conosce i prezzi del passato); fuori dal replay e dall'impronta, mai promossi; prior di nessun vantaggio.
  Ogni tentativo archiviato con il riassunto, le fonti, l'uso e il costo (`state/desk/claude-*.jsonl`), mai la
  chiave; un tetto di 20 $ al mese. Sito: il gruppo «Claude books» sulla pagina «Desk», la sezione «Claude
  analyst» sui «Signals» (la lettura per mercato con le ragioni e il rischio, il riassunto e le fonti, il costo), la
  lettura del proprio mercato sulla pagina di ogni libro Claude. **Spento finché il proprietario non aggiunge il
  segreto `ANTHROPIC_API_KEY`** (README): senza, i quattro libri restano liquidi e la pagina lo dice. I diciassette
  libri di prima e il loro backtest sono identici prima e dopo (impronta b66ecbb82b75 invariata). Rivisto prima
  della messa in linea da un agente che non lo aveva visto scrivere (capitolo 12 F): un tentativo pagato che un giro
  fallito cancellava (ora un registro fuori dal ramo dati lo rimette), i limiti di token di un modello che non
  pensa, un riassunto troncato accettato, le ricerche e il tempo senza un tetto per lettura, un mercato che poteva
  mancare per sempre, il lunedì senza la seduta di venerdì: tutto corretto prima della prima lettura.

## Cosa ha richiesto una correzione rispetto al piano iniziale

Queste sono le cose che solo l'esecuzione reale ha fatto emergere; sono documentate nei commit e nel codice.

1. **Granularità dei lotti.** Con 10.000 $ e il Brent a ~100 $, un lotto da 100 barili è già ~1x del conto:
   il sizing diventava binario e il volatility targeting del §9 non poteva funzionare. Default portato a 10 bbl.
2. **Budget di expected shortfall.** Con il Brent al 50% di volatilità implicita, 1x porta un ES 99% a un
   giorno di circa l'11,6% del nozionale: un budget al 3% avrebbe bloccato la leva sotto 1x per sempre,
   rendendo inutile tutto il gate. Portato al 12% con l'aritmetica in un commento, e il Monte Carlo
   settimanale lo riverifica contro il vincolo sul rischio di rovina.
3. **Calendario delle feature.** `md.prices` è indicizzato sull'unione delle fonti: i giorni in cui il Brent
   non tratta restavano nel frame e avvelenavano ogni finestra mobile (70% di NaN nel 2026). Le feature ora
   vivono sul calendario di negoziazione del Brent.
4. **Costo del walk-forward.** Feature e regimi venivano ricostruiti per ogni singolo giorno: un backtest di
   11 anni era impraticabile. Ora si costruiscono una volta e si affettano per data, cosa lecita solo perché
   ogni feature è causale (è esattamente ciò che verifica `tests/test_no_lookahead.py`).
5. **Strumenti multi-gamba.** La sessione quotava solo il front: spread, crack e butterfly venivano rifiutati
   per prezzo di riferimento mancante e quelle strategie non potevano eseguire. Ora gli spread sono quotati
   dalle gambe e le gambe outright vengono marcate ogni giorno.
6. **Feature mancanti come modificatori.** S1 richiedeva la pendenza della curva, che però usa solo per
   scegliere tra size piena e dimezzata: senza storico di curva taceva per anni. Ora degrada alla size
   prudente e lo dichiara. S4-S7 e S11 restano correttamente silenziose: curva e scorte sono la loro sostanza.

## Il risultato, detto chiaramente

Il sistema è completo e verificato su dati veri, e il suo primo verdetto è negativo: **su 18,5 anni di storia
reale nessuna delle 18 strategie passa la validazione** (DSR 0,00 per tutte, PBO 0,457, Sharpe del master
-0,77). Nessuna viene promossa, tutte restano in incubazione a peso zero e il master è in contanti a 10 000 $.
La dashboard lo dichiara in prima pagina invece di mostrare un conto fermo senza spiegazione.

Nove strategie su diciotto non hanno aperto nessuna operazione nella finestra: le feature che richiedono
(curva lontana, scorte, macro, OPEC+, stagionalità, opzioni) non coprono tutto il periodo. Le loro tesi non
sono smentite, sono non testate — e questo è scritto dove si leggono i risultati, non lasciato intendere.

I numeri completi, incluso il Monte Carlo di rovina sui rendimenti reali dal 1987, sono in `docs/VALIDATION.md`.

## Un limite noto del primo sistema: l'etichetta del regime (misurato il 9 ottobre 2026)

Dal 6 ottobre la pagina d'archivio mostra il regime «Contango + eccesso d'offerta + trend ribassista» al
99,96%, mentre la curva è in backwardation (M1-M6 +10,6%), il trend a 63 giorni è positivo e le scorte sono
sulla media a cinque anni. L'etichetta è sbagliata. La causa è stata riprodotta su una copia dello stato reale
e ha tre parti:

1. il modello sceglie quale pendenza della curva usare guardando l'intera tabella: bastano le quattro righe di
   curva vera del Brent (l'archivio parte dal 5 ottobre) per preferirla alla pendenza M1-M2 approssimata, che
   ha 9 138 righe dal 1987. Poi ogni finestra di stima scarta la colonna, vuota in quella finestra: tutte le
   146 stime girano senza alcuna informazione sulla curva (per questo l'etichetta porta il segno ≈);
2. le regole che danno il nome agli stati leggono solo la pendenza M1-M6: quella di riserva, anche quando è
   nel modello, non arriva mai al nome;
3. il nome descrive la media di lungo periodo di uno stato, non la giornata: il mercato dell'8 ottobre
   (volatilità alta, trend positivo, premio di volatilità negativo) cade nello stato «teso» più vicino, la cui
   media storica ha le scorte alte e nient'altro di notevole.

Riparando i punti 1 e 2 in una prova, l'etichetta dell'8 ottobre diventa «Shock/crash» al 100% - di nuovo
sbagliata, con un rendimento a un mese di +6% - e 3 442 giorni su 9 917 cambiano nome. Non è quindi un
ritocco: cambia la storia dei regimi che leggono S3, S8, S10, S11, S14, S17, la condizione del gate sui long
«geopolitici», i pesi di S20 e l'ensemble delle previsioni, e va seguita da una nuova validazione. Finché
resta così, nessuna etichetta dice «geopolitico» nel mezzo di una guerra, e le cautele che quelle strategie
legano a quella parola non scattano.

Non è stato toccato nulla: è una decisione di ricerca sul primo sistema, il cui conto è fermo per costruzione.
Il desk - i quattro libri che operano - non legge il regime.
