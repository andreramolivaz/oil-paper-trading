# Strategie

Il sistema ha due parti. **I quattro libri del desk** sono ciò che opera: una previsione, quattro modi di
comprarla. **Le 21 strategie del primo sistema** (più sotto) restano in incubazione a peso zero, con le loro
schede e i loro conti ombra: nessuna ha superato la validazione, e la ricerca che contengono resta consultabile.

## I libri del desk (`engine/desk`, `config/books.yaml`)

### La previsione

Sette segnali, ognuno su una scala da −20 a +20 dove 10 è una convinzione normale, raggruppati per **fonte**:
da dove viene l'informazione. Le tre fonti pesano un terzo ciascuna, e dentro una fonte i segnali pesano
uguale.

| Fonte | Segnale | Regola | Da dove viene |
|---|---|---|---|
| **Il prezzo del greggio** | Trend | media di quattro incroci di medie mobili esponenziali (8-32, 16-64, 32-128, 64-256 giorni) sul rendimento investibile, divisi per la volatilità | Carver, parametri pubblicati |
| | Accelerazione | il trend di oggi meno quello di *n* giorni fa, per *n* = 16, 32 e 64 (incroci 16-64, 32-128, 64-256) | Carver, parametri pubblicati |
| | Asimmetria (skew) | meno l'asimmetria dei rendimenti su 180 e 365 giorni, confrontata con la propria storia, divisa per la propria volatilità e lisciata (45 e 90 giorni). Calcolata sul WTI, letta anche dal fondo Brent | Carver, parametri pubblicati; la media di confronto è la storia del mercato stesso |
| **La curva dei future** | Carry | +10 se la curva è in backwardation (il contratto vicino vale più di quello lontano), −10 in contango, 0 se la pendenza è sotto l'1% l'anno | Koijen e altri; Bouchouev |
| | Carry-momentum | +10 se la pendenza è sopra la sua media a 20 giorni, −10 se sotto | Bouchouev e Zuo 2020 |
| **Gli altri mercati** | Rame | la regola di trend qui sopra applicata al future sul rame, sulla chiusura del giorno prima | stessa regola del greggio, nessun parametro nuovo |
| | Dollaro | la stessa regola sull'indice del dollaro (DXY), a segno invertito, sulla chiusura del giorno prima | stessa regola del greggio, nessun parametro nuovo |

La previsione è la media delle tre fonti per 1,75, con tetto a ±20. Il moltiplicatore viene dalla formula di
Carver sulle correlazioni misurate fra i sette segnali (sono poco correlati, quindi la loro media è più piccola
di ciascuno). Quando un segnale manca la sua fonte si legge su ciò che resta, e il moltiplicatore scende: con
la sola previsione di prima (trend, carry, carry-momentum) vale 1,25, come allora. Nessun parametro è stato
scelto guardando i risultati; che cosa è stato provato e scartato è in `docs/RESEARCH.md`.

Rame e dollaro chiudono dopo il regolamento del greggio: una decisione del giorno legge la loro chiusura del
giorno prima, mai quella dello stesso giorno. Il motore scarica le due tabelle al giro che deve prendere la
prima decisione della giornata, e non la sera prima: la riga del giorno in cui una tabella viene scaricata non
è ancora la chiusura di quel giorno (su Yahoo, finché il mercato è aperto è il prezzo di un'altra scadenza;
dopo le 18 di New York è l'inizio della seduta successiva), e diventa quella che c'è nello storico solo dal
giorno dopo. Una chiusura più vecchia di una settimana non viene letta: il segnale tace e gli altri continuano.

- **Tesi.** I flussi di copertura sono lenti e la curva dice chi ha fretta: in backwardation il mercato paga
  chi detiene il barile, e una curva che si irrigidisce anticipa il prezzo. Il trend cattura ciò che la curva
  non dice ancora; la sua accelerazione dice se sta finendo. Chi detiene un mercato che può crollare di colpo
  viene pagato per farlo (skew). E la domanda di greggio segue il ciclo globale con ritardo: rame e dollaro lo
  mostrano prima, con meno shock d'offerta propri.
- **Chi perde.** Chi copre la produzione vendendo a termine a qualunque prezzo; chi compra il contratto vicino
  in contango pagando il roll ogni mese; chi compra protezione contro i crolli quando tutti la vogliono.
- **Quando non funziona.** Mercati laterali con curva piatta (2023-2025: tre anni negativi di fila nel
  backtest, con la previsione di prima e con questa), inversioni brusche a V, shock d'offerta che il ciclo
  globale non vede (rame e dollaro non hanno dato nulla dal 2022 al 2025).

### Dalla previsione alla posizione

```
esposizione (multipli del capitale) = previsione / 10 × obiettivo di volatilità × m / volatilità corrente
```

dove `m` è il moltiplicatore dell'allocatore (fase 13, sotto: 1 di regola, 0,5 con l'interruttore della bolla
acceso, 0 per un libro ritirato; mai sopra 1). Poi tre tetti, nell'ordine: il tetto del libro, il tetto più basso prima di una chiusura dei mercati più lunga
di un giorno, il margine dello strumento. Il broker simulato impone comunque il limite assoluto di 10x. La
dimensione esatta diventa lotti interi con la fascia di inerzia di Carver: dentro il 10% di una posizione
normale non si tocca nulla, fuori si va al bordo della fascia. Non c'è nessun «gate» da superare e nessuna
decisione discrezionale: i tre libri differiscono per quanta previsione comprano e per il lato short.

| Libro | Strumento | Obiettivo di volatilità | Tetto | Prima del weekend | Direzione |
|---|---|---|---|---|---|
| **Prudente** | BNO | 12% (un quarto di Kelly) | 1x | 1x | solo long |
| **Dinamico** | BNO a margine; SCO in contanti per lo short | 25% (mezzo Kelly) | 2x | 1,5x | long e short |
| **Spinto** | /MCL | 50% (Kelly pieno) | 10x | 3x | long e short |

**Il lato short del libro dinamico.** Un fondo non si vende allo scoperto sul conto che i libri imitano.
Quando la previsione è negativa il libro *compra* SCO, il fondo che ogni giorno rende −2 volte un indice di
future sul WTI: metà dei dollari per la stessa esposizione, pagati in contanti, mai più del valore del conto.
Stessa formula e stessa fascia di inerzia del lato long; i tetti (2x, 1,5x prima di un fine settimana) valgono
per l'esposizione al greggio, non per i dollari, e la «leva» mostrata dal terminale è quell'esposizione.
Quando la previsione cambia segno il libro vende una gamba e compra l'altra nella stessa decisione: la
vendita si esegue per prima e l'acquisto la aspetta, così il libro non tiene mai i due lati insieme. La vendita
di un fondo chiude ciò che trova e mai di più (un fondo non va sotto zero); i tetti valgono anche al prezzo
di esecuzione e dopo ogni valutazione, non solo al prezzo della decisione; senza un prezzo recente di SCO il
lato short non si apre e non cresce, si può solo ridurre. SCO segue il WTI e non il Brent, si ricalcola ogni
giorno e non è uno strumento da tenere: comprato nel 2011 e lasciato lì ha perso il 99%. Un raggruppamento di
quote di SCO o di BNO non è gestito: quando ne viene annunciato uno il libro va azzerato a mano. Che cosa
aggiunge il lato short e in quali anni: `docs/RESEARCH.md`, «Il lato short del fondo».

Gli obiettivi sono frazioni di Kelly per uno Sharpe atteso di 0,5, e non sono stati alzati quando i sette
segnali ne hanno misurato uno più alto: una parte di quel miglioramento è selezione. Una decisione al giorno, al primo giro
dopo le 15:00 di New York per il fondo (le 15:18) e dopo le 14:35 per il future (le 14:48, a regolamento
avvenuto); l'ordine si esegue all'apertura della prima barra da 30 minuti che inizia dopo la decisione, con lo
spread e le commissioni reali dello strumento. Una barra è considerata chiusa quindici minuti dopo la sua fine,
perché il flusso dei prezzi è in ritardo: prima di allora il suo massimo e il suo minimo non sono ancora
definitivi, e uno stop va controllato su quelli veri. Il future viene lasciato cinque sedute prima della
scadenza. Il fondo a margine paga il 5,25% l'anno sul prestito.

