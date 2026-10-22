# Oil Paper Trading — Brent

Sistema di ricerca e **paper trading** sul petrolio. Scarica dati reali, calcola una previsione da sette
segnali (il prezzo del greggio, la curva dei future, il rame e il dollaro), e la fa comprare a quattro conti
simulati da 10.000 $ che differiscono per quanta leva usano e per il lato short; dal 13 ottobre 2026 tre libri
**candidati** (oro, rame, agricoltura) girano accanto a loro con le stesse regole. Tutto è pubblicato su un sito
in inglese: <https://andreramolivaz.github.io/oil-paper-trading/>. Dal 16 ottobre la pagina principale («Desk») dice
in una frase quanti libri hanno una posizione aperta, elenca le posizioni e mostra i libri in quattro gruppi (il
nucleo, i candidati, i libri su Kalshi, i libri Claude) con un filtro da cliccare; ogni libro apre la sua pagina con
tutta la sua storia (ogni eseguito, ogni decisione con le sue parole, le vite prima di un reset). Ciò che i libri
leggono - mercato, previsione, indici di Kalshi, l'analista Claude, allocatore, mercati a eventi con le allerte - è
sulla pagina «Signals».

> **Simulazione a scopo di studio su dati reali. Nessun consiglio finanziario, nessun ordine reale, nessun
> broker collegato.** Ogni numero mostrato porta la sua fonte e il suo orario; quando un dato manca il sistema
> scrive che manca e non apre nuovo rischio.

## I quattro libri

