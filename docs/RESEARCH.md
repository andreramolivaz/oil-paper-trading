# Ricerca: che cosa regge sul greggio, che cosa no, con quali strumenti

Ottobre 2026. Questo documento spiega perché i libri del terminale sono fatti così: che cosa dice la
letteratura, che cosa abbiamo misurato sui dati veri, che cosa è stato scartato e con quali numeri. Dove un
risultato viene da un articolo che non è stato possibile leggere per intero è scritto; dove viene da un modello
e non da prezzi osservati è scritto.

## In breve

- **Regge**, con uno Sharpe fra 0,3 e 0,5 su un solo mercato: trend lento, carry (pendenza della curva) e
  carry-momentum, combinati a pesi uguali e dimensionati sulla volatilità. È ciò che comprano i tre libri lineari.
- **Non regge** dopo i costi: il momentum infragiornaliero in ogni forma provata, il ritorno alla media fra il
  fondo Brent e il fondo WTI, la vendita dei fondi a leva per incassarne il decadimento.
- **Le opzioni sul greggio costano più della mossa che segue**, in media dal 2007. Comprare call e put insieme
  «perché può salire ancora o scendere di brutto» è il lato che perde. Venderle con una copertura non incassa
  quel premio, una volta pagate le gambe: resta, di poco, lo spread di put venduto nella direzione del trend.
- **La leva che i dati sopportano è molto meno di 10x.** Con il greggio al 40-45% di volatilità, anche il
  libro più aggressivo (Kelly pieno) sta intorno a 1x. Il tetto di 10x esiste, ma lo si tocca solo con mercati
  calmi e previsione forte.
- **Niente di tutto questo è una certezza.** Uno Sharpe di 0,4 su quindici anni dista un errore standard e
  mezzo da zero. Servono circa 44 anni a 0,3 per arrivare a t = 2.

## 1. Perché il conto era fermo

Quattro cause indipendenti, tutte corrette (i test in `tests/test_tick_job.py` le fissano):

1. **Nessuna strategia promossa.** Il primo sistema portava una strategia sul conto solo dopo sette condizioni
   di validazione; in diciotto anni e mezzo nessuna delle 21 le ha superate, quindi il peso del conto era zero
   *per costruzione*. Non era un guasto: era il risultato. Ma un conto che non può operare non insegna niente.
2. **La fine giornata saltava.** GitHub avvia i lavori pianificati quando può: quello delle 19:40 partiva fra
   quattro e sei ore dopo, passata la mezzanotte di Londra, chiedeva la giornata «di oggi» che non era ancora
   chiusa e usciva con «troppo presto». Due giorni su tre.
3. **Una tabella facoltativa fermava tutto.** I dati sugli insider (Form 4) non venivano scaricati perché il
   loro adattatore non era registrato; la fonte risultava rossa e *qualsiasi* fonte rossa bloccava il nuovo
   rischio. Dal 7 ottobre il conto era in «dati non aggiornati». Ora solo i prezzi che servono a decidere
   possono fermare le decisioni. La catena degli insider era rotta in tre punti, non in uno: adattatore non
   registrato, tabella archiviata ma mai riletta, e modulo delle feature senza la funzione che il costruttore
   chiama (le colonne restavano vuote a ogni giro, con un avviso nel log). Tutti e tre corretti.
4. **Il ciclo da 30 minuti girava quattro volte al giorno.** Stessa causa del punto 2. Ora un unico lavoro
   resta acceso e fa un giro ogni mezz'ora; la pianificazione di GitHub serve solo a riaccenderlo.