Ogni libro si ferma per il resto della giornata se perde più della sua soglia (5%, 8%, 15%) e per sempre, fino
al reset, se scende sotto il 5% del capitale iniziale.

### I libri candidati (fase 12, 13 ottobre 2026)

Tre libri di carta ACCANTO ai quattro, con `role: candidate` in `config/books.yaml`: non entrano nel totale del
desk, hanno il loro, e si giudicano sugli eseguiti reali prima che qualcuno si fidi. Le regole sono quelle del
libro prudente, senza un parametro nuovo; la previsione è la sola fonte del prezzo (trend, accelerazione,
asimmetria) calcolata sul prezzo rettificato del fondo stesso, perché nessuno dei tre ha una curva per
contratto o un «altro mercato» nel motore.

| Libro | Fondo | Che cosa lo muove | Obiettivo di volatilità | Tetto | Direzione | Costo dichiarato |
|---|---|---|---|---|---|---|
| **Gold** | GLD | geopolitica, tassi reali, banche centrali | 12% | 1x | solo long | 1 pb di mezzo spread |
| **Copper** | CPER | ciclo globale, domanda di elettricità (anche dei data center) | 12% | 1x | solo long | 8 pb |
| **Agri** | DBA | tempo meteorologico, raccolti | 12% | 1x | solo long | 5 pb |

Dichiarati prima della prova (`docs/RESEARCH.md`, capitolo 8): un quarto candidato, il gas naturale con i fondi
UNG e KOLD, aveva uno Sharpe negativo sull'intera storia e per il filtro dichiarato non è partito. I tre che
partono misurano 0,50 (oro, t 2,3 su vent'anni), 0,26 (rame) e 0,34 (agricoltura) dopo i costi; con i tre libri
sul greggio a capitale uguale lo Sharpe del portafoglio passa da 0,80 a 0,87 e la perdita massima da −61% a
−30%: è diversificazione, non alfa, e il sito lo scrive. Il terminale mostra per ogni libro anche che cosa
perderebbe su un gap del −20% dello strumento alla posizione di oggi (`stress`).

### I sette candidati della fase 13 (13 ottobre 2026)

Stesse regole del libro prudente, stessa sola fonte del prezzo, `role: candidate`, un conto di carta ciascuno.
Due mercati tradizionali e cinque temi dell'elettrificazione e dei data center, scelti e scritti PRIMA della
prova (`docs/RESEARCH.md`, capitolo 9 A); il filtro dichiarato (Sharpe dopo i costi negativo sull'intera storia
= non parte) non ha morso su nessuno dei cinque temi, e lo si dice: è una selezione.

| Libro | Fondo | Che cosa lo muove | Sharpe misurato (t) | Costo dichiarato |
|---|---|---|---|---|
| **Bonds** | TLT | tassi lunghi, fuga verso la qualità | 0,37 (1,8) (cap. 8 D) | 1 pb |
| **Equities** | SPY | il ciclo, il premio azionario | 0,58 (2,9) (cap. 8 D) | 1 pb |
| **Uranium** | URA | il nucleare per i data center | 0,35 (1,3) | 5 pb |
| **Utilities** | XLU | la domanda di elettricità | 0,43 (2,2) | 1 pb |
| **Lithium** | LIT | batterie, accumulo | 0,67 (2,6) | 3 pb |
| **Semis** | SMH | i chip dell'AI (correlazione 0,65 con SPY) | 0,47 (2,4) | 1 pb |
| **Clean** | ICLN | energia rinnovabile | 0,75 (3,1) | 3 pb |

Obiettivo di volatilità 12%, tetto 1x, solo long, stop giornaliero al 5%. Tenuti insieme agli altri otto a
capitale uguale dal 2013: Sharpe 1,18 (t 4,4), +9,1% l'anno, volatilità 7,7%, perdita massima −13% — contro
0,93 degli otto senza i temi. È diversificazione fra mercati che non si muovono insieme, non alfa su nessuno
di loro, e il sito lo scrive. Da qui si giudicano sugli eseguiti reali: la regola del ciclo di vita (sotto)
dice quando uno di loro diventa core o viene ritirato.

### L'allocatore (`engine/desk/allocator.py`, `config/allocator.yaml`, fase 13)

Il proprietario ha chiesto che il desk «cambi in base a come va», anche sui quattro libri. Ogni regola qui
moltiplica l'obiettivo di volatilità di un libro o il suo tetto prima di una chiusura per un numero fra 0 e 1:
niente alza mai una posizione sopra quanto le regole del libro permettono. Ciascuna è stata misurata sul
replay PRIMA di essere accesa (`docs/RESEARCH.md`, capitolo 9 B), i parametri sono valori pubblicati o numeri
tondi dichiarati, e il backtest riporta sempre i libri con e senza allocatore (pagina backtest, «With and
without the allocator»).

| Regola | Stato | Che cosa fa | Misura |
|---|---|---|---|
| **Interruttore della bolla** (Greenwood, Shleifer & You 2019) | acceso | prezzo del veicolo +100% su 504 sedute → rischio × 0,5 per 252 sedute | Sharpe su per ogni portafoglio (core 0,80 → 0,87; tutti 0,93 → 1,00), perdita massima giù, in entrambe le metà |
| **Controllo del drawdown** (Grossman & Zhou 1993) | spento | rischio × max(0,25; 1 − drawdown / 20%), per libro | taglia la perdita massima di un terzo e lo Sharpe di 0,05-0,14: lo stesso scambio lo fa un obiettivo di volatilità più basso senza perdere Sharpe |
| **Stretto di Hormuz** | acceso | transiti sotto il 10% del normale (PortWatch, pubblicato con ~5 giorni di ritardo) → tetti prima del weekend × 0,5 | politica dichiarata, non misurata: sette mesi di dati, gap del weekend del 2026 fino a +16% |
| **Ciclo di vita** | acceso | candidato → core dopo 12 mesi live con Sharpe realizzato > 0 e drawdown entro quello del backtest; qualunque libro → ritirato (piatto, nessun rischio nuovo) dopo 24 mesi con Sharpe < −0,5 | numeri tondi dichiarati; un ritirato lo riapre solo il proprietario (`books.yaml` o reset) |

Momentum di strategia (D2, pesare i libri sul loro Sharpe recente) è stato misurato e scartato: 0,61 contro
0,80. Il 13 ottobre 2026 l'interruttore della bolla è acceso su BNO e /MCL (il greggio è raddoppiato in due
anni) e su GLD: i tre libri sul greggio e il libro dell'oro partono con l'obiettivo di volatilità dimezzato, e
il terminale lo scrive accanto a ogni decisione («risk x 0.5: price doubled in two years»). Lo stretto è
chiuso (transiti al 2% del normale): i tetti prima del weekend sono dimezzati.

### I mercati a eventi (`engine/desk/prediction.py`, Kalshi)