| Libro | Strumento (com'è su Robinhood) | Rischio | Leva | Che cosa fa |
|---|---|---|---|---|
| **Prudente** | BNO, fondo sul Brent | un quarto di Kelly, 12% di volatilità | mai oltre 1x | compra il fondo quando la previsione è lunga, altrimenti contanti |
| **Dinamico** | BNO a margine; SCO per lo short | mezzo Kelly, 25% | fino a 2x (1,5x nel fine settimana) | la stessa previsione, il doppio del rischio, e anche il lato short: quando la previsione è negativa compra SCO, il fondo che ogni giorno rende −2 volte il WTI, in contanti |
| **Spinto** | /MCL, future micro sul WTI (100 barili) | Kelly pieno, 50% | fino a 10x (3x nel fine settimana) | long e short; può azzerare il conto |
| **Opzioni** | spread di put su USO e BNO | 5% del conto per struttura, due strutture | rischio definito | vende una put sotto il mercato quando la previsione è lunga; sperimentale |

La previsione è una sola, su una scala da −20 a +20, fatta di sette segnali da tre fonti che pesano un terzo
ciascuna:

- **il prezzo del greggio**: trend (quattro incroci di medie mobili), accelerazione del trend, asimmetria dei
  rendimenti;
- **la curva dei future**: carry (backwardation o contango) e carry-momentum (la pendenza sale o scende);
- **gli altri mercati**: trend del rame e trend del dollaro a segno invertito, letti alla chiusura del giorno
  prima.

L'esposizione è `previsione / 10 × obiettivo di volatilità × m / volatilità di oggi`: **la leva non si sceglie,
esce da quel rapporto**. Con il greggio al 45% di volatilità anche il libro da 10x sta intorno a 1x. `m` è il
moltiplicatore dell'allocatore (sotto): 1 di regola, mai sopra 1.

Che cosa aspettarsi, dal backtest con costi reali (dettagli e limiti in `docs/VALIDATION.md`):

| | Rend. annuo | Volatilità | Sharpe | Perdita massima | Sharpe fino all'8 ottobre 2026 (tre segnali) |
|---|---|---|---|---|---|
| Prudente (2011-2026) | +4,9% | 9% | 0,56 | −23% | 0,39 |
| Dinamico (2011-2026) | +19,6% | 29% | 0,77 | −50% | 0,37 |
| Spinto (1986-2026) | +22,2% | 55% | 0,64 | −86% | 0,45 |
| Brent comprato e tenuto (2011-2026) | +4,0% | 35% | 0,29 | −87% | |

Da leggere con tre avvertenze. I quattro segnali aggiunti sono quelli rimasti in piedi fra sedici provati: una
parte del miglioramento è selezione, e la stima che non sceglie nulla è più bassa di circa 0,1. Il lato short
del libro dinamico ha guadagnato quasi tutto in tre anni di crollo (2014, 2015, 2020) e ha tolto qualcosa in
dieci degli altri tredici. E il 2023, il 2024 e il 2025 sono stati negativi per tutti e tre i libri. Uno Sharpe
di 0,6 su quindici anni dista poco più di due errori standard da zero; oltre 0,7-0,8 su un solo mercato non si
va onestamente, e gli obiettivi di rischio dei libri non sono stati alzati. Il momentum infragiornaliero, per
cui il progetto era nato, **non ha mostrato margine dopo i costi** in nessuna delle forme provate ed è stato
lasciato fuori. Il perché, con i numeri e le fonti, è in `docs/RESEARCH.md`.

**I tre libri candidati** (fase 12, 13 ottobre 2026). Oro (GLD), rame (CPER) e agricoltura (DBA), ognuno un
conto di carta da 10.000 $ con le regole del libro prudente e la sola fonte del prezzo: i mercati che la
geopolitica, la domanda di elettricità e il tempo muovono, quasi scorrelati dal greggio (0,03-0,16). Dichiarati
prima della prova e misurati una volta: Sharpe 0,50, 0,26 e 0,34 dopo i costi; con i tre libri sul greggio a
capitale uguale il portafoglio passa da 0,80 a 0,87 di Sharpe e da −61% a −30% di perdita massima, e i tre anni
negativi 2023-2025 si dimezzano. È diversificazione, non alfa. Il gas naturale, provato con i fondi UNG e KOLD,
aveva uno Sharpe negativo e non è partito. Si giudicano sugli eseguiti reali: non entrano nel totale del desk.
Uno Sharpe sopra 3 con una strategia sola, chiesto lo stesso giorno, non esiste su questi strumenti: un backtest
che lo mostrasse sarebbe la prova di un errore (`docs/RESEARCH.md`, capitolo 8).

**Altri sette candidati e l'allocatore** (fase 13, 13-14 ottobre 2026). Treasury (TLT), azioni (SPY) e cinque
temi dell'elettrificazione e dei data center — uranio (URA), utilities (XLU), litio (LIT), semiconduttori (SMH),
energia pulita (ICLN) — con le stesse regole, dichiarati prima della prova: Sharpe fra 0,35 e 0,75 ciascuno,
tredici libri insieme 1,18 (contro 0,93 degli otto senza i temi), +9% l'anno con una perdita massima del 13%.
Ancora diversificazione, non alfa, e una selezione: il filtro dichiarato non ne ha fermato nessuno. Il desk ora
**si ricalibra da sé** con regole pubblicate e misurate prima di accenderle (`config/allocator.yaml`): dopo che il
prezzo di uno strumento è raddoppiato in due anni i suoi libri girano a metà dell'obiettivo di volatilità per un
anno (acceso oggi su Brent, WTI e oro); con lo stretto di Hormuz chiuso i tetti prima del weekend si dimezzano;
un candidato diventa core dopo dodici mesi live con uno Sharpe positivo e un libro viene ritirato dopo
ventiquattro mesi con uno Sharpe sotto −0,5. Il controllo del drawdown è stato misurato e lasciato spento
(toglie Sharpe). Il backtest mostra sempre i libri con e senza allocatore. I **mercati a eventi di Kalshi**
(il Brent di venerdì, lo stretto normale entro quando, l'accordo USA-Iran) sono letti ogni giorno e mostrati sul
terminale accanto a ciò che le opzioni implicano; nessun libro li negozia, perché non c'è una storia su cui
provare una regola: l'archivio si costruisce da oggi (`docs/RESEARCH.md`, capitolo 9). Su quei mercati gira
anche un'**allerta di attività anomala**: una mossa di dieci punti in un giorno su un volume molto sopra il
solito del mercato, con l'impronta delle operazioni (poche e grosse, da un lato solo?) - la firma dei casi di
insider trading finiti sui giornali - apre una issue su GitHub e compare sul terminale. È un'impronta, non un
verdetto: una notizia arrivata prima al mercato lascia la stessa.

**Un libro speculativo su Kalshi** (fase 14, 15 ottobre 2026, su richiesta del proprietario: «una strategia
basata su Kalshi a leva, altamente speculativa»). «Oracle» è un libro candidato di carta da 10.000 $ sul future
micro WTI, long e short fino a 10x con l'obiettivo di volatilità massimo che le regole permettono (50%), che
prende la direzione dai mercati a eventi: la variazione in una settimana della probabilità che il pubblico dà
alla de-escalation entro 90 giorni (lo stretto normale, un incontro USA-Iran, un accordo nucleare: tre scale a
scadenza, lette la sera prima sull'archivio del desk). Se sale, short petrolio; se scende, long; −2 punti di
previsione per punto di probabilità. Un contratto Kalshi non si compra a leva: la leva sta sul petrolio, Kalshi
dà il verso. La regola è scritta in `docs/RESEARCH.md` (capitolo 10) prima della prima decisione; **non ha
backtest** nel replay del desk; si giudica sui soli eseguiti, non viene mai promosso, e il prior dichiarato è
che non abbia alcun vantaggio. Può azzerare il conto. I quattro libri, i dodici candidati e il loro backtest
sono identici prima e dopo. L'archivio dei mercati ha un passato: le candele giornaliere di Kalshi, dal giorno in
cui ogni mercato è stato aperto, riempiono i giorni prima che il desk li leggesse (lo stretto dal 18 marzo), e
sulla storia così ricostruita la regola è stata misurata una volta, com'è scritta: Sharpe 1,19 con un errore
standard di 1,7 su 143 sedute, un mese su otto che fa il risultato - nulla che si distingua da zero
(`docs/RESEARCH.md`, capitolo 10 H).

**Kalshi come riferimento sugli altri mercati** (fase 15, 15 ottobre 2026, sera, su «improve su altri mercati e
anche speculative kalshi sempre come riferimento»). Lo stesso schema sui fondi che il desk già tiene, solo long,
fino a 2x: un indice Kalshi dà la direzione. Due indici oltre alla de-escalation, gli unici con una storia nel
catalogo: il **percorso della Fed** (la variazione attesa del tasso sulle riunioni del FOMC entro 90 giorni, letta
sui mercati degli esiti di ogni riunione; una rilettura accomodante in una settimana è un segnale positivo) e la
**probabilità di recessione** (i mercati annuali come scala a 365 giorni). Quattro libri registrati prima di
misurare e misurati una volta sull'archivio ricostruito dalle candele: **Oracle equities** (SPY: Fed +, recessione
−) e **Oracle utilities** (XLU: Fed +) partono con Sharpe +1,6 e +1,7 (errore standard 1,7 e 2,7); bonds e gold hanno
misurato uno Sharpe negativo e non partono, per il filtro dichiarato. Per rame, chip, litio, energia pulita,
uranio e agricoltura Kalshi non ha oggi un mercato con storia, e il sistema lo dice invece di inventare un
segnale (`docs/RESEARCH.md`, capitolo 11). Stesse regole dell'Oracle: nessun backtest, mai promossi, giudicati
sugli eseguiti veri, prior di nessun vantaggio.

**Un analista Claude** (fase 17, 16 ottobre 2026, su «ho chiamate API gratis Claude 200 $ con la University of
Southern California: come posso usarle per il progetto, e aprire e chiudere posizioni»). Una volta al giorno, la
mattina di New York, il giro chiama l'API di Anthropic due volte: la prima cerca le notizie delle ultime 48 ore sul
web e scrive un riassunto dei fatti con le fonti; la seconda legge solo quel riassunto e dà al greggio, all'oro,
all'S&P 500 e ai Treasury lunghi una previsione da −20 a +20 per le cinque sedute successive, con la fiducia, le
ragioni e il rischio principale. Quattro libri candidati la comprano con le regole di sempre: **Claude oil** sul
future micro WTI (long e short, fino a 10x), **Claude gold**, **Claude equities** e **Claude bonds** su GLD, SPY e
TLT (solo long, fino a 2x). La regola è scritta in `docs/RESEARCH.md` (capitolo 12) prima della prima lettura.
**Non può esistere un backtest**: un modello addestrato sul passato conosce i prezzi che un replay gli chiederebbe
di prevedere. Si giudicano sui soli eseguiti, non vengono mai promossi, il prior è che non abbiano alcun vantaggio.
Ogni lettura resta nell'archivio con il riassunto, le fonti e il costo; la pagina «Signals» la mostra per intero.
**È spento finché non si aggiunge la chiave** (sotto, «Accendere l'analista Claude»): senza, i quattro libri
restano liquidi e la pagina lo dice.

Il primo sistema (21 strategie su conti ombra, regimi, previsioni di prezzo) continua a girare ogni giorno e
resta consultabile dalla pagina **Archive** del sito, una pagina sola: nessuna sua strategia ha superato la
validazione, quindi il suo conto è fermo per costruzione.

## Come funziona

```
GitHub Actions                        branch `data`                    GitHub Pages
┌───────────────────────────┐        ┌─────────────────┐              ┌──────────────────┐
│ runner   un giro ogni 30' │ ─────► │ state/…         │ ───────────► │ il terminale     │
│ watchdog lo riaccende     │ scrive │ site-data/*.json│  raw.github  │ legge desk.json  │
│ weekly   sabato           │        └─────────────────┘              └──────────────────┘
└───────────────────────────┘
```

- **`runner`** è un lavoro che resta acceso circa cinque ore, fa un giro ogni mezz'ora (ai minuti :18 e :48,
  quando l'ultima barra da 30 minuti è arrivata per intero anche su un flusso in ritardo) e alla fine avvia il
  proprio successore. Da domenica 17:30 a venerdì 18:30, ora di New York.
- **`watchdog`** è l'unico lavoro pianificato della settimana: controlla che un runner sia vivo e, se no, lo
  avvia. Le pianificazioni di GitHub partono con ore di ritardo: per questo non fanno più il lavoro, lo
  sorvegliano soltanto.
- **Un giro** (`python -m engine.cli tick`) scarica ciò che serve, esegue sugli ultimi prezzi gli ordini in
  coda, controlla margini e stop, rolla il future se è ora, prende la decisione del giorno se non è già stata
  presa, marca i conti e pubblica `desk.json`. Rifà anche la fine giornata del primo sistema quando una data di
  Londra è chiusa e non l'ha ancora. È idempotente: ripeterlo non duplica nulla.
- **`weekly`** (sabato) aggiorna le fonti lente, rifà backtest e validazione e comprime la storia del branch
  dati.

Un ordine deciso alle 15:18 di New York si esegue all'apertura della barra da 30 minuti successiva (le
15:30), con lo spread e le commissioni dello strumento: mai al prezzo che ha generato il segnale. L'eseguito
compare sul terminale un'ora dopo, quando quella barra è chiusa e il flusso l'ha consegnata tutta. Se il motore resta fermo,
alla ripartenza le barre arrivano nell'ordine e ai prezzi in cui sono avvenute: un ritardo sposta *quando* lo
vedi, non *che cosa* è successo.

## Configurazione (fatta il 5 ottobre 2026)

Le quattro cose che il codice non poteva fare da sé sono state completate e verificate su GitHub:

| Cosa | Dove | Stato |
|---|---|---|
| Repository pubblico | Settings → General | ✅ `raw.githubusercontent.com` risponde 200: la dashboard legge il branch `data` dal vivo |
| GitHub Pages | Settings → Pages → Source: **GitHub Actions** | ✅ online su <https://andreramolivaz.github.io/oil-paper-trading/> |
| Permessi dei workflow | Settings → Actions → General → Workflow permissions → **Read and write** | ✅ i job scrivono il branch dati e aprono le issue di allerta |
| Chiavi API gratuite | Settings → Secrets and variables → Actions | ✅ `EIA_API_KEY` e `FRED_API_KEY` presenti; `ALPHAVANTAGE_API_KEY` aggiunta l'8 ottobre 2026 (la conferma è la fonte `insider_form4` verde nel riquadro «Status» della pagina «Signals») |

Le chiavi restano **opzionali**: senza di esse il sistema gira lo stesso, con più fonti sui fallback.

| Secret | Dove si ottiene | Cosa sblocca | Senza la chiave |
|---|---|---|---|
| `EIA_API_KEY` | <https://www.eia.gov/opendata/register.php> (istantaneo) | Storico completo delle scorte settimanali, scorte di **Cushing**, previsione STEO, proxy storico della curva (WTI C1-C4) | Solo l'ultima settimana di scorte, nessun Cushing, nessun proxy di curva: le feature relative restano NaN dichiarato e le strategie che le usano non operano |
| `FRED_API_KEY` | <https://fredaccount.stlouisfed.org/apikeys> | OVX, VIX, dollaro, tassi e breakeven con storico pieno e fonte ufficiale | Fallback su Yahoo e sui CSV di FRED: funziona, stato giallo |
| `ALPHAVANTAGE_API_KEY` | <https://www.alphavantage.co/support/#api-key> (istantaneo) | Le dichiarazioni Form 4 degli insider del settore, per la strategia S21 del primo sistema | La tabella resta vuota e S21 non opera. I quattro libri non la usano |
| `ANTHROPIC_API_KEY` | <https://console.anthropic.com/> → API keys, nell'organizzazione che ha i crediti | L'analista Claude e i suoi quattro libri (fase 17) | Nessuna lettura: i quattro libri Claude restano liquidi, tutto il resto è com'è |

Senza chiavi il sistema gira comunque: in un'esecuzione reale del 5 ottobre 2026 ha recuperato 23 voci su 25,
stato complessivo giallo, senza alcun crash (dettagli in `docs/DATA_SOURCES.md`).

## Accendere l'analista Claude

1. Nella console di Anthropic (<https://console.anthropic.com/>), nell'organizzazione o nello spazio di lavoro che
   ha i crediti, creare una chiave API (la voce «API keys»). La lettura usa la ricerca web dell'API: deve essere
   abilitata per quell'organizzazione (un amministratore può spegnerla; spenta, ogni lettura fallisce e la pagina
   mostra l'errore dell'API).
2. Su GitHub, nel repository: Settings → Secrets and variables → Actions → **New repository secret**, nome
   `ANTHROPIC_API_KEY`, valore la chiave. **La chiave va solo lì**: mai in un file, in un messaggio o in una issue.
   Il motore non la scrive mai: né nell'archivio, né nei log, né sul sito.
3. Il runner legge i segreti quando parte: la chiave vale dal runner successivo, cioè al passaggio di consegne
   (entro circa cinque ore) o al prossimo aggiornamento del motore su `main`. Per averla subito: Actions → la
   corsa di `runner` in esecuzione → Cancel, poi Actions → `runner` → Run workflow.
4. La prima lettura arriva al primo giro dopo le 13:00 UTC di un giorno lavorativo americano (le 9 di New York con
   l'ora legale). La pagina «Signals», sezione «Claude analyst», dice se la chiave c'è («Reading the news» oppure
   «Off»), mostra la lettura per mercato con le ragioni, il riassunto con le fonti e quanto è costata.

Quanto costa: ogni lettura sono due chiamate a Claude Sonnet 5.5 (2 $ e 10 $ per milione di token in ingresso e in
uscita; il modello pensa prima di rispondere e il pensiero si paga come uscita) più fino a sei ricerche web (0,01 $
l'una); stimato 0,10-0,25 $ a lettura, 3-6 $ al mese, una lettura per giorno lavorativo. Un tetto in `config/analyst.yaml` (`monthly_budget_usd: 20`) ferma le chiamate quando la spesa del
mese, calcolata dall'uso che l'API riporta, lo raggiunge. Con 200 $ di crediti sono anni. Per spegnerlo basta
togliere il segreto (o `enabled: false` nel file): i libri leggono l'ultima lettura per tre giorni, poi si chiudono.

## Il pulsante Reset

Il reset è un workflow (`.github/workflows/reset.yml`) con input di conferma `RESET` e la scelta di che cosa
azzerare: un libro (`prudente`, `dinamico`, `spinto`, `opzioni`) oppure tutto. Archivia la vita corrente,
chiude le posizioni all'ultimo prezzo e riparte da 10.000 $.

Dal sito, pagina **Reset**: se salvi nel tuo browser un token fine-grained con il solo permesso
*Actions: write* su questo repository, il pulsante avvia il workflow direttamente; altrimenti apre la pagina del
workflow su GitHub, dove parte con un clic. **Il token resta nel tuo browser e non finisce mai nel repository.**

## Il Rocket: una lotteria da 1 000 $

Dal 22 ottobre 2026 c'è un conto a parte, chiesto per «fare 10 000 $ in tre mesi partendo da 1 000»: il **Rocket**
(`engine/desk/rocket.py`, `docs/RESEARCH.md` capitolo 14). Compra call su USO quando il Dinamico è long, put quando è
short, metà del conto per scommessa, e solo se nella settimana l'indice di de-escalation di Kalshi si è mosso nel verso
che serve alla scommessa. Vende quando il Dinamico cambia idea, rinnova a sette giorni dalla scadenza, non ha un
obiettivo di guadagno. Le regole di rischio del progetto sono sospese **per lui solo**, e non entra in nessun totale.

**È una lotteria, e i numeri lo dicono** (un modello delle opzioni, ≈): su 811 finestre di tre mesi dal 2011 il 10x
è arrivato nell'1,8%, tutte in tre crolli del petrolio comprati con le put; il risultato tipico è metà del conto; nel
2026 nessuna delle 17 finestre ha raddoppiato. Con contratti interi, sotto i 240 $ circa non riesce più a comprare
nulla: la pagina lo dice.

- **Farlo ripartire.** Pagina «Desk» → Rocket → **Start a new life at 1,000 $**. Con il token salvato sulla pagina
  Reset il pulsante avvia il workflow `rocket-reset.yml` (un clic se la vita è finita, due se è ancora in corso);
  senza token apre il workflow su GitHub, dove «Run workflow» lo avvia. La vita finita resta nella storia del libro.
- **Copiarlo su un conto vero.** La pagina del libro mostra l'ordine di ogni scommessa: per l'API di Alpaca (il conto
  di prova e l'API sono gratuiti, le opzioni sono attive di serie sul conto di prova; `POST /v2/orders` con il
  simbolo OCC, la quantità, il limite) e i passi del modulo di Robinhood (opzioni di livello 2: comprare call e put).
  Il limite è il prezzo a cui ha eseguito il conto di carta, su quotazioni in ritardo di 15 minuti: un ordine vero può
  eseguire altrove o non eseguire. È uno studio, non un consiglio.

## Se il motore si ferma

Ogni pagina del sito lo dice in alto («Engine late» o «Engine stopped»). Ogni runner avvia il proprio successore;
se la catena si spezza la riaccende il watchdog, che però è su una pianificazione di GitHub e può tardare anche
di ore. Per farlo subito: **Actions → runner → Run workflow**. Non c'è nulla da recuperare a mano: al primo
giro il motore riprende da dove era rimasto, e le barre arrivate nel frattempo vengono lette nell'ordine e ai
prezzi in cui sono avvenute.

Nell'elenco delle esecuzioni un runner «cancelled» è normale: ogni modifica al motore su `main` ferma quello
in corso e ne avvia uno sul codice nuovo.

## Limiti, dichiarati

- **Un solo mercato, poca storia.** Quindici anni per il fondo Brent, quaranta per il WTI: gli errori standard
  sono larghi quanto i risultati.
- **Su Robinhood non c'è un future sul Brent.** La leva passa dal WTI: il libro spinto porta anche il rischio
  che i due greggi si muovano diversamente.
- **Un contratto è quasi tutto il conto.** 100 barili a 90 $ sono 0,9 volte 10.000 $: il libro spinto tiene
  zero o un contratto finché la previsione non è forte, e lo scrive in ogni decisione.
- **Prezzi in ritardo.** Yahoo (non ufficiale) è in ritardo di 10-15 minuti, le opzioni Cboe di 15. Gli ordini
  simulati si eseguono su quei prezzi, con uno spread prudente.
- **Curva del Brent.** Yahoo non conserva le scadenze passate: la pendenza reale del Brent esiste da quando il
  motore la archivia. Prima, per il fondo si usa quella del WTI, marcata `approx` (≈).
- **Opzioni.** Il backtest del libro delle opzioni è un modello, non uno storico di quotazioni: gratis non
  esiste (quello di Alpha Vantage è riservato ai piani a pagamento, verificato l'8 ottobre 2026). Assegnazione
  anticipata e rischio a scadenza non sono simulati.
- **Il fine settimana.** Uno stop non ferma un'apertura in gap, e nel 2026 ce ne sono state fino a +16%.
- **Il lato short passa da un fondo a leva.** SCO segue il WTI, si ricalcola ogni giorno e può azzerarsi in una
  notte; i suoi raggruppamenti di quote non sono gestiti: se ne viene annunciato uno mentre il libro dinamico
  lo tiene, il libro va azzerato a mano (`reset --book dinamico`).
- **Rame e dollaro arrivano con un giorno di ritardo per costruzione.** Si legge la chiusura del giorno prima,
  e solo quando uno scaricamento del giorno dopo l'ha confermata: su Yahoo la riga del giorno in corso non è
  ancora la chiusura.

## Sviluppo

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.lock
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy engine
.venv/bin/pytest -q                  # test offline e deterministici
.venv/bin/pytest -q -m network       # test che toccano le fonti reali (opzionali)

OPT_STATE_DIR=./state .venv/bin/python -m engine.cli tick            # un giro completo: scarica, esegue, decide, marca
OPT_STATE_DIR=./state .venv/bin/python -m engine.cli desk-backtest   # backtest dei libri sullo storico archiviato
OPT_STATE_DIR=./state .venv/bin/python -m engine.cli export-site --out site-data
cd site && npm ci && npm run dev
```

Ricerca, numeri e fonti: `docs/RESEARCH.md`. Regole dei libri e schede delle strategie: `docs/STRATEGIES.md`.
Backtest e che cosa garantiscono i test: `docs/VALIDATION.md`. Fonti dati verificate: `docs/DATA_SOURCES.md`.
Contratto JSON del sito: `docs/SITE_DATA.md`. Piano e decisioni: `docs/PLAN.md`. Architettura e regole non
negoziabili: `CLAUDE.md`. Requisiti originali: `docs/BRIEF.md`.

## Licenza e responsabilità

Codice a scopo didattico. I dati appartengono alle rispettive fonti e sono usati secondo i loro termini.
Nessuna garanzia sui risultati: è una simulazione, e una simulazione non è un rendimento.