Le prove sui dati veri dell'8 ottobre hanno poi trovato tre difetti del nuovo motore, corretti prima di
accenderlo: un libro avviato alle 14:48 di New York recuperava la decisione del giorno prima, prendeva quella
del giorno alle 15:18 con il primo ordine ancora in coda e finiva con il doppio della posizione (ora una
decisione sostituisce l'ordine in coda, non si somma); una barra da 30 minuti veniva letta tre minuti dopo la
sua fine, quando il flusso in ritardo non l'aveva ancora consegnata tutta (ora si aspetta un quarto d'ora); e
due aggiornamenti completi in un'ora bastavano a farsi rifiutare da Yahoo per troppe richieste (ora un giro
scarica solo le tabelle che la sua decisione legge).

## 2. Che cosa si può negoziare davvero (Robinhood, ottobre 2026)

| Strumento | Che cos'è | Leva | Costi | Note |
|---|---|---|---|---|
| **BNO** | fondo sul Brent: detiene il future ICE più vicino | 1x; 2x a margine (50% iniziale, 35% mantenimento) | nessuna commissione; 5,25% l'anno sul prestito | l'unico modo diretto di avere il Brent |
| **/MCL** | future micro sul WTI, 100 barili, regolato in contanti | circa 10x (margine ≈ 10% del nozionale, uguale di giorno e di notte) | circa 1,27 $ a contratto per lato | **non esiste un future sul Brent**: la leva passa dal WTI |
| **USO** | fondo sul WTI | come BNO | come BNO | le sue opzioni sono le più liquide sul greggio |
| **Opzioni su BNO e USO** | settimanali e mensili | spread a rischio definito | nessuna commissione, piccoli oneri | nessuna opzione sui future |
| **Contratti a evento** | «il Brent chiuderà sopra X?» | — | — | serie giornaliere, settimanali e mensili da luglio 2026 |

Tre conseguenze che il codice rispetta:

- **Rischio di base.** Il libro a leva segue il WTI, non il Brent. Nel 2026 la differenza fra i due è stata
  ampia (a ottobre circa 12 $ fra le due scadenze vicine); il terminale la mostra.
- **Un lotto è quasi tutto il conto.** 100 barili a 90 $ sono 0,9 volte un conto da 10 000 $. Il libro non
  può detenere «0,47x»: tiene zero o un contratto, e lo scrive nella motivazione di ogni decisione.
- **Nessuna automazione dei future.** Robinhood ha un'interfaccia ufficiale per agenti (azioni, opzioni,
  cripto, senza margine), non per i future. Portare il libro a leva su un conto reale vorrebbe dire inserire
  gli ordini a mano, o usare un altro intermediario.

## 3. Che cosa dice la letteratura

**Carry e carry-momentum (Bouchouev).** La regola meglio documentata sul Brent: stare lunghi quando la
pendenza della curva è sopra la sua media a 20 giorni, corti sotto. Rapporti d'informazione pubblicati sul
Brent, 1993-2020: carry 0,51, momentum di prezzo 0,09, carry-momentum 0,56. Dopo il 2016 il carry scende a
0,12 e circa metà del profitto 2016-20 del carry-momentum viene da marzo-aprile 2020. Lo stesso autore scrive
che il momentum di prezzo «da solo non funziona più bene» e che nel 2023 le strategie sistematiche sul
petrolio sono andate male.

**Trend (Moskowitz-Ooi-Pedersen; Hurst-Ooi-Pedersen; Carver).** Sharpe lordo del trend a 12 mesi sul greggio
intorno a 0,5-0,7 fino al 2009 (valori letti dai grafici degli articoli), più basso dopo; su panieri
diversificati è sceso molto nel decennio 2010. Da
Carver vengono le regole usate qui senza modifiche: medie mobili esponenziali 8-32, 16-64, 32-128, 64-256
scalate a una previsione media di 10 e limitate a ±20, volatilità su 35 giorni, fascia di inerzia del 10%,
obiettivo di rischio pari a metà del Kelly.

**Carry nelle materie prime (Koijen e altri; Gorton-Hayashi-Rouwenhorst).** Forte in sezione trasversale
(Sharpe 0,6), più debole su un mercato solo. Il rapporto AQR sul lungo periodo trova che il momentum funziona
soprattutto in backwardation.