Un contratto Kalshi paga un dollaro se l'evento accade: il suo prezzo è la probabilità che il pubblico gli
dà, in tempo reale, su domande che la previsione non vede (lo stretto torna normale entro dicembre? il Brent
chiude sopra 104 venerdì?). Il desk li legge una volta al giorno dall'API pubblica, senza chiave
(`engine/data/adapters/kalshi.py`: Brent e WTI per giorno/settimana/mese, lo stretto, i colloqui USA-Iran,
l'OPEC, le scorte EIA), archivia la lettura in `state/desk/kalshi-AAAA-MM.jsonl` (un record per evento e
giorno) e sul terminale mostra ogni evento come una scala: per i prezzi, la mediana del pubblico e la
probabilità «sopra il prezzo di adesso» accanto a quella implicita nelle opzioni del fondo (lognormale alla
volatilità implicita ATM, segnata ≈); per le scadenze, la probabilità entro ogni data; per le soglie, la
mediana accanto al dato che il desk misura da sé (i transiti di PortWatch, le scorte EIA). **Nessun libro li
negozia**: non esiste una storia su cui provare una regola, e l'archivio serve a costruirla (primo test
possibile fra un anno). Una scala di prezzo regola sul feed del mercato (il Brent di Pyth, il regolamento ICE
del WTI), non sul contratto che il desk quota: lo scarto fra la mediana del pubblico e il riferimento è in parte
una base, e il terminale lo scrive. Un mercato oltre la sua chiusura (una lettura presa tardi, o vecchia di
giorni) non è una probabilità e il terminale lo mette da parte.

**L'allerta di attività anomala** (`engine/desk/watch.py`, su richiesta del proprietario: «su Kalshi c'è molto
insider trading»). Il giro legge i mercati ogni ora e a ogni lettura giudica i mercati a eventi (non le scale
di prezzo, che seguono il greggio): una mossa di almeno 10 punti in 24 ore (o 5 con le odds triplicate) su un
volume delle 24 ore di almeno 3 volte la mediana giornaliera del mercato e sopra il suo 90° percentile - prima
di dieci letture, almeno un quarto dell'open interest di un mercato aperto da due giorni -, mai sotto 500
contratti. Una allerta per evento e giorno, con l'impronta delle operazioni del mercato più scambiato (quante,
la quota delle cinque più grosse, il lato del taker, la più grossa e la prima) e se il resto della scala si è
mosso; sul terminale per una settimana, su GitHub come issue per due giorni. Nel weekend, quando il runner
dorme, un job a orario (`kalshi-weekend.yml`, `engine.cli watch`) legge solo i mercati e li giudica: GitHub lo
fa partire ogni poche ore, e basta, perché ogni lettura porta i campi «di un giorno fa» dell'API. È
un'impronta, non un verdetto:
una notizia arrivata prima al mercato, chi copre un rischio e un insider la lasciano uguale (`docs/RESEARCH.md`,
capitolo 9 F, con i casi pubblici e le soglie dichiarate prima di accenderla).

### Il libro «Oracle» (`engine/desk/oracle.py`, fase 14) — speculativo, senza backtest

Il proprietario ha chiesto «una strategia basata su Kalshi a leva, altamente speculativa». Un contratto Kalshi
paga 1 $ se l'evento accade e costa la sua probabilità: non si compra a margine e il desk non ha un broker di
carta per i contratti a evento. La leva, quindi, sta sul petrolio - il future micro WTI (/MCL), l'unico
strumento a leva del desk - e Kalshi dà la **direzione**. Tutto è scritto in `docs/RESEARCH.md`, capitolo 10,
prima della prima decisione, perché non c'è una storia su cui provarlo: l'archivio giornaliero del desk
(`state/desk/kalshi-*.jsonl`) È la storia, e il primo test sarà possibile fra un anno.

- **L'indice di de-escalation.** Tre scale a scadenza: lo stretto tornato normale prima di X
  (`KXHORMUZNORM`), un incontro di alto livello USA-Iran prima di X (`KXUSIRANMEETING`), un accordo nucleare
  prima di X (`KXUSAIRANAGREEMENT`). Per ciascuna, la probabilità che l'evento accada entro 90 giorni dalla
  lettura, letta sulla scala interpolando nel tempo fra i mercati quotati a due lati entro 10 centesimi, con
  (oggi, 0) come primo punto; oltre l'ultima scadenza l'ultima. L'indice è la media delle tre, pesi uguali.
- **Il segnale.** La decisione del giorno D legge la lettura archiviata del giorno UTC D (la prima della
  giornata, verso le 00:20 UTC: la sera prima a New York, come i segnali macro leggono la chiusura di ieri) o,
  se manca, la più recente non più vecchia di tre giorni; il riferimento è la lettura più recente fra 7 e 10
  giorni prima. La variazione è la media, sulle componenti presenti in entrambe le letture, di oggi meno
  riferimento: una scala che nasce o si risolve non è una mossa.
- **La previsione.** −2 per punto percentuale di variazione, limitata a ±20, zero sotto un punto: cinque
  punti di de-escalation in una settimana valgono −10 (short normale), dieci −20, cinque di escalation +10.
  Senza una lettura recente il libro non ha una vista e sta piatto; senza riferimento (archivio più giovane di
  una settimana) non decide e resta dov'è.
- **La taglia.** La regola di tutti i libri: `previsione/10 × 0,50 × m / volatilità`, obiettivo di volatilità
  0,50 (Kelly pieno, il massimo delle regole dure), `m` dell'allocatore (oggi 0,5: il WTI è raddoppiato in due
  anni), tetti 10x e 3x prima di una chiusura (1,5x con lo stretto chiuso), stop giornaliero −15%, lotto
  intero. Con il greggio al 44% una previsione di −20 vale 2,3x: due o tre micro contratti.