**Posizionamento (COT).** Sul greggio i dati dicono poco: le posizioni dei gestori accompagnano il prezzo,
non lo anticipano.

**Volatilità implicita meno realizzata.** Segno conteso fra gli studi, potere esplicativo minimo: utile al più
per dimensionare, non per scegliere la direzione.

**Fondamentali e apprendimento automatico.** Nessun risultato verificato batte la passeggiata aleatoria sul
prezzo di chiusura a orizzonti da una settimana a un mese. I modelli neurali pubblicati mostrano margini lordi
che spariscono con 2-3 punti base di costo, e mai su un mercato solo.

**Infragiornaliero.** Il risultato più citato (la prima mezz'ora prevede l'ultima) vale 1,7 punti base lordi
per operazione sulle materie prime prese insieme, meno di un giro di costi. Sul greggio i test diretti danno
un potere esplicativo «essenzialmente nullo», e gli aggiornamenti al 2022 lo vedono svanire.

## 4. Che cosa abbiamo misurato sui dati veri

### Prima i dati

Tre serie «ovvie» erano sbagliate, e una strategia provata su di esse avrebbe misurato l'errore:

- `BZ=F` e `CL=F` non sono un contratto. Vicino alla scadenza le barre infragiornaliere di Yahoo alternano il
  contratto in scadenza e il successivo: con la backwardation del 2026 sono finti movimenti di 5-7 $ nella
  stessa ora, abbastanza da far scattare uno stop che nessun prezzo ha mai toccato.
- La serie giornaliera ha finti rendimenti nei giorni di cambio contratto (−5,9% il 1° ottobre 2026 e −11,3%
  il 1° aprile 2026, giorni in cui il fondo BNO non ha fatto nulla di simile).
- La vecchia serie continua cambiava contratto nel giorno sbagliato.

I libri usano quindi il prezzo del **fondo** (BNO) o del **singolo contratto** detenuto, lasciato cinque sedute
prima della scadenza. Il 20 aprile 2020, quando il WTI in scadenza chiuse a −37 $, un libro così era già da
tre sedute nel contratto successivo: è la differenza fra −306% e una brutta giornata. Il rendimento del WTI dal
1986 viene dai regolamenti EIA dei contratti 1 e 2 (esatto anche nei giorni di passaggio); la pendenza della
curva da una tabella per contratto che il motore archivia e accumula, perché Yahoo dimentica una scadenza il
giorno in cui muore.

### Le tre componenti

Rendimento esatto del WTI 1986-2024, obiettivo di volatilità 15%, 3 punti base di costo (misura di ricerca,
senza lotti):

| | Sharpe |
|---|---|
| Trend | 0,40 |
| Carry | 0,57 |
| Carry-momentum | 0,50 |
| Le tre insieme | 0,67 (t = 4,1; 0,70 / 0,80 / 0,53 in tre sottoperiodi; 0,58 solo long) |

Sul fondo Brent, con la pendenza del Brent vero dove esiste (2018-2026): 0,41, con 2023, 2024 e 2025 negativi e
il 2026 a +27%.

### I tre libri, con i lotti e i costi veri

Stesso motore del paper trading, un ordine deciso alla chiusura ed eseguito all'apertura successiva (la più
severa delle due ipotesi), capitale 10 000 $:

| Libro | Periodo | Rend. annuo | Volatilità | Sharpe (t) | Perdita max | Leva mediana / 95° / max | P(−50% in un anno) |
|---|---|---|---|---|---|---|---|
| Prudente (BNO, 1x) | 2011-2026 | +3,0% | 9% | 0,39 (1,5) | −25% | 0,08x / 0,81x / 0,94x | 0,0% |
| Dinamico (BNO, 2x) | 2011-2026 | +5,1% | 17% | 0,37 (1,5) | −47% | 0,17x / 1,52x / 1,96x | 0,0% |
| Spinto (/MCL, 10x) | 1986-2026 | +10,6% | 49% | 0,45 (2,9) | −89% | 1,07x / 3,04x / 7,44x | 20% |
| BNO comprato e tenuto | 2011-2026 | +4,0% | 35% | 0,29 | −87% | 1x | |
| WTI comprato e tenuto | 1986-2026 | +4,2% | 39% | 0,31 | −99% | 1x | |