- **Che cos'è e che cosa non è.** Candidato (`role: candidate`, `forecast: kalshi`), un conto a sé nel totale
  dei candidati. Nessun backtest, nessuna riga nelle tabelle del replay, fuori dall'impronta delle regole; mai
  promosso dal ciclo di vita (la promozione confronta il drawdown live con quello del backtest, e qui non c'è);
  ritirato come ogni libro dopo 24 mesi con Sharpe sotto −0,5. La decisione registra ogni input (indice,
  componenti, riferimento, variazione, età delle due letture), così fra un anno la regola si riproduce riga per
  riga sull'archivio.
- **Il prior.** Nessun vantaggio: il petrolio è mille volte più liquido di quei mercati, ed è più probabile che
  Kalshi lo segua con un giorno di ritardo che il contrario. Il libro esiste per misurarlo con soldi di carta.
- **L'archivio ha un passato** (15 ottobre, `engine/desk/backfill.py`): le candele giornaliere di Kalshi, che
  esistono dal giorno in cui ogni mercato è stato aperto, riempiono l'archivio per i giorni prima che il desk
  leggesse quei mercati (lo stretto dal 18 marzo, l'accordo dal 9 gennaio, l'incontro dal 24 settembre); una
  lettura del giro non viene mai sostituita. Sulla storia così ricostruita la regola, misurata una volta com'è
  scritta (`scripts/research_oracle.py`, `docs/RESEARCH.md` 10 H): Sharpe 1,19 con un errore standard di 1,7 su
  143 sedute, +27,7% a lotti interi, un mese su otto che fa il risultato. Non distingue nulla da zero; la scala
  dichiarata (media di |F| = 9,8) regge; il prior resta.

### Gli altri libri «Oracle» (`engine/desk/oracle.py`, fase 15) — speculativi, senza backtest

Il proprietario, il 15 ottobre: «improve su altri mercati e anche speculative kalshi sempre come riferimento».
Lo stesso schema sui fondi che il desk già tiene: un indice Kalshi dà la direzione, il fondo dà la leva (2x, il
margine di Robinhood su un fondo), solo long. Registrato in `docs/RESEARCH.md`, capitolo 11, prima di misurare;
misurato una volta sull'archivio ricostruito dalle candele; due libri su quattro partono.

- **Gli indici.** `oracle.py` è un registro (`INDICES`): `deescalation` com'è sopra; **`fedpath`**, il percorso
  della Fed: le riunioni del FOMC con decisione entro 90 giorni (`KXFEDDECISION-AAMMM`), ciascuna valutata come
  variazione attesa del tasso in punti base sui cinque esiti quotati a due lati (taglio di oltre 25 = −50, taglio
  25, invariato, rialzo 25, rialzo di oltre 25 = +50; leggibile con almeno due esiti e l'80% della massa),
  sommate; la variazione settimanale sulle riunioni presenti in entrambe le letture (la sorpresa della riunione
  stessa non c'è: il giorno dopo non ha più un mercato); manica = **−2,5 per punto base**, positiva quando le
  attese si fanno accomodanti, banda morta 1 pb. **`recession`**: i mercati annuali `KXRECSSNBER-AA` («una
  recessione nel 20AA») come scala a scadenza, P(entro il 31 dicembre di Y) = 1 − Π(1 − p), letta a 365 giorni;
  manica = **+4 per punto**, positiva quando la probabilità sale, banda morta 1 punto. Le due scale erano
  dichiarate 1 e 2 e sono state portate a 2,5 e 4 con la regola scritta prima della misura (media di |F| fuori dal
  10 di Carver per più di un fattore due: 3,9 e 4,7). La variazione settimanale di recession si legge sugli anni
  quotati in entrambe le letture, con la scala del riferimento rivalutata a oggi: un mercato annuale che nasce o
  finisce non è una mossa.
- **I libri.** Un libro legge le sue maniche con un **segno** (`sleeves` in `books.yaml`) e ne fa la media, senza
  moltiplicatore; se nessuna ha un valore non decide. `oracle_equities` su SPY: fedpath +1 (Bernanke-Kuttner),
  recession −1; `oracle_utilities` su XLU: fedpath +1 (le utility come quasi-obbligazioni). Obiettivo di
  volatilità 0,50, tetto 2x e 1x prima di una chiusura, stop giornaliero −15%, lotto una quota, `m`
  dell'allocatore. Una previsione negativa è liquidità.
- **Quelli che non partono.** `oracle_bonds` (TLT: fedpath +1, recession +1) e `oracle_gold` (GLD: fedpath +1,
  deescalation +1) hanno misurato uno Sharpe netto negativo (−1,5 e −1,7 con errore standard 1,7-1,8) e per il
  filtro dichiarato non partono; non si tolgono maniche per farli passare.
- **Quanto vale.** Equities Sharpe +1,6 (±1,7) su 197 sedute, utilities +1,7 (±2,7) su 82: entro un errore
  standard da zero, due mesi che fanno l'anno. Il prior di nessun vantaggio resta; nessun backtest nel replay,
  mai promossi, giudicati sugli eseguiti veri; riesame delle scale a 60 letture vive per manica.
- **Che cosa manca, scritto.** Nessun mercato Kalshi con storia per rame, semiconduttori, litio, energia pulita,
  uranio, agricoltura; nessun lato short (nessun fondo inverso qualificato per questi mercati); le scale sulle
  stampe mensili (CPI, buste paga) sono un candidato per un'altra registrazione.

### I libri «Claude» (`engine/desk/analyst.py`, `config/analyst.yaml`, fase 17) — speculativi, senza backtest

Il proprietario, il 16 ottobre: «ho chiamate API gratis Claude 200 $ con la University of Southern California:
come posso usarle per il progetto, e aprire e chiudere posizioni». Letto così: un modello di linguaggio legge le
notizie ogni giorno e dà una direzione, quattro libri di carta la comprano con le regole di sempre. Registrato in
`docs/RESEARCH.md`, capitolo 12, prima della prima lettura.

- **La lettura.** Una al giorno, nei giorni lavorativi americani, al primo giro dopo le 13:00 UTC (la mattina di
  New York), due chiamate all'API Messages di Anthropic: la prima cerca sul web (al massimo 6 ricerche per lettura)
  e scrive un riassunto dei fatti dalla mattina del giorno lavorativo americano precedente (almeno 48 ore: il lunedì
  da venerdì mattina) su greggio, oro, azioni americane e Treasury, ogni fatto con data e fonte; la seconda, senza
  strumenti, legge quel riassunto e risponde con uno schema JSON: per ognuno dei quattro mercati, tutti
  obbligatori, una previsione intera da −20 a +20 per le cinque sedute successive, la fiducia, fino a tre ragioni e
  il rischio principale. Due chiamate perché l'output strutturato non si combina con le citazioni della ricerca. Il
  modello pensa prima di rispondere (è il suo comportamento predefinito); una risposta troncata non è una lettura.
  Al massimo tre tentativi al giorno, a mezz'ora l'uno dall'altro, sette minuti in tutto per lettura; ogni
  tentativo pagato resta anche in un registro fuori dal ramo dati, così un giro fallito non lo fa pagare due
  volte.
- **I libri.** `claude_oil` sul future micro WTI (/MCL), long e short, tetto 10x e 3x prima di una chiusura;
  `claude_gold` (GLD), `claude_equities` (SPY), `claude_bonds` (TLT), solo long, tetto 2x e 1x prima di una
  chiusura. Tutti con obiettivo di volatilità 0,50, stop giornaliero −15%, la regola di taglia di ogni libro
  (`previsione/10 × obiettivo × m / volatilità`, con l'allocatore e la fascia d'inerzia), una manica ciascuno
  (`sleeves` in `books.yaml`).
- **Che cosa legge una decisione.** La lettura più recente presa nel giorno della decisione o nei tre giorni prima,
  mai una presa dopo; più vecchia, previsione 0 e il libro si chiude; nessuna lettura mai presa, nessuna
  decisione (il libro resta liquido). Il lunedì legge quella del venerdì.
- **Che cosa costa, e che cosa lo ferma.** Senza il segreto `ANTHROPIC_API_KEY` nel repository nulla parte e i
  libri restano liquidi. Con la chiave, un tetto di 20 $ al mese calcolato dall'uso che l'API riporta ai prezzi
  pubblicati (Sonnet 5.5: 2 $ e 10 $ per milione di token in ingresso e in uscita, 0,01 $ a ricerca): stimato
  0,10-0,25 $ a lettura, 3-6 $ al mese. Ogni tentativo è una riga di `state/desk/claude-AAAA-MM.jsonl` con il
  riassunto, le fonti, la previsione, l'uso e il costo; la chiave non c'è mai. Un rifiuto, una risposta troncata o
  uno schema non rispettato non danno previsione quel giorno.
- **Che cos'è e che cosa non è.** Candidati speculativi (`forecast: claude`), ciascuno un conto a sé nel totale dei
  candidati, nel gruppo «Claude books» della pagina. Nessun backtest, e nessuno può esistere: un modello
  addestrato sul passato conosce i prezzi che un replay gli chiederebbe di prevedere (Glasserman e Lin, 2023).
  Fuori dal replay, dalle tabelle e dall'impronta; mai promossi; ritirati come ogni libro dopo 24 mesi con uno
  Sharpe sotto −0,5. Il modello non vede le posizioni, i prezzi del desk né la previsione dei sette segnali, e non
  decide taglie: le regole del desk lo fanno.
- **Il prior.** Nessun vantaggio: sono fra i mercati più liquidi del mondo e una notizia vi entra in minuti. Il
  valore, se c'è, è misurare quanto vale un analista automatico che legge le stesse notizie di tutti; servono mesi.
  Dopo 60 letture si controlla la scala (media di |F| intorno a 10) come per Kalshi.

### Il libro delle opzioni (`engine/desk/options.py`) — sperimentale

Vende uno **spread di put a credito** sotto il mercato quando la previsione (la stessa dei libri lineari, con i
sette segnali) è almeno +5:

- scadenza quotata più vicina a 30 giorni, fra 21 e 45;
- put venduta a delta 0,20; put comprata la più lontana che tiene la perdita massima entro il 5% del conto
  (mai un'ala più stretta del 3% del prezzo);
- entrambe le gambe quotate entro il 25% del loro prezzo, su una catena letta da meno di 45 minuti;
- vendita a denaro + un quarto dello spread, acquisto a lettera − un quarto;
- al massimo due strutture aperte, una nuova ogni dieci sedute, tenute fino a scadenza.

Opera su USO (WTI) e su BNO (Brent) quando le quotazioni lo consentono: l'8 ottobre 2026 quelle di BNO erano
larghe dal 35% a oltre il 100% del prezzo, quindi il libro ne resta fuori e lo scrive ogni giorno. Il lato
ribassista (spread di call) non ha mostrato margine nella simulazione e non viene negoziato.

- **Tesi.** È la stessa scommessa direzionale dei libri lineari, più il premio di volatilità sulla put venduta,
  con la perdita massima nota all'apertura.
- **Chi perde.** Chi compra protezione sotto il mercato in un trend rialzista e la paga più della mossa che
  arriva.
- **Quando non funziona.** Crolli improvvisi dentro un trend rialzista: nove operazioni su dieci chiudono in
  utile e una perde dieci volte tanto. Nella simulazione il risultato è nullo fra il 2007 e il 2019.

Numeri, sensibilità e ciò che è stato scartato: `docs/RESEARCH.md` e la pagina **Backtest** del sito.

### Il «Rocket» (`engine/desk/rocket.py`, `rocket_book` in `config/books.yaml`, fase 19) — una lotteria

Chiesto dal proprietario il 22 ottobre 2026: un conto da **1 000 $** che punta a 10 000 in tre mesi, a leva, che si
fa ripartire con un click. È una lotteria e lo dice ovunque: le regole di rischio del progetto sono sospese per lui
solo, per decisione del proprietario, e i suoi numeri non entrano in nessun totale. La regola, registrata prima di
misurare (`docs/RESEARCH.md`, capitolo 14 A):

- **Il segnale.** La direzione del Dinamico (la previsione combinata del fondo Brent: long da +5, short da −5) e
  l'accordo di Kalshi (la variazione in una settimana dell'indice di de-escalation: in discesa per un long, in
  salita per uno short; la sua ampiezza non conta). Una scommessa nuova vuole tutti e due.
- **La scommessa.** Long: call su USO; short: put su USO. La scadenza quotata più vicina a 30 giorni fra 21 e 45; fra
  le opzioni di quella scadenza fuori dal denaro e quotate entro il 25% del loro prezzo, quella con il delta più
  vicino a 0,30 se costa meno di metà del conto, altrimenti la successiva più lontana dal prezzo, fino a delta 0,10.
  Contratti interi, quanti ne stanno in metà del conto (a lettera meno un quarto dello spread, 0,05 $ a contratto),
  mai un nozionale in delta sopra 10 volte il conto. Una alla volta.
- **L'uscita.** Venduta (a denaro più un quarto dello spread) quando il Dinamico non punta più nella sua direzione;
  con 7 giorni o meno alla scadenza venduta e sostituita lo stesso giorno se il segnale tiene. Nessun obiettivo di
  guadagno. Non venduta entro la scadenza: regolata al valore intrinseco sulla chiusura ufficiale.
- **La vita.** A 50 $ (il 5%) il conto è morto; ma con contratti interi smette di poter scommettere prima, quando il
  contratto più economico ammesso costa più di metà del conto (circa 240 $ sulla catena del 16 ottobre): la pagina
  lo dice. Il pulsante «Start a new life» della sua pagina avvia il workflow `rocket-reset.yml`, che archivia la vita
  com'è e riparte da 1 000 $.

- **Tesi.** Nessun vantaggio oltre a quello del Dinamico: le opzioni comprate sul petrolio costano in media più del
  movimento che segue (capitolo 4). Il libro paga quel premio per la possibilità di un 10x, che viene solo da un
  crollo o da un'impennata che la volatilità implicita non aveva prezzato.
- **Le probabilità (un modello ≈, capitolo 14 E).** Su 811 finestre di tre mesi dal 2011: 10x nell'1,8%, tutte in tre
  crolli comprati con le put (autunno 2014, maggio 2015, inizio 2020); raddoppio nel 10,5%; metà del conto o più
  persa nel 50,1%; la mediana finisce a 0,50x. Nel 2026 nessuna delle 17 finestre è arrivata a 2x.
- **Replicabile.** Opzioni comprate su USO: livello 2 su Robinhood e su Alpaca (il conto di prova di Alpaca e la sua
  API sono gratuiti). La pagina del libro mostra l'ordine esatto nei due formati: il simbolo OCC, la quantità e il
  limite per l'API di Alpaca, i passi del modulo di Robinhood.

---

# Universo delle strategie del primo sistema

Ogni strategia ha un conto ombra da 10.000 $ (sempre a leva ≤ 1x) usato per la classifica, e un ciclo di vita:
**ricerca → incubazione (peso zero nel master) → attiva → ritirata** (decadimento rilevato con CUSUM sulla
performance live). Il passaggio a "attiva" richiede: Deflated Sharpe Ratio > 0 sull'insieme dei tentativi,
PBO < 50%, Sharpe out-of-sample positivo dopo costi raddoppiati, e almeno 100 operazioni o 3 anni di storia.
Le strategie che non superano i test **restano a peso zero** e la dashboard lo dice.

Convenzioni comuni: segnale = direzione, probabilità calibrata, rendimento e volatilità attesi sull'orizzonte, stop,
motivazione in italiano. I parametri sono in `config/strategies.yaml`. Tutte le feature sono point-in-time
(`engine/features/catalog.py`). Le serie di prezzo per i segnali sono roll-adjusted; il P&L usa il contratto reale.

Stato di validazione: vedi `docs/VALIDATION.md` (generato dalla fase 6) e la pagina **Strategie** della dashboard.

---

## Trend e momentum

### S1 · Momentum multi-orizzonte filtrato dal carry
- **Tesi.** I flussi di hedging dei produttori e consumatori sono lenti e prevedibili; le notizie fondamentali
  (OPEC, scorte, guerre) si propagano nei prezzi nell'arco di settimane. Il time-series momentum su 1–12 mesi cattura
  questa diffusione. Il carry (struttura della curva) è il filtro: un trend rialzista in backwardation è "pagato"
  (roll yield positivo) ed è coerente con scorte basse; un trend rialzista in contango lotta contro il carry.
- **Chi perde.** Hedger che vendono forward indipendentemente dal prezzo; investitori mean-reversion precoci;
  chi ribilancia meccanicamente contro il trend (indici commodity a pesi fissi).
- **Segnale.** Media dei segni di `tsmom_10..252` pesati per orizzonte, vol-scaled (`rv_yz_21`). Size piena se
  `sign(trend) == sign(slope_m1_m6)`, dimezzata se divergono, zero se `cot_crowding` > 0,9 nella stessa direzione.
- **Regimi favorevoli.** Trend persistenti (`hurst_100` > 0,55, `vr_20` > 1): "Contango + eccesso d'offerta +
  trend ribassista", "Squeeze rialzista". Sfavorevoli: "Range a bassa volatilità", "Shock/crash" (whipsaw).
- **Invalidazione.** Hurst < 0,45 per 60 giorni; inversione del segno dello slope senza inversione del trend;
  Sharpe rolling a 2 anni < 0 dopo costi.

### S2 · Breakout da compressione
- **Tesi.** La volatilità è ciclica: a periodi di compressione (vol realizzata nel decile basso) seguono espansioni.
  Quando il prezzo esce da un canale Donchian/ATR in regime compresso, l'informazione nuova non è ancora prezzata e
  gli stop di chi era in range alimentano il movimento.
- **Chi perde.** Venditori di volatilità e strategie range-bound che restano corte gamma oltre la rottura.
- **Segnale.** `vol_pctl_1y` < 0,25 nei 10 giorni precedenti **e** `donchian_pos_55` fuori da [0,1] (chiusura
  oltre il canale), conferma da volume > media 20g se disponibile. Stop a 2 ATR, trailing a 3 ATR.
- **Regimi favorevoli.** "Range a bassa volatilità" in uscita. Sfavorevoli: vol già alta (`vol_pctl_1y` > 0,7).
- **Invalidazione.** Tre falsi breakout consecutivi; hit rate < 35% su 50 operazioni.

### S3 · Trend adattivo (Kalman)
- **Tesi.** Un filtro di Kalman sul livello e sulla pendenza del prezzo stima il trend con rumore di osservazione
  che cresce con la volatilità: in regimi rumorosi reagisce lentamente, in regimi ordinati rapidamente. Riduce i
  whipsaw dei trend classici con finestre fisse.
- **Chi perde.** Trend follower a finestra fissa che vengono stoppati dal rumore; contrarian che combattono trend
  ordinati.
- **Segnale.** Pendenza filtrata / sua deviazione standard > soglia; rapporto segnale/rumore (Q/R) scalato con
  `rv_yz_21` e con la confidenza del regime. Size ∝ |pendenza normalizzata|, vol-scaled.
- **Regimi favorevoli.** Trend ordinati; neutro in transizione. Sfavorevoli: "Shock/crash".
- **Invalidazione.** Pendenza che cambia segno più di 6 volte in 60 giorni.

## Struttura a termine e carry

### S4 · Carry e roll yield
- **Tesi.** La backwardation segnala scorte basse e convenience yield alto (teoria dello storage): comprare il
  front e rollare incassa il roll yield, e le scorte basse rendono i prezzi fragili al rialzo. Il contango segnala
  eccesso d'offerta e paga per stare corti. Il carry è uno dei pochi premi al rischio documentati nelle commodity.
- **Chi perde.** Index investor long-only che rollano in contango; produttori che vendono differito in backwardation.
- **Segnale.** `roll_yield_ann` e il suo percentile 5 anni `roll_yield_pctl`: long se > 60°, short se < 40°,
  size ∝ |percentile − 0,5|, vol-scaled. Se la curva è proxy (`curve_approx` = 1) la size è dimezzata.
- **Regimi favorevoli.** Entrambi i regimi strutturali (backwardation con scorte basse, contango con glut).
  Sfavorevoli: "Shock/crash" (curva disordinata).
- **Invalidazione.** Correlazione roll yield–rendimento successivo < 0 su 3 anni rolling.

### S5 · Spread di calendario
- **Tesi.** Gli spread M1–M2, M1–M3 e Dec–Dec riflettono la tensione fisica pronta. Quando la stretta si consolida
  (spread che si allarga con scorte in calo e premio spot) lo spread ha momentum; dopo i picchi da evento (notizia
  geopolitica) il front torna indietro più del differito: mean reversion.
- **Chi perde.** Chi copre esposizioni fisiche all'ultimo momento (paga il pronto); speculatori che inseguono il
  picco del front.
- **Segnale.** Momentum dello spread (`spread_chg_5` concorde con `crude_stocks_vs_5y` < 0 e `spot_front_premium` > 0)
  → long spread (long M1 / short M3). Z-score dello spread > 2,5 in concomitanza con `geo_spike` = 1 e scorte
  non in calo → short spread (mean reversion). Posizione multi-gamba, beta flat basso.
- **Regimi favorevoli.** "Backwardation + vol alta + rischio geopolitico" (entrambe le gambe della logica).
- **Invalidazione.** Curva disponibile solo come proxy (nessuna operazione finché non c'è curva Brent reale per
  almeno 120 giorni: la strategia parte in incubazione).

### S6 · Butterfly di curva
- **Tesi.** La curvatura (M1 − 2·M3 + M6) è dominata da flussi tecnici (roll degli indici, hedge delle raffinerie
  su scadenze specifiche) e torna verso la forma di equilibrio: relative value a basso beta sul prezzo flat.
- **Chi perde.** Flussi di roll calendarizzati e hedger vincolati a una scadenza.
- **Segnale.** Z-score 1 anno di `butterfly_1_3_6`; entra oltre ±2, esce a 0,5; half-life stimata OU deve essere
  < 20 giorni. Tre gambe (+1, −2, +1).
- **Regimi favorevoli.** Qualsiasi regime con curva reale e liquida; sfavorevole: "Shock/crash".
- **Invalidazione.** Half-life > 40 giorni o curva proxy. Parte in incubazione fino a 120 giorni di curva reale.

### S7 · Conferma fisica
- **Tesi.** Un rally del flat price con spread fermi è premio speculativo/finanziario: tende a sgonfiarsi. Un rally
  con spread che si allargano insieme è una stretta fisica reale: va seguito. È anche la condizione 4 del gate
  della leva (§9) per i long sui picchi geopolitici.
- **Chi perde.** Chi compra il titolo di giornale senza guardare la curva; chi vende la stretta vera troppo presto.
- **Segnale.** `ret_5` > +1,5σ: se `spread_chg_5` ≤ 0 e `spot_front_premium` non sale → short (fade) con stop
  stretto; se `spread_chg_5` > 0 e `cushing_vs_5y` < 0 → long (follow). Specchio per i ribassi.
- **Regimi favorevoli.** "Backwardation + vol alta + rischio geopolitico". Sfavorevole: "Range a bassa volatilità".
- **Invalidazione.** Curva proxy (nessun fade senza spread reali).

## Inter-market e relative value

### S8 · Brent–WTI
- **Tesi.** Il differenziale è ancorato dall'arbitraggio dell'export USA (costo di trasporto Cushing–costa–Europa) e
  si allontana con scorte di Cushing estreme, colli di bottiglia logistici e premio geopolitico mediorientale
  (che colpisce il Brent più del WTI). Cointegrazione con z-score e soglie dipendenti dal regime.
- **Chi perde.** Chi tratta i due greggi come identici; esportatori/importatori con hedge rigidi.
- **Segnale.** Residuo della regressione rolling (3 anni) di Brent su WTI con driver `cushing_vs_5y`, `geo_index`,
  `crude_exports`: `brent_wti_z` oltre ±2 (±2,5 in regime geopolitico) → mean reversion sullo spread; uscita a 0,5.
- **Regimi favorevoli.** Tutti tranne "Shock/crash" (cointegrazione instabile).
- **Invalidazione.** Test di Johansen/ADF sul residuo non significativo per 2 finestre consecutive.

### S9 · Crack spread
- **Tesi.** I prodotti guidano il greggio quando la domanda finale sorprende: un rally dei crack (RBOB/HO vs greggio)
  anticipa l'aumento delle lavorazioni e della domanda di greggio. I crack estremi tornano verso i costi di
  raffinazione perché i margini attirano/espellono capacità rapidamente.
- **Chi perde.** Raffinatori con hedge statici; trader di greggio che ignorano i prodotti.
- **Segnale.** `product_lead` (prodotti − greggio a 5g) > 1σ → long greggio a 1 settimana; `crack_321_z` > 2,5 →
  short crack (short prodotti / long greggio), < −2,5 → long crack. Crack del diesel come conferma per il Brent.
- **Regimi favorevoli.** Stagione di guida e manutenzioni (crack volatili); sfavorevole: "Shock/crash".
- **Invalidazione.** Lead-lag (correlazione `product_lead` con `ret_5` successivo) non positiva su 3 anni.

### S10 · Fair value macro
- **Tesi.** Il petrolio ha beta variabili nel tempo verso dollaro, tassi reali, azionario e rame (domanda globale).
  Un filtro di Kalman stima i beta; quando il prezzo si scosta dal fair value macro senza conferma fondamentale
  (scorte, curva) tende a tornare. In regime geopolitico il residuo è dominato dal premio al rischio: si opera solo
  se il residuo non è spiegato dal `geo_index`.
- **Chi perde.** Flussi macro indiscriminati (risk-on/risk-off) che trascinano il petrolio fuori dai fondamentali.
- **Segnale.** `macro_fv_z` oltre ±2 e `geo_spike` = 0 e scorte non estreme → mean reversion verso il fair value.
- **Regimi favorevoli.** "Range a bassa volatilità", "Contango + eccesso d'offerta". Sfavorevole: geopolitico.
- **Invalidazione.** R² del modello < 0,2 su 2 anni rolling.

## Fondamentali ed eventi

### S11 · Sorpresa sulle scorte EIA
- **Tesi.** Il mercato reagisce alla sorpresa rispetto alle attese, non al livello. Non avendo il consenso, lo
  modelliamo: variazione attesa = stagionalità (media 5 anni della settimana) + tendenza recente + aggiustamento per
  import/lavorazioni. La sorpresa (specie su Cushing e sulla domanda implicita) genera un drift di 1–3 giorni perché
  i fondi aggiustano gradualmente.
- **Chi perde.** Chi tratta solo sul numero del titolo (crude) ignorando prodotti, Cushing e domanda implicita.
- **Segnale.** Mercoledì dopo le 10:30 ET: `stock_surprise_z` combinato (crude 40%, Cushing 25%, domanda implicita
  25%, lavorazioni 10%): sotto −1 → long 1–3 giorni; sopra +1 → short. Size ∝ |z|, cap 1x.
- **Regimi favorevoli.** Mercati guidati dai fondamentali; in regime geopolitico il peso è dimezzato.
- **Invalidazione.** Accuratezza direzionale post-report < 50% su 52 settimane (test binomiale).

### S12 · Playbook OPEC+
- **Tesi.** Prima delle riunioni la volatilità implicita sale e il mercato si posiziona; il drift post-decisione
  dura 2–5 giorni perché le dichiarazioni vengono digerite (quote effettive vs annunciate, compliance). La direzione
  pre-evento segue la tendenza del posizionamento e della curva, ma con leva ridotta per il rischio binario.
- **Chi perde.** Chi mantiene leva piena attraverso l'evento; chi inverte sulla prima reazione.
- **Segnale.** Da `config/events.yaml`: nei 2 giorni prima, posizione ridotta nella direzione del trend 21g
  (`tsmom_21`) se concorde con lo slope; nei 3 giorni dopo, segue il segno del rendimento del giorno dell'evento
  se > 1σ (drift). La leva master è tagliata da `L_evento`.
- **Regimi favorevoli.** Tutti; la componente drift è più forte in "Contango + eccesso d'offerta".
- **Invalidazione.** Drift post-evento non significativo su 12 riunioni consecutive.

### S13 · Premio geopolitico
- **Tesi.** I picchi di notizie (Iran, Hormuz, Houthi, sanzioni) creano un premio al rischio immediato con momentum
  iniziale di 1–3 giorni; se la curva e il fisico (spread, premio spot) non confermano l'interruzione reale
  dell'offerta, il premio si sgonfia nelle 1–3 settimane successive. Regole asimmetriche: entrata rapida sul picco,
  uscita immediata sulle notizie di de-escalation (il mercato scende più in fretta di quanto sale).
- **Chi perde.** Chi compra il picco e lo tiene senza conferma fisica; chi è corto durante l'escalation con stop
  troppo stretti.
- **Segnale.** `geo_spike` = 1 → long 2 giorni con stop 1,5 ATR. Dopo 3 giorni: se `spread_chg_5` ≤ 0 e
  `spot_front_premium` non sale → short (fade) fino a `geo_index` sotto la media 20g; `deescalation_flag` = 1 →
  chiudi ogni long e valuta short a 3 giorni.
- **Regimi favorevoli.** "Backwardation + vol alta + rischio geopolitico". Sfavorevole: "Range a bassa volatilità".
- **Invalidazione.** Ritorno medio post-picco non significativo su 30 episodi.

### S14 · Stagionalità seria
- **Tesi.** Manutenzioni di raffineria (feb–mar, set–ott), driving season (mag–ago), uragani (ago–ott), domanda di
  riscaldamento (nov–feb) creano pattern di scorte e crack. Solo gli effetti che superano test out-of-sample con
  correzione per test multipli vengono usati, e solo come **inclinazione** (tilt ±25% della size) mai da soli.
- **Chi perde.** Nessuno in modo diretto: è un premio di rischio stagionale pagato da chi deve coprirsi in date fisse.
- **Segnale.** Rendimento medio atteso per settimana dell'anno su 20 anni, filtrato per significatività
  (t > 2 dopo Bonferroni) → tilt sulle altre strategie; mai posizione autonoma.
- **Regimi favorevoli.** Mercati guidati dai fondamentali. Disattivato in "Shock/crash" e geopolitico.
- **Invalidazione.** Perdita di significatività nella finestra più recente (10 anni).

### S15 · Posizionamento COT
- **Tesi.** I managed money ai massimi di net length (percentile > 90% a 3 anni) con trend in esaurimento (slope del
  trend che si appiattisce, `vr_5` < 1) segnalano affollamento: lo sbilancio deve essere smontato. Vale anche come
  filtro: riduce le strategie trend quando sono affollate.
- **Chi perde.** L'ultimo entrato nel trend; i fondi che devono liquidare simultaneamente.
- **Segnale.** `cot_mm_net_brent_pctl` > 0,9 e `tsmom_21` in calo → short contrarian a 2 settimane; specchio sotto
  0,1. Filtro di affollamento `cot_crowding` applicato a S1–S3.
- **Regimi favorevoli.** Fine di trend lunghi; sfavorevole: "Shock/crash" (il posizionamento si azzera comunque).
- **Invalidazione.** Rendimento post-estremo non significativo su 40 episodi.

## Volatilità

### S16 · Regime di volatilità
- **Tesi.** Il premio per il rischio di volatilità (OVX − realizzata) è alto quando il mercato teme eventi: senza un
  evento in calendario i movimenti tendono a rientrare (mean reversion di 1–5 giorni). Quando la volatilità è compressa
  (OVX e realizzata entrambe basse) i breakout sono più affidabili: il regime decide la famiglia da favorire.
- **Chi perde.** Compratori di protezione sistematici (overpay); venditori di vol prima degli eventi.
- **Segnale.** `vrp` > 70° percentile e `hours_to_event` > 72 → mean reversion su `ret_1` oltre ±1,5σ; `vrp` <
  30° e `vol_pctl_1y` < 0,2 → abilita S2 con size piena. Opera direttamente sul future (nessuna opzione).
- **Regimi favorevoli.** "Backwardation + vol alta" per la mean reversion; "Range a bassa volatilità" per il breakout.
- **Invalidazione.** OVX non disponibile (prima del 2007 il conto ombra non opera).

### S17 · Reversal di breve
- **Tesi.** Nei regimi rumorosi ad alta volatilità (Hurst < 0,5) i movimenti di 1–5 giorni sono in parte liquidità e
  stop, e rientrano. Un processo OU stimato sui rendimenti a 5 giorni fornisce half-life e soglie; si opera solo in
  assenza di conferma fondamentale (nessuna sorpresa EIA, nessun picco di notizie).
- **Chi perde.** Chi insegue i movimenti intraday; stop forzati di posizioni a leva.
- **Segnale.** `hurst_100` < 0,5, `ret_5` oltre ±2σ (vol-scaled), `stock_surprise_z` neutro, `geo_spike` = 0 →
  posizione contraria con orizzonte = half-life OU (cap 5 giorni), stop 1,5σ.
- **Regimi favorevoli.** "Shock/crash" e geopolitico (con size ridotta). Sfavorevoli: trend ordinati.
- **Invalidazione.** Hurst > 0,55 persistente o half-life > 10 giorni.

### S18 · Opzioni sintetiche (approssimazione, facoltativa)
- **Tesi.** Prima di eventi binari (OPEC+, grandi report) la volatilità implicita è spesso inferiore a quella
  realizzata nel giorno dell'evento: un long straddle sintetico (Black-76 sul future con vol da OVX) guadagna se il
  movimento supera il premio. Con premio alto (OVX ≫ GARCH), strutture a rischio definito (iron condor sintetico).
- **Chi perde.** Chi vende volatilità a sconto prima degli eventi; chi la compra cara dopo.
- **Segnale.** `hours_to_event` < 48 e OVX < vol GARCH prevista → long straddle sintetico; OVX > GARCH·1,4 e nessun
  evento → short strangle a rischio definito. Prezzi dall'OVX con ipotesi di skew dichiarata; **etichettata approx**.
  Resta a peso zero nel master salvo validazione.
- **Regimi favorevoli.** Pre-evento; "Range a bassa volatilità" per la vendita di premio.
- **Invalidazione.** Senza dati di opzioni reali resta un'approssimazione: mai peso > 0 nel master.

## Machine learning e meta-livello

### S19 · Meta-labeling
- **Tesi.** Le strategie a regole hanno un buon richiamo ma precisione modesta. Un gradient boosting addestrato con
  triple-barrier labeling (stop, target, scadenza) sui segnali primari stima P(profitto) dalle feature di contesto
  (regime, vol, curva, COT, notizie) e la usa per la size. CV purged con embargo evita il leakage temporale; SHAP
  spiega la size scelta.
- **Chi perde.** Nessuno direttamente: migliora la precisione delle altre strategie.
- **Segnale.** Per ogni segnale primario: `prob` sostituita dalla probabilità calibrata del modello (isotonic);
  size = 0 se prob < 0,5. Retraining settimanale, walk-forward; modello versionato.
- **Regimi favorevoli.** Tutti; l'utilità dipende dalla quantità di segnali storici (richiede ≥ 300 segnali).
- **Invalidazione.** Brier score peggiore della calibrazione ingenua (0,5) su 6 mesi live.

### S20 · Allocatore regime-condizionato
- **Tesi.** La performance delle strategie dipende dal regime; i pesi devono adattarsi online. Hedge (pesi
  moltiplicativi) sulla performance out-of-sample nel regime corrente, con shrinkage verso il risk parity (umiltà) e
  penalità di correlazione (diversificazione reale). Decide anche l'ingresso nel gate "alpha forte".
- **Chi perde.** Nessuno direttamente: è il meta-livello del portafoglio.
- **Segnale.** Pesi w_i ∝ exp(η · Sharpe OOS nel regime) · shrink verso 1/vol_i, penalità per |ρ| > 0,5; aggiornamento
  settimanale; turnover limitato da isteresi.
- **Regimi favorevoli.** Tutti. In "Transizione" i pesi collassano verso il risk parity e la leva verso ≤ 1x.
- **Invalidazione.** Non applicabile: viene confrontato con l'equal weight e il risk parity nella dashboard.

---

## Famiglie (per il gate "accordo di almeno 3 famiglie poco correlate")

| Famiglia | Strategie |
|---|---|
| trend | S1, S2, S3 |
| carry | S4, S5, S6, S7 |
| relative_value | S8, S9, S10 |
| fundamental | S11, S14, S15 |
| event | S12, S13 |
| volatility | S16, S17, S18 |
| ml | S19 (sovrascrive la prob degli altri), S20 (allocatore) |

## S21 — Convinzione degli insider (SEC Form 4)

**Dato**: le dichiarazioni Form 4 che dirigenti, consiglieri e soci al 10% devono depositare quando trattano
azioni della propria società. Sono atti pubblici e dovuti per legge: qui non c'è nulla di non pubblico, e
nulla di tutto questo è un consiglio finanziario. Fonte: Alpha Vantage, che ripubblica i depositi SEC.

**Tesi economica**: chi dirige una società di esplorazione e produzione conosce il proprio costo marginale, i
tassi di declino dei giacimenti e il libro di copertura. Quando più dirigenti comprano azioni della propria
società sul mercato, con denaro proprio e già tassato, stanno esprimendo una view sull'economia del greggio a
termine che nessuna serie pubblicata contiene ancora. È una delle anomalie documentate più antiche
(Lakonishok e Lee 2001; Cohen, Malloy e Pomorski 2012 sulla differenza fra insider «di routine» e
«opportunisti»).

**Chi sta dall'altra parte**: chi legge gli stessi depositi come una nota di governance invece che come una
previsione sulla materia prima, e i flussi sistematici che prezzano il greggio solo su curva e scorte.

**Regimi favorevoli**: fasi laterali e punti di svolta, dove curva e momentum non dicono niente. È l'opposto
di una strategia da shock: un evento geopolitico muove il greggio molto prima di quanto chiunque possa
depositare un Form 4.

**Invalidazione**: il segnale salta quando il flusso è guidato da fatti societari — una fusione, una finestra
di riacquisto, un fondo che accumula una partecipazione — invece che dalla materia prima.

### Le tre trappole, misurate sui dati veri

| Trappola | Misura | Cosa fa il codice |
|---|---|---|
| La retribuzione non è convinzione | su ConocoPhillips solo **729 righe su 2.951 (25%)** sono operazioni di mercato in azioni ordinarie a un prezzo vero; il resto sono assegnazioni, maturazioni, esercizi di opzioni e ritenute fiscali, quasi tutte a prezzo 0 | solo le righe `open_market` entrano nel punteggio |
| Un socio al 10% non è un insider, ai fini di questa tesi | su Occidental **146 dei 258 acquisti di mercato** sono di un «10% Owner», mediana **17,2 M$**, massimo **564 M$**: è un fondo che accumula una partecipazione, cioè una view su un'azione, non sul greggio | i soci al 10% sono etichettati ed esclusi |
| Una sola operazione non può essere il punteggio | le dimensioni coprono quattro ordini di grandezza | ogni operazione è divisa per la mediana storica di quel ticker e troncata a ±3: un acquisto da 500 M$ e uno da 5 M$ dicono entrambi «ha comprato» |

### Il punto che decide tutto: la data di pubblicazione

Il feed porta **solo la data della transazione, mai quella di deposito**. Un Form 4 va depositato entro **due
giorni lavorativi** (17 CFR 240.16a-3), quindi trattare la data di transazione come il momento in cui il
mercato lo ha saputo sarebbe look-ahead — esattamente il difetto che `tests/test_no_lookahead.py` esiste per
catturare. Il motore usa quindi `published_at = data transazione + 2 giorni lavorativi`, che è il **più tardi**
in cui il deposito può legalmente comparire: i depositi reali sono spesso immediati, quindi la stima è
prudente e il motore non vede mai un'operazione prima di quando avrebbe potuto. È un'inferenza, non
un'osservazione, perciò la serie è sempre marcata `approx=true` e la dashboard mostra «≈».

### Segnale

* **Long** quando `insider_score` > 0,5 (quartile superiore del proprio storico a tre anni) **e** almeno
  **3 società distinte** hanno comprato nella finestra. La soglia di ampiezza è ciò che impedisce alla storia
  di una singola società di muovere il master.
* **Short** quando il punteggio < −0,7. L'asticella è più alta di proposito: un insider vende per comprare
  casa, per un divorzio o per una regola di diversificazione; per comprare c'è una ragione sola.
* **Nessun segnale** sotto 6 operazioni qualificanti nella finestra. Con circa nove acquisti di mercato per
  società all'anno, un trimestre silenzioso è normale, e il silenzio non va letto come una view piatta.
* Orizzonte **42 sedute (due mesi)**.

### Perché non è «tempo reale»

Non può esserlo, e vale la pena dirlo invece di lasciarlo intendere. La sola finestra di deposito è di due
giorni lavorativi; il flusso utile è di circa nove acquisti di mercato per società all'anno, quindi servono
settimane perché la finestra dica qualcosa. Il job che aggiorna la fonte è quello **settimanale**. Chi tratta
questo fattore in giornata sta trattando rumore.