Da leggere insieme a questi numeri:

- A costi doppi gli Sharpe sono 0,37 / 0,35 / 0,37. Eseguendo alla stessa chiusura: 0,35 / 0,33 / 0,51.
- Il libro prudente fa il 2021 a +24%, il 2022 a +19%, poi tre anni negativi (−4%, −5%, −7%) e il 2026 a
  +27%. Tre anni di fila in perdita sono normali per questa strategia.
- Il libro spinto ha avuto un giorno a −30% e una settimana a −39%. In un anno qualsiasi ha quasi nove
  probabilità su dieci di perdere un quarto del conto e una su cinque di perderne metà. È ciò che «Kelly
  pieno» significa, non un difetto da correggere con un parametro.
- Lo stop giornaliero non ferma un'apertura in gap: nel 2026 i fine settimana hanno prodotto aperture fino a
  +16%. Per questo il libro spinto scende a 3x prima di ogni chiusura dei mercati più lunga di un giorno.

### Scartato

| Idea | Risultato |
|---|---|
| Rottura dell'«area di rumore» infragiornaliera su BNO, USO, UCO, XLE (barre orarie) | Sharpe lordo fra −1,5 e −2,1 |
| Prima ora → ultima ora; seguito del mercoledì EIA; finestra di regolamento | nessun effetto, \|t\| sotto 1,7 |
| Rapporti di varianza orari sul contratto di dicembre | sotto 1 (0,93 a 4 ore, 0,76 a 23 ore): lieve ritorno alla media, troppo piccolo per i costi |
| Ritorno alla media dello spread BNO-USO | Sharpe fra −0,2 e −0,5 |
| Vendere insieme UCO e SCO (decadimento dei fondi a leva) | 0,96 sull'intero campione, −0,36 dal 2022 |

### Osservato, non negoziato

- **La notte paga, il giorno no.** Su BNO dal 2010 il rendimento fra la chiusura e l'apertura successiva nei
  giorni feriali è +8,7 punti base (t = 3,3); quello dall'apertura alla chiusura è circa zero. I libri tengono
  le posizioni di notte comunque: una regola dedicata vorrebbe dire due esecuzioni al giorno per 9 punti base.
- **Le grandi giornate al rialzo proseguono**: dopo una seduta oltre 2,5 deviazioni standard, +114 punti base
  nei tre giorni seguenti (t = 2,0). Poche osservazioni, t al limite: annotato, non usato.

### Volatilità e opzioni

- L'indice OVX (volatilità implicita a 30 giorni delle opzioni su USO) è stato sopra la volatilità poi
  realizzata di 5,8 punti in media, quattro giorni su cinque; nel 2026 di 10,8 punti.
- Comprare uno straddle alla pari ogni settimana ha perso lo 0,66% del nozionale a settimana (t = −6,7, stima
  da OVX).
- **Le quotazioni vere decidono più del modello.** L'8 ottobre 2026 le opzioni su USO erano quotate fra il 4%
  e il 13% del prezzo sulle scadenze liquide; quelle su BNO fra il 35% e oltre il 100%, con una catena ferma da
  due ore. Una strategia che «funziona» a metà prezzo può perdere tutto attraversando quegli spread: per questo
  il libro delle opzioni legge denaro e lettera veri (Cboe, 15 minuti di ritardo) e non compra né vende a un
  prezzo che il mercato non ha mostrato.

Simulazione **su modello** (Black-Scholes sull'OVX, 2007-2026; non esiste uno storico gratuito di quotazioni):

| Struttura | Sharpe |
|---|---|
| Comprare lo straddle, sempre | −0,63 |
| Vendere condor o farfalle coperte, sempre | fra −0,2 e −0,8 con lo smile; la farfalla torna positiva solo a volatilità piatta |
| Spread a debito nella direzione della previsione | 0,2-0,5, con perdite massime oltre il 35% |
| Spread di put a credito quando la previsione è lunga (il libro) | fra 0,42 e 0,71 secondo smile e costi; +2-4% l'anno; perdita max −14% |
| Lo stesso sul lato ribassista (spread di call) | circa zero: non si tratta |

Il risultato positivo dipende dall'ampiezza dell'ala comprata: sotto il 3% del prezzo sparisce. Per questo il
libro compra l'ala più larga che il 5% del conto consente, e sotto il 3% non opera. È un margine piccolo,
quasi tutto nella parte direzionale, nullo fra il 2007 e il 2019: **sperimentale** a tutti gli effetti.

### Hormuz

I transiti di petroliere nello Stretto, dai segnali AIS raccolti dall'FMI (PortWatch), sono passati da circa 50
al giorno a circa uno da marzo 2026. È un dato settimanale, pubblicato il martedì per la settimana chiusa la
domenica: contesto, non segnale. Il terminale lo mostra accanto al prezzo; nessuna regola lo usa, perché con
un solo episodio nella storia disponibile ogni regola sarebbe un racconto.

### Contratti a evento sul Brent

Le serie «il Brent chiuderà sopra X» esistono da luglio 2026 (la giornaliera dal 31): 4,1 milioni di contratti
scambiati sulla giornaliera in settanta giorni, differenza denaro-lettera di 1,5-2 centesimi vicino al prezzo (6 sulla
mensile), poche centinaia di contratti da un dollaro per livello. Si potrebbero confrontare con la probabilità
implicita nelle opzioni; con settanta giorni di storia e quei volumi è un esperimento da poche centinaia di
dollari. **Non implementato**: i dati sono stati scaricati e descritti, niente di più.

## 5. Quanta leva

Per una strategia con Sharpe atteso *S*, la volatilità che massimizza la crescita è *S* stessa (Kelly). Con
*S* = 0,5: 50% l'anno è il massimo sensato, 25% è mezzo Kelly, 12% un quarto. Sono i tre obiettivi dei libri.
Oltre il 50% la crescita attesa non sale: scende, e sale la probabilità di rovina.

L'esposizione è `previsione / 10 × obiettivo di volatilità / volatilità corrente`. L'8 ottobre 2026, con il
Brent al 44% di volatilità e la previsione a +5,7, fa 0,16x per il libro prudente e 0,33x per il dinamico; per
il libro sul WTI (previsione +3,9, volatilità 42%) fa 0,47x, cioè meno di un contratto. La leva è un
*risultato*: arriva a 3x-7x solo quando il greggio è calmo (15-20% di volatilità) e trend e curva sono
d'accordo.

## 6. Limiti

- **Quindici anni per il Brent, quaranta per il WTI, un solo mercato.** Gli errori standard sono larghi quanto
  i risultati.
- **La curva del Brent per contratto esiste solo da quando il motore la archivia.** Prima, la pendenza usata
  per BNO è quella del WTI: 51% delle sedute, marcate come approssimazione.
- **Prezzi in ritardo di 10-15 minuti** (Yahoo, non ufficiale) e catene di opzioni in ritardo di 15 (Cboe).
- **Assegnazione anticipata e rischio a scadenza delle opzioni non sono modellati.**
- **Strategie «non pubbliche».** Quello che un desk proprietario non pubblica non si recupera con una
  ricerca: qui c'è ciò che è documentato e ciò che abbiamo potuto misurare. Chi promette altro vende.
- **La ricerca in rete di questa sessione ha esaurito il suo limite** prima di chiudere alcuni punti: i
  numeri di alcuni articoli (indicati sotto) vengono dal solo riassunto.

## 7. Prossimi passi possibili

1. Lasciar girare i quattro libri: il libro delle opzioni, in particolare, costruisce l'unico storico di
   quotazioni reali di cui disporremo.
2. Curva del Brent reale: fra qualche mese la pendenza di BNO non avrà più bisogno della procura WTI.
3. Contratti a evento contro la probabilità implicita nelle opzioni, in piccolo.
4. Replica di Ewald e altri (2025) sulla stagionalità infragiornaliera del Brent, l'unico lavoro che dichiara
   un margine dopo i costi: richiede dati al minuto.

## Fonti

Strumenti e regole di negoziazione (verificati l'8 ottobre 2026):

- [Robinhood, prodotti future](https://robinhood.com/us/en/about/futures/) ·
  [aprire un conto future](https://robinhood.com/us/en/support/articles/get-started-with-a-futures-account/) ·
  [prima di negoziare un future: commissioni e margini](https://robinhood.com/us/en/support/articles/before-trading-a-futures-contract/) ·
  [scadenza dei future](https://robinhood.com/us/en/support/articles/futures-contract-expiration/) ·
  [margin call sui future](https://robinhood.com/us/en/support/articles/futures-deficits-and-margin-calls/) ·
  [tassi del margine](https://robinhood.com/us/en/support/articles/margin-rates/) ·
  [tariffario Robinhood Derivatives](https://cdn.robinhood.com/assets/robinhood/legal/RHD_Fee_Schedule.pdf)
- [CME, i future arrivano su Robinhood (29 gennaio 2025)](https://www.cmegroup.com/media-room/press-releases/2025/1/29/cme_group_futurestolaunchonrobinhoodbringingnewtradingopportunit.html) ·
  [regolamento NYMEX, capitolo 200 (CL)](https://www.cmegroup.com/rulebook/NYMEX/2/200.pdf)
- [Robinhood per gli agenti](https://robinhood.com/us/en/newsroom/robinhood-is-now-open-to-agents/) ·
  [negoziare con un agente](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
- [Robinhood, contratti a evento sul Brent](https://invest.robinhood.com/us/en/prediction-markets/commodities/brent-crude/) ·
  [Kalshi, condizioni dei contratti sulle materie prime](https://assets.kalshi.com/contract_terms/COMMODITIES.pdf)
- [CFTC, 20 aprile 2020](https://www.cftc.gov/PressRoom/PressReleases/8315-20)

Dati:

- [IMF PortWatch](https://portwatch.imf.org) (transiti negli stretti)
- Cboe, quotazioni ritardate delle opzioni: `https://cdn.cboe.com/api/global/delayed_quotes/options/{SIMBOLO}.json`
- [Alpha Vantage, chiave gratuita](https://www.alphavantage.co/support/#api-key) (Form 4)
- EIA, FRED, Yahoo, CFTC, ICE: vedi `docs/DATA_SOURCES.md`

Letteratura (letta per intero salvo dove indicato):

- Bouchouev e Zuo, [Oil risk premia under changing regimes, GCARD 2020](https://jpmcc-gcard.com/digest-uploads/2020-winter/issue-pages/Page%2049_59%20GCARD%20Winter%202020%20Bouchouev.pdf);
  Bouchouev, commenti OIES [2024](https://www.oxfordenergy.org/wpcms/wp-content/uploads/2024/03/Energy-Quantamentals-%5EN2-Myths-and-Realities-about-CTAs-Final.pdf),
  [2025 sul premio di volatilità](https://www.oxfordenergy.org/wpcms/wp-content/uploads/2025/01/Energy-Quantamentals-The-Revival-of-the-Volatility-Risk-Premium.pdf),
  [2025 su algoritmi e OPEC](https://www.oxfordenergy.org/wpcms/wp-content/uploads/2025/06/Comment-Energy-Quantamentals-A-Tale-of-Two-Algorithms-and-OPEC.pdf),
  [2026 sulla crisi](https://www.oxfordenergy.org/wpcms/wp-content/uploads/2026/04/Comment-Energy-Quantamentals-8-Oil-Crisis-in-the-Eyes-of-a-Financial-Trader.pdf);
  *Virtual Barrels* ([Springer 2023](https://link.springer.com/book/10.1007/978-3-031-36151-7), solo i riassunti dei capitoli)
- Moskowitz, Ooi, Pedersen, [Time series momentum](http://docs.lhpedersen.com/TimeSeriesMomentum.pdf);
  Hurst, Ooi, Pedersen, [A century of evidence on trend-following investing](https://fairmodel.econ.yale.edu/ec439/hurst.pdf);
  Baltas e Kosowski, [Demystifying time-series momentum strategies](https://www.cmegroup.com/education/files/demystifiing-time-series-momentum-strategies.pdf)
- Carver: [regola EWMAC](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/systems/provided/rules/ewmac.py),
  [parametri predefiniti](https://github.com/robcarver17/pysystemtrade/blob/master/sysdata/config/defaults.yaml),
  [quanto rischio prendere](https://qoppac.blogspot.com/2020/03/how-much-risk-should-we-take.html),
  [conti piccoli e diversificazione](https://qoppac.blogspot.com/2016/03/diversification-and-small-account-size.html)
  (i libri non sono stati aperti: le formule vengono dal codice e da riproduzioni di terzi)
- Koijen, Moskowitz, Pedersen, Vrugt, [Carry](http://docs.lhpedersen.com/Carry.pdf);
  Gorton, Hayashi, Rouwenhorst, [The fundamentals of commodity futures returns](https://www.nber.org/system/files/working_papers/w13249/w13249.pdf);
  Boons e Prado, [Basis-momentum](https://conference.nber.org/conf_papers/f89296/f89296.pdf);
  AQR, [Commodities for the long run](https://www.nber.org/system/files/working_papers/w22793/w22793.pdf)
- Kang, Rouwenhorst, Tang, [A tale of two premiums](https://conference.nber.org/conf_papers/f69870/f69870.pdf) (posizionamento)
- Ornelas e Mauad, [Volatility risk premia and future commodity returns](https://www.bis.org/publ/work619.pdf);
  Harvey e altri, [The impact of volatility targeting](https://people.duke.edu/~charvey/Research/Published_Papers/P135_The_impact_of.pdf)
- Wen, Indriawan, Lien, Xu, [Intraday return predictability in the crude oil market](https://digital.library.adelaide.edu.au/dspace/bitstream/2440/141224/2/hdl_141224.pdf);
  Baltussen, Da, Lammers, Martens, [Hedging demand and market intraday momentum](https://www3.nd.edu/~zda/intramom.pdf);
  Zarattini, Aziz, Barbon, [Beat the market: an effective intraday momentum strategy](https://alexandria.unisg.ch/server/api/core/bitstreams/a99aba00-f967-49b3-aceb-f544dc386e0b/content);
  Wen, Gong, Ma, Xu, [Intraday momentum and return predictability: evidence from the crude oil market](https://ideas.repec.org/a/eee/ecmode/v95y2021icp374-384.html) (solo riassunto)
- Smith-Meyer, Haugom, Ewald, [efficienza del Brent per frequenza](https://eprints.gla.ac.uk/363709/1/363709.pdf);
  Ewald e altri, [Intra-day seasonality and abnormal returns in the Brent crude oil futures market](https://eprints.gla.ac.uk/363707) (solo riassunto)
- Lim, Zohren, Roberts, [Enhancing time series momentum strategies using deep neural networks](https://arxiv.org/abs/1904.04912);
  Wood, Roberts, Zohren, [Slow momentum with fast reversion](https://arxiv.org/abs/2105.13727)
