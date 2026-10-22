# Ricerca: che cosa regge sul greggio, che cosa no, con quali strumenti

Ottobre 2026. Questo documento spiega perché i libri del terminale sono fatti così: che cosa dice la
letteratura, che cosa abbiamo misurato sui dati veri, che cosa è stato scartato e con quali numeri. Dove un
risultato viene da un articolo che non è stato possibile leggere per intero è scritto; dove viene da un modello
e non da prezzi osservati è scritto.

## In breve

- **Regge**, con uno Sharpe fra 0,5 e 0,75 su un solo mercato: una previsione fatta di sette segnali da tre
  fonti che pesano un terzo ciascuna. Il prezzo del greggio (trend, accelerazione del trend, asimmetria dei
  rendimenti), la curva dei future (carry, carry-momentum) e altri mercati (trend del rame e del dollaro). È
  ciò che comprano i tre libri lineari. Fino all'8 ottobre 2026 i segnali erano tre e lo Sharpe 0,35-0,45:
  il capitolo 4 dice che cosa è stato aggiunto, che cosa è stato scartato e quanto di quel miglioramento è
  selezione.
- **Il lato short conta, e costa.** Un fondo non si vende allo scoperto; il libro «dinamico» va short
  comprando SCO, il fondo che ogni giorno rende −2 volte il WTI. Sul fondo Brent 2011-2026 porta lo Sharpe da
  0,48 a 0,76, ma quasi tutto il guadagno viene da tre anni di crollo (2014, 2015, 2020); in dieci degli altri
  tredici ha tolto qualcosa.
- **Non regge** dopo i costi: il momentum infragiornaliero in ogni forma provata, il ritorno alla media fra il
  fondo Brent e il fondo WTI, la vendita dei fondi a leva per incassarne il decadimento. E, fra i sedici
  segnali giornalieri provati a ottobre 2026: posizionamento e flussi (COT), scorte EIA, margine di
  raffinazione, premio di volatilità, insider, valore a cinque anni.
- **Le opzioni sul greggio costano più della mossa che segue**, in media dal 2007. Comprare call e put insieme
  «perché può salire ancora o scendere di brutto» è il lato che perde. Venderle con una copertura non incassa
  quel premio, una volta pagate le gambe: resta, di poco, lo spread di put venduto nella direzione del trend.
- **La leva che i dati sopportano è molto meno di 10x.** Con il greggio al 40-45% di volatilità, anche il
  libro più aggressivo (Kelly pieno) sta intorno a 1x. Il tetto di 10x esiste, ma lo si tocca solo con mercati
  calmi e previsione forte. Gli obiettivi di volatilità dei libri **non** sono stati alzati insieme allo Sharpe
  misurato.
- **Niente di tutto questo è una certezza.** Uno Sharpe di 0,56 su quindici anni dista poco più di due errori
  standard da zero. Il 2023, il 2024 e il 2025 sono stati negativi per tutti e tre i libri, con la previsione
  vecchia e con la nuova. E oltre 0,7-0,8 su un solo mercato non si va onestamente: per salire servono altri
  mercati indipendenti, non più leva e non altri parametri.

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
| **SCO** | fondo che ogni giorno rende −2 volte un indice di future sul WTI (ProShares UltraShort Bloomberg Crude Oil) | −2x al giorno; comprato in contanti | nessuna commissione; 0,95% l'anno dentro il prezzo | il modo di essere short senza prendere a prestito azioni; su più giorni non rende −2 volte il greggio |
| **Opzioni su BNO e USO** | settimanali e mensili | spread a rischio definito | nessuna commissione, piccoli oneri | nessuna opzione sui future |
| **Contratti a evento** | «il Brent chiuderà sopra X?» | — | — | serie giornaliere, settimanali e mensili da luglio 2026 |

Tre conseguenze che il codice rispetta:

- **Rischio di base.** Il libro a leva segue il WTI, non il Brent. Nel 2026 la differenza fra i due è stata
  ampia (a ottobre circa 12 $ fra le due scadenze vicine); il terminale la mostra.
- **Un lotto è quasi tutto il conto.** 100 barili a 90 $ sono 0,9 volte un conto da 10 000 $. Il libro non
  può detenere «0,47x»: tiene zero o un contratto, e lo scrive nella motivazione di ogni decisione.
- **Lo short di un fondo passa da un altro fondo.** Il conto che i libri imitano non prende a prestito azioni
  di BNO. Per essere short il libro «dinamico» *compra* SCO: in contanti, mai oltre il valore del conto, metà
  dei dollari per la stessa esposizione. Segue il WTI e non il Brent (altro rischio di base), si ricalcola ogni
  giorno (in un mercato che oscilla senza direzione perde anche se il greggio non sale) e non può perdere più
  di ciò che vi è investito. La pagina del fondo avverte che SCO genera un modulo fiscale K-1.
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

**Accelerazione e asimmetria (Carver).** Due regole pubblicate con il sistema di Carver
(`pysystemtrade`, configurazione `rob_system`) e usate qui con i suoi parametri. L'*accelerazione* è il trend
di oggi meno quello di *n* giorni fa (16, 32 e 64 giorni): un trend ancora positivo che perde forza dà un
segnale negativo. L'*asimmetria* (skew) compra i mercati i cui rendimenti sono stati più asimmetrici al ribasso
del solito, perché chi sopporta cadute rare e grandi viene pagato per farlo; finestre di 180 e 365 giorni. Una
differenza dichiarata: Carver confronta lo skew di un mercato con la media di tutti i suoi mercati, un mercato
solo può confrontarlo soltanto con la propria storia.

**Altri mercati (rame, dollaro).** Il greggio segue il ciclo globale con ritardo, e il rame e il dollaro lo
portano con meno shock d'offerta propri; il greggio è prezzato in dollari, quindi un dollaro che scende lo
sostiene. Che i rendimenti del petrolio siano in parte prevedibili da variabili finanziarie è un risultato
vecchio e contestato; qui non si cita un articolo per i parametri perché non ce ne sono: ai due mercati si
applica, senza cambiare nulla, la stessa regola di trend del greggio.

**Posizionamento (COT).** Sul greggio i dati dicono poco: le posizioni dei gestori accompagnano il prezzo,
non lo anticipano. Kang, Rouwenhorst e Tang trovano un premio per chi fornisce liquidità agli speculatori su
un paniere di materie prime; sul solo greggio, qui, non si vede (capitolo 4).

**Volatilità implicita meno realizzata.** Segno conteso fra gli studi, potere esplicativo minimo: utile al più
per dimensionare, non per scegliere la direzione. Ellwanger (2015) trova che i premi per il rischio di coda
estratti dalle opzioni prevedono i rendimenti del greggio, ma servono le opzioni sui future, che qui non ci
sono. Chevallier e Sévi (2013) trovano una relazione di segno negativo fra premio di varianza e rendimenti del
WTI. La procura provata qui (OVX meno volatilità realizzata) aveva il segno delle azioni, positivo, e ha perso
(capitolo 4).

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

### Le tre componenti (la previsione fino all'8 ottobre 2026)

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

### Da tre a sette segnali: sedici candidati, quattro tenuti

La domanda era se la previsione potesse avere uno Sharpe più alto. Con più leva no: la leva moltiplica
rendimento e rischio insieme e lo Sharpe non si muove. Con parametri migliori nemmeno, se «migliori» vuol dire
scelti guardando il risultato. Lo Sharpe sale solo aggiungendo fonti di rendimento che non siano la stessa
scommessa di quelle che ci sono già.

**Metodo.** Sedici segnali, ognuno con una ragione pubblicata e con i parametri del suo autore, scritti prima
di guardare un risultato e provati una volta sola sulle serie dei libri (WTI 1985-2026, fondo Brent 2010-2026):
obiettivo di volatilità 15%, 3 punti base sul nozionale scambiato, fascia di inerzia, posizione presa sul dato
del giorno e applicata al rendimento del giorno dopo. Ogni segnale è poi stato guardato contro lo stare
semplicemente lunghi, con un giorno di ritardo, con i parametri vicini e nelle due metà del campione. Qui sotto
ci sono tutti e sedici, non solo quelli tenuti. (Lo Sharpe del fondo è quello long e short, per confrontare i
segnali fra loro; t fra parentesi.)

| Segnale | Da dove viene | WTI | prima / seconda metà | Fondo Brent | Esito |
|---|---|---|---|---|---|
| **Accelerazione** del trend, 16-32-64 giorni | Carver | 0,31 (2,0) | 0,25 / 0,37 | 0,60 | **tenuto** |
| **Asimmetria** (skew) a 180 e 365 giorni | Carver | 0,47 (2,9) | 0,68 / 0,26 | 0,12 sulla propria storia; 0,33 con il segnale del WTI | **tenuto**, letto sul WTI |
| Trend del **rame** | la regola di trend del desk | 0,59 (3,0) | 0,56 / 0,63 | 0,66 | **tenuto** |
| Trend del **dollaro**, invertito | la regola di trend del desk | 0,29 (1,9) | 0,07 / 0,52 | 0,67 | **tenuto** |
| Rottura del canale (breakout) a 40-320 giorni | Carver | 0,29 (1,9) | 0,40 / 0,17 | 0,39 | scartato: correlato 0,79 con ciò che c'è, è lo stesso trend |
| Carry continuo (pendenza divisa per la volatilità) | Carver | 0,34 (2,2) | 0,58 / 0,03 | −0,21 | scartato: peggio del semplice segno |
| Trend preso solo dove la curva è d'accordo | Fuertes, Miffre, Rallis | 0,40 (2,6) | 0,49 / 0,30 | 0,40 | scartato: correlato 0,88, non aggiunge nulla |
| Valore: prezzo di cinque anni fa contro oggi | Asness, Moskowitz, Pedersen | −0,09 | −0,12 / −0,07 | −0,43 | scartato |
| Flusso settimanale degli speculatori, al contrario | Kang, Rouwenhorst, Tang | 0,15 (0,7) | 0,19 / 0,10 | 0,11 | scartato |
| Pressione di copertura a 52 settimane | Basu, Miffre | −0,26 | −0,65 / 0,10 | −0,15 | scartato |
| Crescita dell'open interest a 12 mesi | Hong, Yogo | 0,41 (1,8) | 0,65 / 0,17 | 0,31 | scartato: a 6 mesi fa −0,04, a 24 mesi 0,46 |
| Scorte EIA sotto la norma stagionale | Ye, Zyren, Shore | 0,09 | 0,20 / −0,01 | 0,03 | scartato |
| Scorte EIA in calo su 4 settimane | teoria dello stoccaggio | 0,19 (1,2) | 0,18 / 0,21 | 0,24 | scartato: a 2 settimane −0,04, a 8 settimane 0,01 |
| Margine di raffinazione 3-2-1 sopra la sua media | — | 0,31 (1,4) | 0,05 / 0,57 | 0,47 | scartato: con un giorno di ritardo 0,05 e 0,12 |
| Premio di volatilità (OVX meno realizzata) | per analogia con le azioni | −0,35 | −0,54 / −0,16 | −0,14 | scartato |
| Acquisti degli insider (Form 4) | la strategia S21 | 0,07 | 0,18 / −0,04 | 0,22 | scartato |

Che cosa dicono le righe, oltre al numero:

- **Il margine di raffinazione era un artefatto di orario.** Sembrava il migliore dei fondamentali, ma i prodotti
  raffinati chiudono su Yahoo alle 17:00 di New York e il greggio è regolato alle 14:30: il segnale «del giorno»
  conteneva due ore e mezza di futuro. Con un giorno di ritardo sparisce. I quattro tenuti, con lo stesso
  ritardo, non si muovono: accelerazione 0,30, skew 0,46, rame 0,58, dollaro 0,28.
- **Un segnale che cambia segno con la finestra accanto non è un segnale.** Open interest e scorte danno
  numeri decenti alla finestra pubblicata e zero o meno a quella vicina. Sono fuori anche se, aggiunti agli
  altri, avrebbero dato la cifra più alta di tutte (vedi sotto).
- **Posizionamento e flussi non dicono nulla sul solo greggio**, come già scritto dalla letteratura: il premio
  che Kang, Rouwenhorst e Tang misurano è su un paniere.
- **Il premio di volatilità è stato provato con il segno sbagliato, e resta fuori lo stesso.** Scritto prima
  come per le azioni (premio alto: lunghi), ha dato −0,35. Rovesciato darebbe +0,35 con t = 1,5, ed è il segno
  che riportano Chevallier e Sévi. Non entra: sarebbe scegliere il segno dopo aver visto il risultato, e a
  t = 1,5 non si distingue dal caso. È il primo candidato da riguardare fra un anno, sui dati che oggi non
  esistono.
- **Rame e dollaro: guardati alle quattro velocità del trend, non a una.** Erano stati scritti con una sola
  coppia di medie (16-64). Guardando le coppie vicine il rame dà 0,42 / 0,59 / 0,60 / 0,58 e il dollaro
  0,33 / 0,29 / 0,29 / 0,38: nessuna è scelta, si usano tutte e quattro, cioè la stessa regola del trend del
  greggio (rame 0,65, t = 3,3 su 25 anni; dollaro 0,38, t = 2,4 su 42). Il dollaro vale quasi zero fino al 2005
  (0,13) e 0,65 dopo: è un legame recente. Insieme fanno 0,77 sul WTI e 0,92 sul fondo, **ma sono stati piatti
  dal 2022 al 2025** (+0,6%, −0,3%, −1,8%, −6,5%) e sono tornati positivi nel 2026.
- **È il ciclo globale, non il rame.** Lo stesso trend sull'indice S&P 500 dà 0,29 e sugli energetici (XLE)
  0,38. Non sono stati aggiunti: non erano nell'elenco scritto prima, XLE è in buona parte il greggio stesso
  (correlato 0,65-0,73 con il suo trend) e l'S&P 500 sul fondo Brent dà 0,18.
- **Lo skew del fondo Brent, da solo, non funziona** (0,12): sedici anni non bastano a dire che cosa è «il
  solito» per un'asimmetria misurata su un anno. Lo skew è quindi calcolato una volta sola, sulla storia lunga
  del WTI, e letto da entrambi i veicoli. Sul WTI regge a tre modi diversi di togliere la media (0,35-0,47); la
  finestra a 180 giorni da sola dà 0,55, quella a 365 dà 0,29: si usano entrambe, come pubblicato. La seconda
  metà del campione (0,26) è molto più debole della prima (0,68).

**Come si combinano.** Tre fonti di informazione, un terzo ciascuna, e pesi uguali dentro ogni fonte: il prezzo
del greggio (trend, accelerazione, skew), la curva (carry, carry-momentum), gli altri mercati (rame, dollaro).
Il moltiplicatore di diversificazione viene dalla formula di Carver, `1 / √(w′Cw)`, sulle correlazioni misurate
fra le sette previsioni: 1,77 sul WTI e 1,73 sul fondo, arrotondato a 1,75. Con quel moltiplicatore la
previsione media vale 8 in valore assoluto e tocca il tetto di ±20 nel 5,5% dei giorni. Quando manca un
segnale la sua fonte tiene il suo terzo e si legge su ciò che resta; il moltiplicatore scende in proporzione al
numero di segnali presenti (è una retta, e rispetto alla formula esatta sottostima di 0,0-0,3: quando manca
informazione il libro prende un po' meno rischio, mai di più).

Quella per fonte non è l'unica combinazione provata, e non è la cella migliore:

| Combinazione (misura di ricerca) | WTI, eseguito all'apertura dopo / alla stessa chiusura | Fondo Brent |
|---|---|---|
| Prima: trend, carry, carry-momentum | 0,53 / 0,60 | 0,38 / 0,37 |
| Tutto ciò che è stato provato, a pesi uguali, senza scegliere nulla | 0,61 / 0,64 | 0,66 / 0,66 |
| Solo prezzo: i tre più accelerazione e skew | 0,61 / 0,64 | 0,43 / 0,44 |
| I tre più rame e dollaro | 0,62 / 0,68 | 0,70 / 0,71 |
| I sette a pesi uguali | 0,68 / 0,71 | 0,71 / 0,71 |
| **I sette, un terzo per fonte (quella dei libri)** | **0,70 / 0,75** | **0,75 / 0,75** |
| Lo stesso, con l'S&P 500 fra gli altri mercati | 0,72 / 0,76 | 0,68 / 0,68 |
| Lo stesso, più open interest e scorte come quarta fonte | 0,73 / 0,77 | 0,83 / 0,81 |

Altre sette varianti (famiglie raggruppate in altro modo, con e senza skew, rame e dollaro a una sola velocità)
stanno fra 0,58 e 0,69. La combinazione per fonte regge nei tre terzi del campione WTI (0,61 / 0,66 / 0,77, contro 0,54 / 0,44 / 0,46
della previsione vecchia) e fa scendere gli scambi da 18,5 a 13 volte il capitale l'anno.

**Quanto è sicuro il miglioramento.** Bootstrap a blocchi stazionari dei rendimenti giornalieri appaiati (gli
stessi giorni ricampionati per le due previsioni), differenza fra gli Sharpe:

| Serie | Prima | Dopo | Differenza (intervallo al 90%) | Probabilità che sia peggio |
|---|---|---|---|---|
| WTI long e short, apertura successiva | 0,53 | 0,70 | +0,17 (da +0,04 a +0,30) | 1,8% |
| WTI long e short, stessa chiusura | 0,60 | 0,75 | +0,14 (da +0,01 a +0,27) | 3,9% |
| Fondo Brent solo long | 0,35 | 0,49 | +0,14 (da −0,06 a +0,34) | 12,4% |
| Fondo Brent long e short | 0,38 | 0,75 | +0,37 (da +0,16 a +0,58) | 0,2% |

**Quanto è selezione.** Quattro tenuti su sedici, e una combinazione fra quindici: una parte del numero è il
fatto di aver scelto. La riga «tutto ciò che è stato provato, a pesi uguali» non sceglie nulla e dà 0,61 sul WTI
contro 0,53 di prima: circa metà del miglioramento misurato (+0,08 su +0,17) resta anche così. L'altra metà può
essere vera o può essere selezione, e i dati non bastano a dirlo. Sul fondo solo long, il caso del libro
prudente, il miglioramento ha una probabilità su otto di essere caso.

### I tre libri, con i lotti e i costi veri

Stesso motore del paper trading: commissioni e spread dello strumento, lotti interi, interessi sul margine,
stop giornaliero, un ordine deciso alla chiusura ed eseguito all'apertura successiva (la più severa delle due
ipotesi), capitale 10 000 $. I numeri sono più bassi della misura di ricerca qui sopra, che non ha lotti né
interessi: quelli da guardare sono questi.

| Libro | Periodo | Rend. annuo | Volatilità | Sharpe (t) | Perdita max | Leva mediana / 95° / max | P(−50% in un anno) |
|---|---|---|---|---|---|---|---|
| Prudente (BNO, 1x, solo long) | 2011-2026 | +4,9% | 9% | 0,56 (2,2) | −23% | 0,09x / 0,79x / 0,96x | 0,0% |
| Dinamico (BNO 2x, short con SCO) | 2011-2026 | +19,6% | 29% | 0,77 (3,0) | −50% | 0,57x / 1,71x / 1,98x | 0,1% |
| Spinto (/MCL, 10x) | 1986-2026 | +22,2% | 55% | 0,64 (4,1) | −86% | 1,07x / 3,53x / 8,53x | 22% |
| BNO comprato e tenuto | 2011-2026 | +4,0% | 35% | 0,29 | −87% | 1x | |
| WTI comprato e tenuto | 1986-2026 | +4,2% | 39% | 0,31 | −99% | 1x | |

Prima e dopo, stesso motore e stessi dati:

| Libro | Sharpe con i tre segnali | con i sette | con i sette e il lato short |
|---|---|---|---|
| Prudente | 0,39 (t 1,5) | 0,56 (t 2,2) | — (resta solo long) |
| Dinamico | 0,37 (t 1,5) | 0,54 (t 2,1) | 0,77 (t 3,0) |
| Spinto | 0,45 (t 2,9) | 0,64 (t 4,1) | — (era già long e short) |

E senza l'effetto del lotto (conto di riferimento mille volte più grande, obiettivo di volatilità 15%): fondo
Brent solo long da 0,35 a 0,48; fondo Brent con lo short in SCO da 0,46 a 0,76; WTI long e short da 0,43 a
0,65. Segnale per segnale e fonte per fonte, queste tabelle sono nella pagina del backtest e in
`desk/backtest.json`. Sul WTI le tre fonti da sole valgono 0,46 (prezzo), 0,44 (curva) e 0,49 (altri mercati):
quasi uguali e poco correlate, che è il motivo per cui insieme fanno 0,65.

Da leggere insieme a questi numeri:

- A costi doppi gli Sharpe sono 0,54 / 0,73 / 0,57. Eseguendo alla stessa chiusura: 0,50 / 0,70 / 0,69.
- Il libro prudente fa il 2021 a +35%, il 2022 a +18%, poi tre anni negativi (−5%, −3%, −9%) e il 2026 a
  +45%. Il dinamico: −13%, −12% e −17% nei tre anni, il 2026 a +95%. Tre anni di fila in perdita sono normali
  per questa strategia, con la previsione vecchia come con la nuova.
- Il libro spinto ha avuto un giorno a −31% e una settimana a −39%. In un anno qualsiasi ha quasi nove
  probabilità su dieci di perdere un quarto del conto e più di una su cinque di perderne metà. È ciò che «Kelly
  pieno» significa, non un difetto da correggere con un parametro. Il libro dinamico, ora che è investito il
  96% dei giorni invece del 58%, ha tre probabilità su dieci di perdere un quarto del conto in un anno.
- Lo stop giornaliero non ferma un'apertura in gap: nel 2026 i fine settimana hanno prodotto aperture fino a
  +16%. Per questo il libro spinto scende a 3x prima di ogni chiusura dei mercati più lunga di un giorno e il
  dinamico a 1,5x, da entrambi i lati.
- **Il libro delle opzioni non migliora.** La sua simulazione su modello dà 0,63 con la previsione vecchia e
  0,63 con la nuova (da 0,39 a 0,71 secondo smile e costi), con la perdita massima che passa da −14% a −18%.
  Resta sperimentale, e nessuna sua regola è stata toccata.

### Il lato short del fondo: comprare SCO

Con la previsione vecchia il lato short aggiungeva poco: sul WTI in quarant'anni, 0,43 long e short contro 0,41
solo long. Con la nuova aggiunge in tutti e tre i terzi del campione (0,59 / 0,64 / 0,73 contro 0,50 / 0,53 /
0,38; sull'intero periodo 0,65 contro 0,47). Un libro che può solo comprare butta via quella parte.

Il fondo Brent non si vende allo scoperto sul conto che i libri imitano. La via è *comprare* un fondo inverso:
SCO rende ogni giorno −2 volte un indice di future sul WTI. Misurato sui prezzi veri dei due fondi dal 2010:

| Fondo Brent, previsione nuova (misura di ricerca) | Sharpe |
|---|---|
| Solo long | 0,52 |
| Long e short vendendo BNO allo scoperto (non disponibile) | 0,75 |
| Long con BNO, short comprando SCO | 0,73 |

Passare da SCO costa quindi 0,02 rispetto a uno short ideale, e contiene tutto ciò che SCO è davvero: la
commissione dello 0,95% l'anno, il rinnovo dei future, il WTI al posto del Brent (correlazione giornaliera con
BNO −0,93, beta −1,83) e il ricalcolo giornaliero. Nel motore, con i costi e gli interessi veri, il libro
dinamico passa da 0,54 a 0,77.

Che cosa bisogna sapere di questo numero:

- **Viene da tre anni.** La differenza fra il fondo con e senza il lato short è positiva in sei anni e negativa
  in dieci; i tre migliori (2014, 2015, 2020) sommano più del totale. Dal 2011 al 2018 il lato short porta lo
  Sharpe da 0,09 a 0,86; dal 2019 al 2026 lo lascia dov'era (0,68 contro 0,71). È un'assicurazione contro i
  crolli che paga di rado e molto, e che nel frattempo costa.
- **SCO non è uno strumento da tenere.** Comprato nell'agosto 2011 e tenuto fino a ottobre 2026 ha perso il
  99,3%, mentre BNO guadagnava l'82%. Nel 2020 il fondo Brent ha perso il 38% e SCO ha chiuso l'anno a −4%; nel
  2023 hanno perso entrambi. Il libro lo tiene solo finché la previsione è negativa e ne ricalcola la quantità
  a ogni decisione, con la stessa fascia di inerzia del lato long.
- **In contanti, mai a margine.** Al massimo quanto vale il conto (2x di esposizione al greggio), 0,75 volte il
  conto prima di un fine settimana. Non può perdere più di ciò che vi è investito, ma un rialzo del 50% in una
  notte lo azzererebbe, e nel 2026 ci sono state aperture a +16%.
- **Il libro prudente resta solo long.** È il libro che si può tenere senza margine e senza fondi a leva, e
  avere accanto un libro uguale con e senza il lato short è il modo di misurarne l'effetto dal vivo.
- **Il libro spinto non cambia strumento.** Portare la sua esposizione con UCO e SCO (+2x e −2x) invece che con
  lotti da 100 barili dà lo stesso Sharpe (0,59 contro 0,58 dal 2009) finché la leva non ha limiti; senza
  margine, cioè entro 2x, scende a 0,52. La frazione di lotto che si guadagna non vale il tetto che si perde.

### Scartato

| Idea | Risultato |
|---|---|
| Rottura dell'«area di rumore» infragiornaliera su BNO, USO, UCO, XLE (barre orarie) | Sharpe lordo fra −1,5 e −2,1 |
| Prima ora → ultima ora; seguito del mercoledì EIA; finestra di regolamento | nessun effetto, \|t\| sotto 1,7 |
| Rapporti di varianza orari sul contratto di dicembre | sotto 1 (0,93 a 4 ore, 0,76 a 23 ore): lieve ritorno alla media, troppo piccolo per i costi |
| Ritorno alla media dello spread BNO-USO | Sharpe fra −0,2 e −0,5 |
| Vendere insieme UCO e SCO (decadimento dei fondi a leva) | 0,96 sull'intero campione, −0,36 dal 2022 |
| Dodici dei sedici segnali giornalieri provati a ottobre 2026 (breakout, carry continuo, accordo trend-curva, valore, flusso e posizionamento COT, open interest, scorte, margine di raffinazione, premio di volatilità, insider) | uno per uno nella tabella «Sedici candidati, quattro tenuti» |

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

**Gli obiettivi non sono stati alzati con i sette segnali.** Lo Sharpe misurato ora è 0,55-0,75, ma una parte
è selezione, e un libro dimensionato su uno Sharpe che non ha perde più di quanto guadagni uno dimensionato su
uno Sharpe che supera. Se i sette segnali valgono davvero 0,65, il libro spinto sta girando a tre quarti di
Kelly invece che a Kelly pieno: è l'errore dal lato giusto.

L'esposizione è `previsione / 10 × obiettivo di volatilità / volatilità corrente`. L'8 ottobre 2026, con il
Brent al 43% di volatilità e la previsione a +5,5 (prezzo +12,0, curva 0,0, altri mercati −2,6), fa 0,15x per
il libro prudente e 0,32x per il dinamico; per il libro sul WTI (previsione +4,1, volatilità 42%) fa 0,49x,
cioè meno di un contratto. La leva è un *risultato*: arriva a 3x-8x solo quando il greggio è calmo (15-20% di
volatilità) e le tre fonti sono d'accordo.

## 6. Limiti

- **Quindici anni per il Brent, quaranta per il WTI, un solo mercato.** Gli errori standard sono larghi quanto
  i risultati.
- **Sedici segnali provati, quattro tenuti.** Chi sceglie fra sedici trova qualcosa anche nel rumore. La stima
  che non sceglie nulla è nel capitolo 4; quella che conterà è il paper trading da qui in avanti.
- **Rame e dollaro hanno funzionato soprattutto fra il 2001 e il 2021.** Dal 2022 al 2025 non hanno dato
  nulla. Il rame letto qui è il future più vicino in serie continua (`HG=F`): contiene i piccoli salti del
  cambio di scadenza, trascurabili per un trend ma non nulli. E su Yahoo la riga del giorno in cui si scarica
  non è la chiusura di quel giorno (a mercato aperto è il prezzo di un'altra scadenza, la sera è l'inizio della
  seduta dopo): dal vivo il motore legge una riga solo quando uno scaricamento del giorno successivo l'ha
  confermata, che è la forma in cui la riga sta nello storico su cui il segnale è stato misurato.
- **Il lato short del fondo dipende da tre anni di crollo** e passa da uno strumento, SCO, che segue il WTI,
  si ricalcola ogni giorno e può azzerarsi in una notte. I raggruppamenti di quote (SCO ne ha fatti più
  d'uno) non sono gestiti: lo storico di Yahoo è già rettificato, ma un libro che tenesse SCO nel giorno di un
  raggruppamento vedrebbe un guadagno che non esiste, e va azzerato a mano prima.
- **Il backtest non incontra le condizioni degradate.** Una tabella che manca per un giro, una barra letta a
  metà, una gamba eseguita e l'altra no: dal vivo succedono, nel replay giornaliero mai. Le regole per quei
  casi (`docs/VALIDATION.md`, «Revisione indipendente») sono provate da test costruiti apposta, non dai numeri
  di questo documento, che infatti non sono cambiati quando sono state scritte.
- **Lo skew è letto sul WTI anche per il Brent**, e sulla seconda metà del campione vale un terzo della prima.
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
   quotazioni reali di cui disporremo. Il confronto fra il libro prudente e il dinamico dirà dal vivo che cosa
   vale il lato short.
2. Altri mercati. È l'unica via onesta a uno Sharpe più alto: lo stesso sistema su gas naturale, prodotti
   raffinati, metalli e indici porta 0,6 su un mercato verso 1 su un paniere, perché le scommesse sono
   indipendenti. Su Robinhood esistono i future micro corrispondenti; servirebbero le loro serie per contratto.
   Fatto in parte nella fase 12 (capitolo 8) con i fondi di oro, rame e agricoltura; il gas naturale va fatto
   con il future micro /MNG e il carry della sua curva, non con i fondi, che lo hanno bocciato.
3. Curva del Brent reale: fra qualche mese la pendenza di BNO non avrà più bisogno della procura WTI.
4. Contratti a evento contro la probabilità implicita nelle opzioni, in piccolo.
5. Replica di Ewald e altri (2025) sulla stagionalità infragiornaliera del Brent, l'unico lavoro che dichiara
   un margine dopo i costi: richiede dati al minuto.

## 8. Fase 12 (13 ottobre 2026): altri mercati e nuove fonti, dichiarati prima del test

**Il contesto in cui si decide.** Brent a 106 $ il 13 ottobre 2026 (+70% sull'anno); guerra con l'Iran dal
28 febbraio, cessate il fuoco dall'8 aprile ma transiti a Hormuz a una frazione della norma; il prezzo è
passato da 118 (fine marzo) a 70 (1° luglio) a 109 (inizio settembre); volatilità realizzata intorno al 40%,
curva in backwardation del 25% l'anno fra dicembre 2026 e dicembre 2027; nel fine settimana attacchi a
petroliere vicino a Hormuz e all'aeroporto di Riad, OPEC+ che produce di più, un «cessate il fuoco energetico»
Russia-Ucraina incerto. Un mercato di questo tipo premia chi non prevede l'evento e diversifica le scommesse.

**La domanda.** Che cosa si può aggiungere ACCANTO ai quattro libri, che restano come sono, con una prova sui
dati veri? Il capitolo 7 lo aveva già detto: non più parametri sullo stesso mercato, ma mercati indipendenti
e fonti di rendimento indipendenti. Tutto ciò che segue è scritto PRIMA di guardare un risultato, con la regola
di sempre: una regola pubblicata, i parametri del suo autore, una prova sola, tutti i candidati riportati.

### A. Libri satellite: la stessa previsione, su altri mercati

I quattro mercati sono scelti per la forza che li muove, non per il loro storico: il **tempo meteorologico**
muove il gas naturale e l'agricoltura, la **geopolitica** l'oro (e il greggio, che c'è già), la **domanda di
elettricità dell'intelligenza artificiale** il rame e il gas. Ognuno diventa un libro di 10 000 $ identico
nelle regole a uno dei libri esistenti, con la sola fonte di previsione che il suo prezzo contiene (trend,
accelerazione, asimmetria calcolati sulla serie del fondo stesso: le regole e i parametri di
`engine/desk/signals.py`, nessuno nuovo). Niente carry (non ne archiviamo la curva) e niente rame/dollaro.

| Libro | Fondo | Lato short | Regole | Costi dichiarati (mezzo spread) |
|---|---|---|---|---|
| Oro | GLD | nessuno | prudente: vol 12%, tetto 1x, soglia 5% | 1 pb |
| Rame | CPER | nessuno | prudente | 8 pb |
| Gas | UNG | compra KOLD (−2x al giorno) | dinamico: vol 25%, tetto 2x (1,5x nel fine settimana), soglia 8% | 3 pb; KOLD 8 pb |
| Agricoltura | DBA | nessuno | prudente | 5 pb |

Commissioni zero (Robinhood), modello di slippage del desk, ordine deciso alla chiusura ed eseguito
all'apertura della seduta dopo, come per BNO. Serie: i prezzi rettificati dei fondi su Yahoo (GLD dal 2004,
UNG dal 2007, DBA dal 2007, CPER e KOLD dal 2011), le stesse tabelle che il motore scaricherà ogni giorno.

**Che cosa decide la prova.** Niente viene scelto sul risultato: i quattro libri partono in paper trading
come «candidati», separati dai quattro libri attivi, e si giudicheranno sugli eseguiti veri. L'unico filtro
dichiarato: un mercato il cui libro ha uno Sharpe dopo i costi NEGATIVO su tutto il suo storico non parte (una
regola che ha perso soldi per quindici anni su un mercato non ha titolo per riprovarci). Si riportano, per
ciascuno: Sharpe e t, rendimento annuo, perdita massima, anni positivi, costi, correlazione giornaliera con il
libro dinamico; e per l'insieme: lo Sharpe del portafoglio dei quattro libri attivi, da solo e con i satelliti
(capitale uguale), che è il numero che conta.

### B. Nuove fonti sul greggio (solo ricerca: oggi non entrano in nessun libro)

Provate con lo stesso banco della fase 11 (obiettivo di volatilità 15%, 3 punti base sul nozionale, fascia di
inerzia, posizione presa sul dato del giorno e applicata al rendimento del giorno dopo, WTI dai contratti EIA
1983-2024 e dal 2024 dal contratto tenuto), così i numeri si confrontano con la tabella dei sedici.

1. **Basis-momentum** (Boons e Prado, *Journal of Finance* 2019): il rendimento composto a 12 mesi del primo
   contratto meno quello del secondo, entrambi rollati ogni mese; segnale +10 se positivo, −10 se negativo (la
   versione a indicatore dell'articolo, che riporta coefficienti positivi nelle serie storiche dei singoli
   mercati, greggio compreso, fino al 2014). Dal vivo non è calcolabile oggi: dal 5 aprile 2024 l'EIA non
   pubblica più i contratti NYMEX e il secondo contratto non ha una storia gratuita; servono dodici mesi di
   archivio per contratto (ottobre 2027) o una fonte a pagamento.
2. **Premio di termine, spread di calendario** (Szymanowska, de Roon, Nijman, van den Goorbergh, *JF* 2014;
   Boons e Prado sugli «spreading returns»): lungo il primo contratto e corto il terzo quando la curva è in
   backwardation, il contrario in contango, segno della base del giorno prima; rendimento dai contratti EIA
   1-4, obiettivo di volatilità 25%, costi di due gambe /MCL (1,27 $ per lotto per lato, 1,5 pb). Un libro
   su spread non esiste nel motore: si misura prima di costruirlo.
3. **Indice di rischio geopolitico giornaliero** (Caldara e Iacoviello, *AER* 2022): esplorativo, senza regola
   pubblicata di negoziazione. La media a 30 giorni dell'indice contro la sua media a un anno, in unità di
   deviazione standard; segno positivo = lungo greggio. Dichiarato in anticipo: qualunque sia il risultato NON
   entra in un libro, serve solo a dire se valga la pena disegnare una fonte geopolitica.

Non provati, e perché: il **tempo meteorologico sul Brent** non ha una regola (gli uragani muovono i margini
della benzina, l'inverno i distillati: i mercati del tempo sono il gas e l'agricoltura della parte A); i
**transiti a Hormuz** sono un solo evento (n = 1: si mostrano, non si misurano); i segnali da **notizie letti
da un modello linguistico** non si possono misurare sul passato (il modello lo conosce già) e solo un paper
trading in avanti li può giudicare.

### C. Un conto, non otto

Lo Sharpe che interessa è quello del portafoglio: i libri attivi insieme, poi con i satelliti, a capitale
uguale. La correlazione fra i rendimenti giornalieri dei libri è riportata per intero.

### Risultati (13 ottobre 2026, serie fino al 13 ottobre)

**A. I libri satellite.** Replay reale del desk (`desk-backtest`: ordine deciso alla chiusura ed eseguito
all'apertura dopo, costi dichiarati, conto da 10 000 $), prezzi rettificati di Yahoo scaricati il 13 ottobre.
La riga dell'agricoltura è quella DOPO la correzione delle distribuzioni (vedi sotto, «Le distribuzioni di un
fondo»): prima leggeva 0,29 (1,3), +1,7%, −19%, perché il libro contabilizzava come perdita i 3,5-4,4% che DBA
paga ogni dicembre. GLD, CPER, BNO e SCO non hanno mai pagato nulla: le loro righe non cambiano.

| Libro | Fondo | Anni | Sharpe (t) | Rend. annuo | Vol | Perdita max | Anni positivi | Costi/anno | A costi doppi | Stessa chiusura | Corr. con dinamico |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Oro | GLD | 20,7 | **0,50 (2,3)** | +3,6% | 7,8% | −22% | 12/21 | 0,04% | 0,49 | 0,48 | 0,04 |
| Rame | CPER | 13,7 | 0,26 (1,0) | +2,5% | 12,2% | −24% | 9/14 | 0,35% | 0,24 | 0,26 | 0,06 |
| Agricoltura | DBA | 18,5 | 0,34 (1,5) | +2,0% | 6,5% | −18% | 10/19 | 0,18% | 0,31 | 0,32 | 0,10 |
| Gas | UNG, short con KOLD | 18,3 | **−0,12 (−0,5)** | −5,4% | 23,4% | −71% | 6/19 | 0,38% | −0,14 | −0,03 | 0,02 |

Comprare e tenere il fondo, senza leva: GLD 0,59 (+9,7% l'anno con il 18,5% di volatilità e il −46% di perdita
massima), CPER 0,25, DBA 0,03, UNG −0,47 (−99,9% in diciotto anni: il contango se lo mangia). Per segnale, al
15% di volatilità e sul conto di riferimento (long-only; il gas long e short): oro trend 0,58, accelerazione
0,43, skew 0,21; rame 0,06 / 0,01 / 0,40; agricoltura 0,12 / 0,16 / 0,22; gas 0,17 / −0,19 / −0,10.

**Il filtro dichiarato si applica al gas: non parte.** Con i fondi, il gas naturale non si negozia: UNG perde il
contango ogni mese e KOLD perde il ricalcolo giornaliero su un mercato con l'80% di volatilità; la soglia di
perdita giornaliera dell'8% è scattata undici volte. La strada giusta è il future micro Henry Hub (/MNG,
1 000 mmBtu: circa 3 000 $ di nozionale, un terzo del conto per lotto), dove il carry si incassa e nessun fondo
decade: richiede le serie per contratto del gas e una stagionalità della curva che il motore oggi non ha. È nel
capitolo 7, non in un libro.

**C. Il portafoglio** (capitale uguale in ogni libro, ribilanciato ogni giorno, dal 30 gennaio 2013, il primo
giorno in cui tutti i libri hanno una curva; lo Sharpe è sui rendimenti del portafoglio, non la media degli
Sharpe; è il metodo di `report.portfolio_stats`, e i numeri sono quelli che `desk-backtest` scrive nel payload
e il sito mostra; la prima stesura di questa tabella, calcolata a mano sulle curve di un passaggio precedente,
dava 0,81 / +23,6% per i tre libri e 0,82 con il gas: è stata sostituita con i numeri riproducibili dal comando):

| Portafoglio | Sharpe (t) | Rend. annuo | Vol | Perdita max |
|---|---|---|---|---|
| I tre libri lineari (prudente, dinamico, spinto), stessa finestra | 0,80 (3,0) | +21,2% | 29,3% | −61% |
| I tre libri lineari su tutta la loro storia comune (dal 2011) | 0,73 (2,8) | +18,3% | 28,8% | −61% |
| I tre più oro, rame, agricoltura | **0,87 (3,2)** | +12,8% | 15,2% | −30% |
| I tre più i quattro satelliti (gas compreso) | 0,80 (3,0) | +10,5% | 13,6% | −29% |
| Oro, rame, agricoltura da soli | 0,47 (1,7) | +2,7% | 5,9% | −12% |
| Prudente e dinamico | 0,84 (3,1) | +14,5% | 18,1% | −38% |
| Prudente e dinamico più i tre satelliti | **0,92 (3,4)** | +7,7% | 8,4% | −15% |

Correlazione fra i rendimenti giornalieri: oro, rame e agricoltura con i libri sul greggio fra 0,03 e 0,16, fra
loro 0,11-0,25; il gas con tutto 0,00-0,03. Gli anni cattivi dei libri sul greggio, con i tre satelliti a
capitale uguale: 2023 da −16,7% a −8,9%, 2024 da −19,1% a −3,3%, 2025 da −26,6% a −8,3%; e quelli buoni
dimezzati (2014 da +61% a +28%, 2026 da +98% a +44%).

**Lettura.** Lo Sharpe sale di poco (0,80 → 0,87) perché i satelliti portano poco rischio: un quarto di Kelly,
solo long, su mercati che il nostro segnale legge debolmente (rame e agricoltura a t ≈ 1). Quello che portano
è la quasi indipendenza: la volatilità e la perdita massima del portafoglio si dimezzano e tre anni negativi su
tre diventano sopportabili. È diversificazione, non alfa: il rendimento a capitale uguale scende perché metà del
capitale rende il 2-4% l'anno. Il filtro dichiarato ne ha fermato uno su quattro; nessuno dei tre che partono
è stato scelto sul risultato.

**B. Le fonti sul greggio** (banco della fase 11, WTI dai contratti EIA 1983-2024 e dal contratto tenuto dopo):

| Candidato | WTI, Sharpe (t) | Prima / seconda metà | Un giorno di ritardo | Fondo Brent long-only | Esito |
|---|---|---|---|---|---|
| Basis-momentum a 12 mesi, segno (Boons e Prado) | 0,02 (0,1) | 0,27 / −0,23 | −0,03 | −0,33 | scartato |
| lo stesso, continuo (vicino) | 0,25 (1,6) | 0,48 / −0,13 | 0,22 | −0,05 | scartato: niente dopo il 2005 |
| lo stesso, a 6 e a 18 mesi (vicini) | 0,12 / 0,19 | 0,42 / −0,19 · 0,33 / 0,05 | 0,07 / 0,13 | −0,41 / −0,12 | scartato |
| Indice GPR a 30 giorni contro un anno, continuo | −0,07 (−0,5) | −0,04 / −0,11 | −0,07 | −0,03 | scartato |
| lo stesso, a segno · invertito | 0,04 · 0,05 | 0,12 / −0,03 · 0,01 / 0,08 | −0,00 · 0,04 | 0,16 · −0,22 | scartato |

Nel campione dell'articolo (fino al 2014) il basis-momentum aveva qualcosa anche qui (0,27 nella prima metà);
dopo, nulla. Il rischio geopolitico misurato dalle notizie non dice nulla sulla direzione del greggio il giorno
dopo, in nessuna delle tre forme: era atteso, ora è misurato.

**Lo spread di calendario.** Lungo il contratto tenuto e corto quello due mesi dopo quando la base è in
backwardation, il contrario in contango, 25% di volatilità obiettivo, 1983-2024: **lordo 0,62 (t 3,9)**, al
contrario −1,85 (il segno è quello giusto, il premio di termine esiste, come nella letteratura); **netto −0,64**.
La volatilità dello spread è il 5% l'anno, quindi l'esposizione mediana è 4,4 volte il nozionale per gamba; le
due gambe di /MCL (1,5 punti base di mezzo spread e 1,27 $ per lotto per lato), rinnovate ogni mese e girate a
ogni cambio di segno, costano il 34% l'anno. Il premio di termine si vede e non si incassa con questi strumenti;
con un conto istituzionale (spread di un tick sul /CL, nessuna commissione) i costi scendono di dieci volte e
il netto torna positivo: non è il nostro caso, e non lo si finge.

**Le distribuzioni di un fondo** (trovato dalla revisione indipendente del 13 ottobre, prima della messa in
linea). I rendimenti che la previsione legge vengono dalla chiusura rettificata di Yahoo, gli eseguiti e le
valutazioni dalle barre grezze: il giorno in cui un fondo stacca una distribuzione in contanti la barra grezza
scende dell'importo e il libro contabilizzava una perdita che il conto vero non ha (riceve i contanti). Misurato
sulle tabelle archiviate, con il salto del fattore di rettifica (`fund_distributions`): BNO, USO, SCO, GLD e CPER
non hanno mai pagato nulla; DBA ha pagato 0,18 $ (2018), 0,26 (2019), 0,10 (2022), 0,96 (2023, 4,4%), 1,08
(2024, 3,9%) e 0,91 (2025, 3,5%), ogni volta a fine dicembre: a 0,9x un libro perdeva il 3-4% in un giorno che
per chi lo detiene è piatto, a un passo dalla soglia giornaliera del 5%. Corretto: il broker accredita
``quote × importo`` sulla posizione all'apertura della data di stacco, una volta per data, nel replay (prima
della barra del giorno) e dal vivo (al giro il cui scaricamento mostra lo stacco: la mattina dello stacco, fino
a quel giro, la perdita si vede e la nota lo dice). Per l'agricoltura lo Sharpe passa da 0,29 a 0,34 e il
rendimento annuo da +1,7% a +2,0% (669 $ accreditati in diciotto anni); gli altri libri sono identici numero per
numero. Resta non gestito un raggruppamento di quote, come prima.

**Che cosa parte.** Tre libri candidati in paper trading accanto ai quattro: oro (GLD), rame (CPER),
agricoltura (DBA), con le regole del libro prudente e la sola fonte del prezzo. Nessuna nuova fonte nei libri
sul greggio. Il gas non parte. Il backtest dei quattro libri è identico, numero per numero, prima e dopo questa
fase (verificato rieseguendolo con il codice precedente sulle stesse serie).

**Uno Sharpe sopra 3 con una strategia sola**, chiesto il 13 ottobre: non esiste su questi strumenti e a questa
frequenza, e un backtest che lo mostrasse sarebbe la prova di un errore (anticipazione, costi omessi, scelta fra
molti tentativi), non di una strategia. Uno Sharpe di 3 è un t di 3 dopo un solo anno: certezza statistica. Le
strategie sistematiche documentate su un mercato valgono 0,3-0,5 dopo i costi, i fondi di managed futures
diversificati 0,5-0,8, i fondi quantitativi migliori 2-3 con migliaia di segnali, decine di mercati e
infrastruttura al millisecondo. La leva non cambia lo Sharpe. Quello che si può fare onestamente è quanto sopra:
più mercati indipendenti, costi veri, numeri scritti prima di guardarli.

### D. Registrazione del 13 ottobre, sera: obbligazioni, azioni e il rendimento della liquidità

**La domanda del proprietario.** «Nuovi segnali da integrare? Voglio uno strumento che guadagni anche se il
mercato scende, con un obiettivo del 10-15% l'anno senza rischiare.» La risposta onesta viene prima del test:
un rendimento senza rischio esiste ed è quello dei buoni del Tesoro (il 4% circa nel 2025, misurato sotto con
SGOV); tutto ciò che sta sopra è pagato con rischio, e «guadagna anche se scende» è ciò che il lato short e il
trend-following danno nei crolli (2014, 2015, 2020) e non nei mercati laterali (2023-2025). Quello che si può
ancora misurare, dichiarato qui prima di guardare un numero:

1. **Altri due mercati indipendenti, con fondi che Robinhood tiene: i Treasury a 20 anni (TLT) e l'indice
   S&P 500 (SPY)**, con le regole del libro prudente e la sola fonte del prezzo (trend, accelerazione,
   asimmetria sul prezzo rettificato del fondo), solo long, obiettivo di volatilità 12%, tetto 1x, soglia
   giornaliera 5%, mezzo spread 1 pb (SPY) e 1 pb (TLT), distribuzioni accreditate. Sono i due mercati che
   nella letteratura sul trend-following (Hurst, Ooi, Pedersen, *A century of evidence on trend-following
   investing*, 2017) hanno il premio più documentato dopo le materie prime, e i meno correlati con il greggio.
   Prior dichiarato: Sharpe 0,2-0,5 ciascuno da soli, correlazione con i libri sul greggio sotto 0,2; per il
   portafoglio di tutti i libri a capitale uguale mi aspetto +0,05-0,10 di Sharpe. Il filtro è quello della
   parte A: uno Sharpe negativo sull'intera storia non parte. Il lato short (SH, TBF) richiede tabelle che non
   ho: resta per dopo.
2. **Il rendimento della liquidità.** I libri tengono in contanti dal 50% al 90% del conto (leva mediana del
   prudente 0,09x) e il broker simulato non remunera i contanti. Misuro, con il rendimento totale di SGOV
   (buoni del Tesoro a 0-3 mesi, dal giugno 2020, unica serie in archivio), quanto avrebbe reso la liquidità
   inutilizzata di ogni libro anno per anno. È l'unico guadagno «senza rischiare» del sistema, e vale quanto
   vale il tasso: nulla dal 2009 al 2016, il 4-5% dal 2023.
3. **Che cosa NON provo** perché già misurato o fuori dalle regole: più leva (lo Sharpe non cambia), vendita di
   premio con le opzioni (negativa a quattro gambe), segnali infragiornalieri, notizie e indici geopolitici
   (nulla), parametri ottimizzati.

Tabelle: Yahoo via il workflow `research-data` del 13 ottobre (TLT dal 2002, SPY dal 2000, SGOV dal 2020).

**Risultati (misurati la sera stessa, dopo la registrazione; replay reale del desk con le distribuzioni
accreditate: TLT ne paga una al mese, SPY una a trimestre).**

| Libro (solo long, regole del prudente) | Anni | Sharpe (t) | Rend. annuo | Vol | Perdita max | A costi doppi | Comprato e tenuto |
|---|---|---|---|---|---|---|---|
| Treasury 20 anni, TLT | 23,0 | 0,37 (1,8) | +2,1% | 6,0% | −16% | 0,36 | 0,28 (+3,1%, −48%) |
| S&P 500, SPY | 25,5 | **0,58 (2,9)** | +3,9% | 6,9% | −11% | 0,58 | 0,58 (+9,6%, −55%) |

Sul SPY la regola non batte il fondo tenuto (stesso Sharpe), ne dimezza il rendimento e divide per cinque la
perdita massima: è un modo di stare in azioni che esce nei ribassi, non un segnale che li anticipa. Il 2022
resta negativo (−8,1%). Correlazioni con i libri sul greggio fra −0,04 e 0,17; TLT e SPY fra loro −0,18.

| Portafoglio a capitale uguale, dal 30 gennaio 2013 | Sharpe (t) | Rend. annuo | Vol | Perdita max |
|---|---|---|---|---|
| I tre libri sul greggio | 0,80 (3,0) | +21,2% | 29,3% | −61% |
| I tre più oro, rame, agricoltura (i candidati di oggi) | 0,87 (3,2) | +12,8% | 15,2% | −30% |
| ... più TLT | 0,87 (3,2) | +11,2% | 13,2% | −27% |
| ... più SPY | 0,91 (3,4) | +11,8% | 13,1% | −25% |
| ... più TLT e SPY | **0,92 (3,4)** | +10,5% | 11,6% | −23% |
| Prudente e dinamico più i cinque satelliti | **1,03 (3,8)** | +6,5% | 6,3% | −12% |
| TLT e SPY da soli | 0,77 (2,9) | +3,3% | 4,3% | −10% |

Gli anni cattivi, con tutti e otto i libri: 2023 −6,1%, 2024 −2,2%, 2025 −5,8% (i tre libri sul greggio da
soli: −16,7, −19,1, −26,6%); i buoni dimezzati un'altra volta (2014 +23%, 2026 +31%). Il prior era giusto
nella direzione e nell'ordine di grandezza (+0,05 di Sharpe), con SPY che vale più di TLT.

**Il rendimento della liquidità.** SGOV, rendimento totale per anno: 2020 0,05%, 2021 0,04%, 2022 1,6%, 2023
5,1%, 2024 5,3%, 2025 4,2%, 2026 2,9% fino al 13 ottobre (3,7% annualizzato). La leva mediana dei libri nel
replay: prudente 0,09x, dinamico 0,57x, spinto 1,07x (margine: la liquidità resta quasi tutta), oro 0,01x,
rame 0,28x, agricoltura 0,00x. Nessun replay e nessun libro in linea accredita questo interesse: sul 2023-2025
mancano circa 4 punti l'anno al prudente e ai satelliti, 2 al dinamico, e nulla fra il 2009 e il 2016, quando
i buoni rendevano zero. È l'unico rendimento senza rischio di mercato del sistema ed è il tasso del momento,
non una proprietà della strategia.

**Lettura, per la domanda da cui è partita.** Il miglior portafoglio onesto che questo progetto sa costruire
vale uno Sharpe di 0,9-1,0 nel campione (meno fuori campione: una parte è selezione dei segnali della fase
11). Un rendimento atteso del 10-15% l'anno con quello Sharpe richiede il 10-15% di volatilità, che negli
ultimi tredici anni ha voluto dire perdite massime del 20-30% e un anno su tre in perdita; a un terzo di quel
rischio (6% di volatilità) si resta sul 6-7% più l'interesse sulla liquidità. «Guadagnare anche se il mercato
scende» è vero nei crolli, dove il lato short e il trend hanno fatto il 2014, il 2015 e il 2020, e falso nei
mercati laterali, dove si perde (2023-2025, tre anni su tre). «Senza rischiare» vale solo il tasso dei buoni
del Tesoro. Che cosa seguirebbe, se il proprietario lo vuole: TLT e SPY come quarto e quinto libro candidato
(un'ora di lavoro, la macchina dei satelliti esiste); l'interesse sulla liquidità nel broker simulato, con la
serie dei buoni a 3 mesi (FRED DTB3, senza chiave); il lato short di azioni e obbligazioni con i fondi inversi
SH e TBF (tabelle da scaricare, stesso meccanismo di SCO). Non seguirebbe nessuna leva in più.

### E. Registrazione del 13 ottobre, notte: la previsione di volatilità e i pesi fra i libri

Chiesto dal proprietario dopo la discussione sui libri di fisica: due test, scritti qui prima di guardare un
numero, con la regola di decisione già fissata. Nessuno dei due tocca un segnale: il primo cambia il
denominatore della posizione (`previsione / 10 × obiettivo / volatilità`), il secondo come si divide il
capitale fra i libri.

**1. La volatilità con cui si dimensiona.** Oggi è la media mobile esponenziale di Carver (span 35 giorni,
pavimento 10%), `signals.ew_vol`. Candidati, tutti causali (walk-forward, stimati su dati fino al giorno prima,
ristimati ogni 21 giorni, almeno 500 osservazioni), tutti con lo stesso pavimento del 10%, perché il pavimento
è una regola del libro e non del modello:

- V1. GARCH(1,1) con innovazioni t di Student (Bollerslev 1986), un passo avanti: il codice del primo sistema
  (`engine/features/volatility.py`), parametri stimati, non scelti.
- V2. HAR (Corsi 2009) sul logaritmo della varianza di Yang-Zhang (2000) calcolata sulle barre giornaliere
  open-high-low-close: componenti a 1, 5 e 22 giorni, obiettivo la varianza media dei 22 giorni successivi,
  stima OLS a finestra crescente. È un adattamento dichiarato: il HAR di Corsi usa la varianza realizzata
  intraday, che su quarant'anni di storia non esiste; Yang-Zhang è lo stimatore giornaliero più efficiente
  che la letteratura offra.
- V3. Il nucleo logaritmico della cascata multifrattale (Bacry, Delour, Muzy 2001; Bacry, Kozhemyak, Muzy 2008):
  la previsione del logaritmo della volatilità è la media dei logaritmi passati pesata come 1/k fino alla
  scala integrale T = 512 giorni (due anni, l'ordine di grandezza stimato in letteratura), ricondotta alla
  media con un solo coefficiente stimato walk-forward. Semplificazione dichiarata del predittore del MRW (non
  stimo λ²: entra solo nel coefficiente).

Misure: (i) qualità della previsione, indipendente dal trading: perdita QLIKE (Patton 2011) ed errore
quadratico sul logaritmo, contro la volatilità realizzata dei 22 giorni successivi, per BNO, il contratto WTI,
GLD, CPER, DBA; (ii) il replay reale del desk dei sei libri lineari con ciascuna regola, e il portafoglio a
capitale uguale dei tre libri sul greggio più i tre satelliti. Regola di decisione, fissata ora: una regola
sostituisce quella di Carver solo se batte la sua QLIKE su tutti e cinque i mercati, alza lo Sharpe del
portafoglio di almeno 0,03 senza peggiorare la perdita massima di nessun libro del nucleo, e lo fa in
entrambe le metà del campione. Prior: +0,02-0,05 di Sharpe, perdite massime un po' più basse; GARCH e HAR
quasi equivalenti, il nucleo logaritmico a metà strada fra loro e la media esponenziale.

**2. I pesi fra i libri.** Oggi capitale uguale (1/N), ribilanciato ogni giorno. Candidati, sui rendimenti
giornalieri dei libri (i tre sul greggio, i tre satelliti, TLT e SPY della parte D: otto serie dal 2013),
pesi calcolati su dati fino al giorno prima e tenuti 21 giorni, solo long, nessun libro oltre 2/N:

- W1. Inverso della volatilità (parità di rischio senza correlazioni), volatilità a 63 giorni.
- W2. Minima varianza sulla covarianza campionaria degli ultimi 252 giorni.
- W3. Minima varianza sulla covarianza pulita con la teoria delle matrici casuali: gli autovalori sotto il
  bordo di Marchenko-Pastur λ₊ = (1 + √(N/T))² sostituiti dalla loro media a traccia costante (Laloux, Cizeau,
  Bouchaud, Potters 1999; Bun, Bouchaud, Potters 2017).
- W4. Minima varianza sulla covarianza di Ledoit-Wolf (2004), il riferimento standard.

Misure: Sharpe, volatilità, perdita massima e rotazione dei pesi, per metà di campione. Regola di decisione:
si abbandona 1/N solo per un metodo che alzi lo Sharpe di almeno 0,05 in entrambe le metà. Prior: zero
(DeMiguel, Garlappi, Uppal 2009: sotto i venti asset 1/N non si batte fuori campione), con W3 ≈ W4 > W2 e W1
≈ 1/N. Lo si misura per chiudere la domanda.

**Risultati (misurati subito dopo, stessa notte).**

*La previsione di volatilità.* Qualità della previsione (QLIKE contro la volatilità realizzata dei 22 giorni
successivi, più basso è meglio; finestra dove tutti i modelli hanno una previsione propria, i primi 500 giorni
di ogni serie usano la media di Carver per tutti):

| Mercato | V0 Carver | V1 GARCH-t | V2 HAR-YZ | V3 nucleo log |
|---|---|---|---|---|
| BNO | 0,343 | 0,307 | **0,253** | 0,281 |
| WTI (contratto) | 0,282 | 0,251 | **0,200** | 0,347 |
| GLD | 0,260 | 0,225 | **0,209** | 0,220 |
| CPER | **0,277** | 0,651 | 0,443 | 0,467 |
| DBA | 0,158 | 0,149 | **0,149** | 0,168 |
| TLT | 0,187 | 0,180 | 0,178 | **0,173** |
| SPY | 0,385 | 0,350 | **0,323** | 0,373 |

Il replay reale dei libri (Sharpe; perdita massima fra parentesi) e il portafoglio a capitale uguale dal 2013:

| | V0 Carver | V1 GARCH-t | V2 HAR-YZ | V3 nucleo log |
|---|---|---|---|---|
| Prudente | 0,56 (−23%) | **0,59 (−21%)** | 0,48 (−26%) | 0,47 (−26%) |
| Dinamico | 0,77 (−50%) | **0,80 (−49%)** | 0,76 (−51%) | 0,69 (−46%) |
| Spinto | 0,64 (−86%) | **0,70 (−84%)** | 0,67 (−81%) | 0,58 (−90%) |
| Oro / rame / agricoltura | 0,50 / 0,26 / 0,34 | 0,51 / **0,13** / 0,34 | 0,49 / 0,20 / 0,33 | 0,48 / 0,17 / 0,34 |
| Tre più tre satelliti | 0,87 (−30%) | **0,90 (−29%)** | 0,85 (−29%) | 0,77 (−28%) |
| ... prima / seconda metà | 0,76 / 0,97 | 0,80 / 0,98 | 0,79 / 0,90 | 0,63 / 0,89 |
| Tutti e otto (con TLT e SPY) | 0,93 (−22%) | **0,96 (−22%)** | 0,90 (−21%) | 0,82 (−21%) |

Verdetto, con la regola scritta sopra: **resta la media di Carver.** Il GARCH è l'unico che migliora il trading
(+0,03 sul portafoglio, esattamente la soglia, in entrambe le metà, perdite massime del nucleo non peggiori) ma
fallisce la prima condizione: sul rame prevede peggio della media (0,65 contro 0,28, in entrambe le metà) e
dimezza quel libro (0,26 → 0,13). Il HAR su Yang-Zhang è il miglior previsore su sei mercati su sette e fa
negoziare peggio (prudente 0,56 → 0,48): una stima più reattiva muove la posizione più spesso e paga più
spread, e con il tetto a 1x la precisione in più non compra nulla. È la lezione di Carver, misurata: per
dimensionare serve una volatilità lenta, non una volatilità giusta. Il nucleo logaritmico è peggio di tutto.
Riaprire il GARCH per il solo greggio sarebbe una selezione dopo il fatto; se mai, con altri anni di rame.

*I pesi fra i libri* (otto libri dal 2014, il primo anno serve alla stima; pesi tenuti 21 giorni, tetto 2/N):

| Metodo | Sharpe | Rend. annuo | Vol | Perdita max | Prima / seconda metà | Rotazione per ribilanciamento |
|---|---|---|---|---|---|---|
| 1/N (oggi) | 1,02 | +12,1% | 11,9% | −22% | **1,12** / 0,93 | 0 |
| W1 inverso della volatilità | 1,00 | +4,2% | 4,2% | −8% | 1,00 / 1,04 | 0,19 |
| W2 minima varianza, campionaria | 1,03 | +3,4% | 3,3% | −6% | 0,77 / 1,23 | 0,06 |
| W3 minima varianza, matrici casuali | 1,02 | +3,4% | 3,3% | −6% | 0,72 / 1,26 | 0,06 |
| W4 minima varianza, Ledoit-Wolf | 1,10 | +3,7% | 3,4% | −6% | 0,82 / 1,32 | 0,05 |

Verdetto: **resta 1/N.** La pulizia con le matrici casuali non si distingue dalla covarianza campionaria (con
otto serie e 252 giorni il bordo di Marchenko-Pastur è a 1,39: quasi nulla da pulire), Ledoit-Wolf vale +0,08
ma solo nella seconda metà (0,82 contro 1,12 nella prima), e tutte le minime varianze sono un altro
portafoglio: un terzo della volatilità e del rendimento, concentrato nei libri meno volatili. Il prior era
giusto. Entrambi i test chiudono una domanda e non aprono una modifica: niente entra in linea.

## 9. Fase 13 (13 ottobre 2026, sera): i temi, i mercati che sanno prima, e un portafoglio che si ricalibra

**La richiesta del proprietario**, nelle sue parole: tenere in considerazione elettrificazione, fabbisogno
elettrico dei data center e bolle; «i mercati Kalshi che sanno prima: prendi i dati»; e un portafoglio la cui
dinamicità «cambia in base a come va», in tutte le forme proposte (esposizione dopo le perdite, capitale
verso i libri che vanno meglio, promozione e ritiro dei libri, regimi), **anche sui quattro libri in linea**.
Tutto registrato qui prima di guardare un numero, con la regola di decisione per ciò che si misura e il
motivo per ciò che non si misura.

### A. I temi come mercati: altri satelliti

Stesse regole dei satelliti della fase 12 (prudente, solo long, fonte prezzo, 12% di volatilità, tetto 1x,
soglia 5%, distribuzioni accreditate, mezzo spread 2-5 pb), stesso filtro (uno Sharpe negativo sull'intera
storia non parte), una prova ciascuno:

| Tema | Fondo | Da quando | Che cosa rappresenta |
|---|---|---|---|
| Nucleare per i data center | URA | 2010 | uranio e miniere |
| Rete elettrica e domanda di potenza | XLU | 1998 | utilities USA |
| Elettrificazione, batterie | LIT | 2010 | litio e filiera delle batterie |
| L'investimento in AI (e la sua bolla) | SMH | 2000 | semiconduttori |
| Energia pulita | ICLN | 2008 | solare, eolico, idrogeno |

Più TLT e SPY della parte D, che il filtro ha già passato. Prior: 0,2-0,5 ciascuno, correlazioni con il
greggio sotto 0,2, SMH e XLU correlati a SPY (0,6-0,8: contano meno di quanto sembri). Misura: Sharpe e t,
rendimento, perdita massima, anni positivi, e il portafoglio a capitale uguale con tutto il resto.

### B. Il portafoglio che si ricalibra: le quattro forme, una per una

- **D1. Esposizione dopo le perdite (controllo del drawdown).** Grossman e Zhou (1993): il rischio scala con
  il cuscino. Regola: moltiplicatore m = max(0,25; 1 − DD / 0,20), dove DD è il calo dal massimo dell'equity
  del PORTAFOGLIO dei libri (a capitale uguale) e 0,20 la perdita massima che si accetta; m moltiplica
  l'obiettivo di volatilità di ogni libro, anche dei quattro, e risale con il recupero. Prior: lo Sharpe non
  cambia (±0,03), la perdita massima scende di un terzo, il rendimento composto un po' meno. Regola di
  decisione: entra se la perdita massima del portafoglio scende di almeno un quinto senza che lo Sharpe cali
  di più di 0,05, in entrambe le metà.
- **D2. Capitale verso i libri che vanno meglio (momentum delle strategie).** Pesi proporzionali allo Sharpe
  degli ultimi 36 mesi di ogni libro, limitati fra 0,5/N e 2/N, ricalcolati ogni mese. Prior: nulla o
  negativo (si compra alto; l'evidenza sul «momentum dei gestori» è debole e rumorosa). Regola di decisione:
  come per i pesi della parte E, +0,05 in entrambe le metà, altrimenti 1/N.
- **D3. Promozione e ritiro.** Non si misura: è governo del desk, e lo si dichiara. Un candidato diventa
  nucleo dopo 12 mesi di eseguiti reali con Sharpe realizzato sopra 0 e perdita massima entro quella del suo
  backtest; un libro, nucleo o candidato, viene fermato (posizione chiusa, conto in attesa) dopo 24 mesi con
  Sharpe realizzato sotto −0,5; un libro fermato si riapre solo per decisione del proprietario. Le soglie sono
  numeri tondi dichiarati, non ottimizzati; i mesi partono dall'8 ottobre 2026 per i quattro e dal 13 per i
  satelliti.
- **D4. Regimi.** Tre interruttori con tre gradi di evidenza. (i) *Bolla, misurabile*: Greenwood, Shleifer e
  You (2019), un raddoppio del prezzo in due anni precede un crollo del 40% nel 53% dei casi (azioni); regola:
  dopo un +100% a due anni del prezzo del veicolo, obiettivo di volatilità dimezzato per 12 mesi. Si misura
  sul WTI 1986-2026 e sul fondo Brent; regola di decisione come D1. (ii) *Stretto chiuso, non misurabile*
  (sette mesi di dati): con i transiti di Hormuz sotto il 10% della norma, tetto da fine settimana dimezzato.
  Entra come scelta di politica, dichiarata, perché un gap del +16% in un fine settimana è già successo nel
  2026 e il costo della regola è una parte del rendimento dei venerdì. (iii) *Volatilità implicita*: OVX è già
  nel dimensionamento attraverso la volatilità realizzata; non si aggiunge nulla.

### C. I mercati che sanno prima: Kalshi

API pubblica, senza chiave (`api.elections.kalshi.com/trade-api/v2`), sondata la sera del 13 ottobre: esistono
serie sul WTI (`WTIH`, sopra/sotto ogni ora; `WTIMIN`, minimo annuale; `KXWTIVSBRENT`), e il catalogo completo
va letto da un runner con rete (il workspace non arriva oltre i registri). Che cosa si fa: (1) un adattatore
che archivia ogni giorno i mercati su petrolio, stretto, Iran, cessate il fuoco e OPEC con i loro prezzi
(probabilità); (2) sul terminale, la probabilità di «WTI sopra X» del mercato a eventi accanto a quella
implicita nelle opzioni USO che il desk già scarica: la divergenza è un dato nuovo, senza storico; (3) nessun
libro la negozia finché non ha almeno un anno di archivio. Prior sul segnale: ignoto; sulla liquidità: sottile.

### Risultati (13 ottobre, notte; registrazione sopra, numeri dopo)

**A. I temi.** Replay reale, distribuzioni accreditate, mezzo spread dichiarato:

| Libro | Anni | Sharpe (t) | Rend. annuo | Vol | Perdita max | Anni positivi | A costi doppi | Comprato e tenuto |
|---|---|---|---|---|---|---|---|---|
| Uranio, URA | 14,7 | 0,35 (1,3) | +3,0% | 9,8% | −28% | 6/15 | 0,33 | 0,17 (−0,2%, −87%) |
| Utilities, XLU | 25,5 | 0,43 (2,2) | +3,1% | 7,8% | −20% | 15/26 | 0,42 | 0,48 (+7,6%, −52%) |
| Litio, LIT | 15,0 | **0,67 (2,6)** | +5,4% | 8,2% | −24% | 6/16 | 0,66 | 0,39 (+7,4%, −66%) |
| Semiconduttori, SMH | 25,1 | 0,47 (2,4) | +4,6% | 10,8% | −21% | 18/26 | 0,46 | 0,58 (+14,4%, −70%) |
| Energia pulita, ICLN | 17,1 | **0,75 (3,1)** | +6,9% | 9,4% | −20% | 8/18 | 0,74 | 0,18 (+1,1%, −73%) |

Tutti e cinque passano il filtro (il filtro non ha morso: è una selezione, e lo si dice). Correlazioni con i
libri sul greggio fra −0,08 e 0,09; SMH e SPY 0,65, LIT/ICLN/SMH fra loro 0,35-0,39 (una famiglia, non cinque
mercati). Il portafoglio a capitale uguale dal 30 gennaio 2013:

| Portafoglio | Sharpe (t) | Rend. annuo | Vol | Perdita max | Prima / seconda metà |
|---|---|---|---|---|---|
| I tre più oro, rame, agricoltura, TLT, SPY (otto) | 0,93 (3,4) | +10,6% | 11,6% | −22% | 0,83 / 1,01 |
| Gli otto più i cinque temi (tredici) | **1,18 (4,4)** | +9,1% | 7,7% | −13% | 0,97 / 1,36 |
| I cinque temi da soli | 1,07 (3,9) | +6,2% | 5,9% | −10% | 0,61 / 1,45 |

Metà del guadagno dei temi sta nella seconda metà (2020-2026: litio, energia pulita, nucleare hanno avuto i
loro anni); nella prima valgono 0,61. Entrano come candidati, tutti e cinque, con TLT e SPY: dodici libri
candidati accanto ai quattro.

**B. L'allocatore.** Misurato sulle curve dei libri (regola applicata al rendimento del giorno dopo; il motore
fa lo stesso attraverso l'obiettivo di volatilità):

| Regola | Portafoglio | Senza → con: Sharpe | Perdita max | Rend. annuo | Metà |
|---|---|---|---|---|---|
| D1 controllo del drawdown (DD max 20%, pavimento 0,25) | tre libri greggio | 0,80 → 0,66 | −61% → −28% | +21% → +10% | 0,83/0,79 → 0,69/0,63 |
| | otto | 0,93 → 0,88 | −22% → −15% | +10,6% → +7,8% | 0,83/1,01 → 0,78/0,97 |
| D2 momentum delle strategie (36 mesi) | otto, dal 2016 | 0,80 → 0,61 | −22% → −24% | +8,8% → +7,6% | 1,03/0,55 → 0,77/0,39 |
| D4(i) interruttore bolla (+100% in due anni → metà rischio per un anno) | tre libri greggio | 0,80 → **0,87** | −61% → −52% | +21% → +23% | 0,83/0,79 → 0,84/0,90 |
| | tre più tre satelliti | 0,87 → **0,94** | −30% → −24% | +12,8% → +13,5% | 0,76/0,97 → 0,77/1,08 |
| | otto | 0,93 → **1,00** | −22% → −18% | +10,6% → +11,1% | 0,83/1,01 → 0,85/1,13 |

Verdetti con le regole scritte prima. *D1 non entra*: taglia la perdita massima di un terzo, ma costa 0,14 di
Sharpe sul greggio (il moltiplicatore sta al pavimento metà dei giorni: un portafoglio al 29% di volatilità
vive in drawdown) e 0,05 sugli otto, e il rendimento composto di un terzo; la stessa perdita massima si ottiene
con un obiettivo di rischio più basso senza perdere Sharpe. Resta nella configurazione, spento, come leva di
preferenza del proprietario (`config/allocator.yaml`), non come regola misurata. *D2 non entra*: peggio di
1/N in entrambe le metà, come previsto. *D4(i) entra*: più Sharpe e meno perdita in tutti i portafogli e in
entrambe le metà, con parametri pubblicati e non toccati; i giorni dimezzati sono il 18% della storia del fondo
Brent (2018-19, 2022-24, 2026) e il 30% di quella del WTI (quasi tutta nell'era della backwardation 1988-2006,
poi 2022-24 e 2026); oro 2025-26, rame 2022-23, SPY 2011-12 e 2022-23, mai per agricoltura e Treasury. Oggi è
acceso sul Brent e sul WTI: i tre libri sul greggio partono con l'obiettivo di volatilità dimezzato. *D3* e
*D4(ii)* entrano come politiche dichiarate, non misurate.

**C. Kalshi.** Catalogo letto dal runner: 15 011 serie, 400 000 mercati aperti nelle prime 400 pagine. Le serie
che servono esistono: Brent giornaliero, settimanale e mensile (`KXBRENTD`, `KXBRENTW`, `KXBRENTMON`), WTI
(`KXWTI`, `KXWTIW`, `KXWTIMONTHLY`, più i 15 minuti e l'ora), lo stretto (`KXCLOSEHORMUZ`, `KXHORMUZRETURN`,
`KXHORMUZNORM`, `KXHORMUZWEEKLY`, `KXMAXSHIPSHORMUZ`), Iran (`KXUSAIRANAGREEMENT`, `KXUSIRANMEETING`,
`KXIRANCRUDE`), OPEC (`KXOPECCUTS`), scorte EIA (`KXEIACRUDEW`), produzione saudita (`KXSAUDICRUDE`). Un
mercato porta bid e ask del sì, ultimo prezzo, volume, open interest, soglia e regola di regolamento (il WTI
giornaliero regola sul contratto ICE WBS). Limite di richieste: un 429 dopo una trentina di chiamate serrate;
l'adattatore ne fa una al secondo. Dall'archivio di oggi in poi il desk salva ogni giorno i prezzi di quei
mercati; il primo test possibile fra un anno.

Prima lettura dal vivo, dal runner, il 14 ottobre alle 01:22 UTC (296 mercati in 13 eventi;
`tests/fixtures/kalshi_markets_2026-10-14.json` ne conserva i record veri, nella forma dell'API). Serie per
serie: `KXBRENTD` (il Brent del 14 alle 17:00 di New York, venti soglie da 97 a 106,5 $: mediana del pubblico
104,2 $), `KXBRENTW` (il Brent di venerdì 16, da 82 a 120 $: sopra 102 al 70%, sopra 104 al 53%, sopra 106 al
35%, mediana 104,4 $), `KXBRENTMON` (il 30 ottobre, mediana 104,1 $), `KXWTI` (il WTI del 14 e del 15 per
soglia, mediane 95,3 e 94,4 $, più una scala a 75 soglie sul regolamento del 3 novembre, mediana 94,3 $),
`KXWTIW` (il 16 ottobre: un «sotto 80», venticinque intervalli di un dollaro e un «sopra 104,99»: sommati
dall'alto danno una mediana di 94,7 $), `KXHORMUZNORM` (media a 7 giorni dei transiti sopra 60, PortWatch:
entro il 1° novembre 2%, dicembre 4%, gennaio 2027 5%, aprile 25%, luglio 2027 38%, gennaio 2028 54%, gennaio
2029 67%; 1-3 milioni di contratti per data), `KXHORMUZWEEKLY` (transiti dal 12 al 18 ottobre: mediana 22, cioè
tre al giorno, contro i 0,9 al giorno di petroliere che PortWatch dava al desk al 4 ottobre), `KXMAXSHIPSHORMUZ`
(massimo giornaliero di ottobre: almeno 8 al 52%, almeno 10 al 27%), `KXUSAIRANAGREEMENT` (accordo nucleare
entro il 1° gennaio 2027 al 12%, entro il 2028 al 35%, entro il 20 gennaio 2029 al 39%), `KXUSIRANMEETING`
(incontro di alto livello entro dicembre 23%, gennaio 42%, febbraio 56%, aprile 2027 80%), `KXEIACRUDEW` (le
scorte della settimana al 9 ottobre: mediana 421,4 milioni di barili contro 424,1 della settimana prima).
Cinque serie non avevano alcun mercato aperto (`KXCLOSEHORMUZ`, `KXHORMUZRETURN`, `KXOPECCUTS`, `KXSAUDICRUDE`,
`KXWTIMONTHLY`) e nessun mercato era oltre la sua chiusura.

Due cose imparate. La prima: le sonde fatte dal workspace prima della messa in linea, attraverso il proxy di
lettura, avevano restituito per alcune serie copie vecchie di mesi (la scala mensile del Brent del 31 luglio
ancora «attiva», lo stretto «normale entro dicembre» al 38% invece del 4%): quelle cifre erano sbagliate ed
erano finite in una prima versione di questi appunti; conta solo lo scaricamento del runner, e la guardia del
motore contro un mercato oltre la chiusura resta come guardia, non come descrizione di Kalshi. La seconda: la
mediana del pubblico per il Brent di venerdì (104,4 $) stava 2,5% sotto il contratto di dicembre che il desk
quotava quella sera (BZZ26, 107,0 $), e lo stesso scarto si vede sul giornaliero e sul mensile: il mercato
regola sul proprio feed del Brent (Pyth), non sul contratto ICE, e in una backwardation del 25% l'anno un feed
che segua un mese più avanti vale circa il 2% in meno. Lo scarto è in parte una base, non un'aspettativa, e il
terminale lo scrive accanto al numero. Il mercato porta anche il sottotitolo che distingue le soglie («Above
$103.99», «Before Jan 1, 2027»), che l'adattatore archivia, e non porta il codice della serie: lo si ricava
dalla domanda. Che cosa ne fa il motore (`engine/desk/prediction.py`): un record per evento e giorno in
`state/desk/kalshi-AAAA-MM.jsonl`; sul terminale la mediana del pubblico, la probabilità «sopra il prezzo di
adesso» (o il suo limite, quando il prezzo è fuori dalla scala) e, accanto, quella delle opzioni del fondo
sotto una lognormale alla volatilità implicita ATM (segnata ≈: senza smile e senza base fra fondo e future);
per le scadenze la scala delle date; per le soglie la mediana accanto al dato che il desk misura da sé.
Nessun libro legge quei numeri.

### D. Che cosa questo cambia nelle regole dure

«La taglia di un libro è previsione / 10 × obiettivo / volatilità, nient'altro» diventa «... × obiettivo ×
m / volatilità», dove m è il moltiplicatore dell'allocatore (D1 e D4), uguale per tutti i libri, scritto ogni
giorno nello stato e sul terminale. Le decisioni dei libri non cambiano in nessun altro modo. Il backtest
riporta i numeri con e senza allocatore, sempre.

### F. L'allerta di attività anomala sui mercati a eventi (14 ottobre, scritta prima di accenderla)

Il proprietario: «su Kalshi c'è molto insider trading: inseriscilo come allerta». Che cosa si sa. Sulle azioni,
Meulbroek (1992) misura che nei giorni in cui un insider negozia il prezzo si muove e il volume è anomalo: le
due cose insieme sono la firma, nessuna delle due da sola. Sui mercati a eventi la firma è la stessa, più
visibile perché il mercato è sottile: Rothschild e Sethi (2016) ricostruiscono su Intrade 2012 un singolo
operatore che compra da una parte sola, grosso, a lungo; nei casi pubblici recenti (sei portafogli nuovi che
comprano «attacco USA all'Iran entro il 28 febbraio» a 10-20 centesimi poche ore prima dei raid del 28 febbraio
2026 e incassano circa un milione, segnalati da Bubblemaps e ricostruiti da The Block; il riservista israeliano
incriminato per le scommesse su Polymarket sull'apertura della guerra dei dodici giorni del giugno 2025, basate
su informazioni classificate) il quadro è: una mossa grande, in una direzione, su un volume molto sopra il
solito del mercato, attraverso poche operazioni grosse dallo stesso lato, prima della notizia. Nessuno
distingue da fuori un insider da chi copre un rischio con informazione lecita o da una notizia che è arrivata
prima al mercato che ai giornali: l'allerta segnala l'impronta e dice che cos'è.

La regola (`engine/desk/watch.py`), per mercato, a ogni lettura (il giro legge i mercati ogni ora):
1. il mercato è un mercato a eventi (lo stretto, i colloqui, l'OPEC, le scorte EIA, ...), non una scala di
   prezzo su Brent o WTI: una scala si muove con il prezzo del greggio, e questo non è informazione su un
   evento;
2. la mossa in 24 ore (la quotazione «di un giorno fa» che l'API stessa riporta, contro quella di adesso) è di
   almeno 10 punti di probabilità, oppure di almeno 5 punti con le odds moltiplicate o divise per 3 (dal 3%
   al 9% le odds triplicano; dal 45% al 51% no);
3. il volume delle 24 ore è insolito: almeno 3 volte la mediana dei volumi giornalieri del mercato e sopra il
   suo 90° percentile, quando ci sono almeno 10 letture giornaliere nell'archivio (`state/desk/kalshi-*.jsonl`,
   che da oggi registra `volume_24h`); prima di allora almeno il 25% dell'open interest, per un mercato aperto
   da almeno 48 ore (il primo giorno di un mercato è tutto il suo volume) - e mai sotto 500 contratti.
I mercati segnalati di uno stesso evento (le soglie di una scala) fanno UNA allerta, scritta una volta per
evento e giorno UTC (`state/desk/kalshi_alerts-AAAA-MM.jsonl`), che dice se il resto della scala si è mosso
(una notizia muove tutta la scala; una soglia sola che si muove è la vista più strana) e porta l'impronta delle
operazioni del giorno sul mercato segnalato più scambiato, quando si sono potute leggere (`/markets/trades`,
poche chiamate al giorno): quante, la quota delle cinque più grosse, la quota dal lato dominante del taker, la
più grossa e la prima delle grosse. Il job delle allerte apre una issue su GitHub per le allerte degli ultimi
due giorni; il terminale mostra quelle dell'ultima settimana. Nessun libro legge nulla di tutto questo. Le
soglie sono numeri tondi dichiarati, non stimati: non c'è una storia su cui stimarli, e un'allerta non è una
regola di negoziazione.

Provata sulla prima lettura con i campi delle 24 ore (il runner, 14 ottobre, 03:03 UTC) e su una settimana
di operazioni vere scaricate dallo stesso runner (`tests/fixtures/kalshi_markets_2026-10-14T03.json`,
`kalshi_trades_2026-10-14.json`): il mercato più scambiato del giorno, «stretto normale entro il 1°
novembre», 181 506 contratti in 24 ore di cui 100 000 in una sola operazione a un centesimo, non si è mosso
(un rendimento del 2% in due settimane comprando il NO: non è informazione) e non è segnalato; le scale del
WTI del 14 salite di 30-50 punti con il greggio sono fuori dal campo; due allerte vere: la scala delle scorte
EIA della settimana al 9 ottobre, tre soglie scese di 10-24 punti su tutto il loro open interest la sera in
cui esce il rapporto dell'API (una notizia pubblica: tutta la scala si è mossa, e l'allerta lo scrive), e la
soglia «più di 25 transiti» della settimana, scesa di 12 punti su due volte il suo open interest con il resto
della scala fermo (25 operazioni, le cinque più grosse il 54%, l'82% con il taker sul NO). Sull'archivio della
settimana si vede anche l'impronta tipica senza mossa: il 13 ottobre alle 15:54-15:56 UTC un solo taker ha
venduto SÌ su tutte le date 2027 della scala dello stretto (140-850 contratti ciascuna). Il tasso di falsi
allarmi è da misurare sulle settimane a venire; se supera un'allerta al giorno le soglie vanno riviste, e
scritte qui.

## 10. Fase 14 (15 ottobre 2026, 03:20 UTC): un libro speculativo su Kalshi, scritto prima di ogni risultato

Il proprietario: «voglio che crei una strategia basata su Kalshi a leva, altamente speculativa». Questo capitolo
è la registrazione della regola, scritta prima che il libro prenda una decisione e prima che esista un numero
da guardare: non esiste una storia di questi mercati su cui provarla (l'archivio del desk è nato il 14 ottobre
e ha due letture), quindi non c'è un backtest, e il libro si giudica solo sugli eseguiti veri. Tutto ciò che
segue è dichiarato, non stimato; ciò che verrà misurato è scritto in fondo, con quando.

### A. Che cosa si può fare, e che cosa no

Un contratto Kalshi paga 1 $ se l'evento accade e costa la sua probabilità: non si compra a margine, e il
desk non ha (né avrà qui) un broker di carta per contratti a evento. La leva, quindi, non sta su Kalshi: sta
sul petrolio. Il libro usa il future micro WTI (/MCL, 100 barili, margine 10%), l'unico strumento a leva che
il desk negozia, e Kalshi gli dà la **direzione**. È una strategia su una fonte di informazione, non su un
mercato nuovo.

### B. L'ipotesi, e il prior

L'ipotesi: i mercati a eventi sulla fine della guerra (lo stretto che torna normale, un incontro di alto
livello fra Stati Uniti e Iran, un accordo nucleare) incorporano la de-escalation prima del prezzo del greggio,
e quando il pubblico alza quelle probabilità il premio geopolitico nel petrolio si sgonfia dopo. Il prior,
scritto qui: **nessun vantaggio**. Il petrolio è mille volte più liquido di quei mercati e i partecipanti
sono gli stessi lettori di notizie; è più probabile che Kalshi segua il petrolio con un giorno di ritardo che
il contrario, e in quel caso il segnale è un trend del petrolio letto in ritardo e con il segno opposto a quello
che il libro «spinto» già compra. Snowberg, Wolfers e Zitzewitz (2007) hanno misurato la notte delle elezioni
del 2004 che le probabilità dei mercati a eventi e i future sull'indice azionario si muovono insieme entro
minuti: comovimento, non anticipo (ricordato, non riletto). Nessuno studio noto misura se Kalshi anticipi i
future sul petrolio. Il libro esiste per misurarlo con soldi di carta, e il nome che porta - «Oracle» - è
ironico quanto basta.

### C. La regola (`engine/desk/oracle.py`, costanti con lo stesso nome)

1. **Le componenti** (`COMPONENTS`): tre scale a scadenza, già archiviate ogni giorno dal desk:
   `KXHORMUZNORM` («la media a 7 giorni dei transiti nello stretto sopra 60 prima del giorno X»),
   `KXUSIRANMEETING` («un incontro di alto livello USA-Iran prima di X»), `KXUSAIRANAGREEMENT` («un accordo
   nucleare prima di X»). Le tre vie per cui la guerra finisce; pesi uguali (1/N), nessun'altra scelta.
2. **La probabilità a orizzonte costante** (`HORIZON_DAYS = 90`): per ogni scala, P(l'evento accade entro 90
   giorni dalla lettura), letta sui punti (scadenza, mid) dei mercati della scala con quotazione a due lati
   - un'offerta sopra zero e una domanda sotto uno: su questa API bid 0 vuol dire nessun bid e ask 1 nessun
   ask - e spread non oltre 10 centesimi (`MAX_SPREAD = 0.10`: un mercato quotato più largo o da un lato
   solo non è una probabilità, è un'assenza), più il punto (oggi, 0) - un mercato aperto non è ancora
   accaduto -, interpolando
   linearmente nel tempo; oltre l'ultima scadenza vale l'ultima. Una scadenza fissa perde probabilità da sola
   con il passare dei giorni; l'orizzonte costante toglie quella deriva al primo ordine. Il pubblico non è
   forzato alla monotonia: la scala dell'accordo, il 14 ottobre, dava più probabilità a gennaio 2027 che a
   febbraio, e si legge com'è. Se una serie ha più eventi aperti lo stesso giorno conta il più scambiato.
3. **L'indice di de-escalation** D = la media delle componenti presenti nella lettura.
4. **La lettura** (`LOOKBACK_DAYS = 7`, `REFERENCE_WINDOW_DAYS = 3`, `STALE_DAYS = 3`): la decisione del giorno
   D legge la lettura archiviata del giorno UTC D (la prima della giornata, verso le 00:20 UTC, cioè la sera
   prima a New York: il libro legge i prezzi di ieri sera, come i segnali macro leggono la chiusura di ieri)
   o, se manca, la più recente non più vecchia di tre giorni; come riferimento la lettura più recente fra 7 e
   10 giorni prima. La variazione ΔD è la media, sulle componenti presenti in **entrambe** le letture, di
   p_oggi − p_riferimento: una scala che nasce o si risolve non è una mossa - e questo include l'evento
   stesso: l'adattatore scarica solo i mercati aperti, quindi quando una scala si regola (l'incontro avviene,
   ogni suo mercato paga e sparisce) la componente scompare e il libro ne vede la rincorsa, mai l'arrivo. Un
   giorno i cui record non hanno alcuna componente leggibile (quotazioni da un lato solo o più larghe del
   limite) non è una lettura: vale la più recente leggibile entro tre giorni, e nella finestra del riferimento
   la più recente leggibile (trovato in revisione: un giorno vuoto chiudeva una posizione a leva e la riapriva
   il giorno dopo). Senza una lettura recente il libro non ha una vista e sta piatto (previsione 0); senza
   riferimento (l'archivio ha meno di una settimana) non decide e resta dov'è, cioè piatto all'inizio: la
   prima decisione possibile è del 21 ottobre.
5. **La previsione** (`FORECAST_PER_POINT = 2.0`, `DEADBAND = 0.01`): F = −2 × (ΔD in punti percentuali),
   limitata a ±20; sotto un punto di variazione F = 0 (un centesimo è lo spread). Cinque punti di
   de-escalation in una settimana valgono −10, lo short «normale» della scala di Carver; dieci punti −20, il
   massimo; cinque punti di escalation +10, long. La scala non è misurata (non c'è su che cosa): è il numero
   tondo per cui una mossa che fa notizia (dieci punti) satura la previsione. Carver stima lo scalare della
   previsione sui dati perché la media di |F| sia 10: si farà dopo 60 letture (fine dicembre), scrivendo qui
   la media dei |ΔD| settimanali, e la scala si toccherà solo se è fuori di un fattore due - e si scriverà.
6. **La taglia**: la regola di tutti i libri, nient'altro: `F/10 × obiettivo × m / volatilità` con l'obiettivo
   di volatilità **0,50**, il massimo che le regole dure permettono (Kelly pieno su uno Sharpe di 0,5 che il
   libro non ha: è la richiesta di «altamente speculativo» e il motivo per cui può azzerarsi), `m`
   dell'allocatore come per ogni altro libro (oggi 0,5: il WTI è raddoppiato in due anni, quindi il libro parte
   con l'obiettivo dimezzato e lo scrive), tetti 10x e 3x prima di una chiusura (1,5x con lo stretto chiuso),
   stop giornaliero −15%, banda d'inerzia 10%, lotto intero. Con il greggio al 44% di volatilità una
   previsione di −20 vale 2,3x di esposizione, cioè due o tre micro contratti su 10 000 $: la leva alta è un
   tetto, non un programma, come per il libro spinto.
7. **Il libro**: `oracle`, «Oracle», `role: candidate`, `forecast: kalshi`, veicolo MCL, long e short, 10 000
   $, un conto a sé, nel totale dei candidati. Non entra nel backtest del desk (nessuna serie da riprodurre nel
   replay; la storia scaricata il 15 ottobre, sezione H, è stata misurata a parte), e il sito lo scrive su ogni
   riga che lo riguarda. La decisione registra tutti gli input (indice, componenti,
   riferimento, variazione, età delle due letture): fra un anno la regola si riproduce riga per riga
   sull'archivio, ed è quello il primo test.

### D. Che cosa lo ferma

Le protezioni dei libri (lo stop giornaliero, il margine, il conto morto a 500 $) e la regola del ciclo di
vita come per ogni libro: ritirato dopo 24 mesi con Sharpe realizzato sotto −0,5. **Mai promosso**: la
promozione richiede un drawdown del backtest da confrontare, e questo libro non ne ha (la regola del ciclo di
vita, da oggi, non promuove un libro senza backtest: prima lo avrebbe fatto). Il proprietario lo rivede dopo sei
mesi, circa 125 decisioni, con lo Sharpe realizzato e il suo errore standard (±0,7 su sei mesi: sei mesi non
distinguono 0 da 1). Nessun parametro si tocca prima del riesame della scala del punto 5.

### E. Che cosa cambia nelle regole dure

«I mercati a eventi sono archiviati e mostrati, mai negoziati: nessun libro li legge finché non c'è un anno
di archivio» diventa: nessun libro del **nucleo** li legge; un libro **candidato dichiarato speculativo**, su
richiesta esplicita del proprietario, può negoziare una regola scritta qui prima della prima decisione, è
giudicato sul solo record live, non ha backtest e non è promosso dal ciclo di vita. I quattro libri del nucleo,
i dodici candidati e il loro backtest non cambiano di un byte (verificato rieseguendo il replay prima e dopo).

### F. La revisione prima della messa in linea

Un revisore che non aveva visto scrivere il codice lo ha attaccato con script propri (la matematica contro la
regola, il calendario, i due libri sullo stesso future per sette giorni e sedici giri al giorno con un roll e un
weekend, l'archivio corrotto, l'esportazione, la taglia). Corretto: una lettura senza componenti leggibili
contava come la lettura del giorno e chiudeva la posizione (punto 4); una quotazione da un lato solo passava
per una probabilità (punto 2); una riga tagliata nell'archivio (il runner viene cancellato a ogni push mentre
scrive) faceva fallire l'esportazione di `desk.json` e, ogni giro, la previsione del libro, che restava con la
posizione aperta e mai ridimensionata - ora la riga è saltata con un avviso, e il blocco del terminale dice
l'errore invece di far cadere la pagina; ogni libro è alimentato con i propri contratti e un libro piatto è
marcato sul contratto del giorno (prima: il primo in ordine alfabetico dei contratti tenuti da tutti i libri del
veicolo, che con due libri sullo stesso future faceva dipendere l'età dei prezzi di uno dal roll mancato
dell'altro); la decisione «dati mancanti» ora porta le scale dell'allocatore. Verificato dal revisore e da me:
le decisioni, gli eseguiti e l'equity del libro spinto sono identici con e senza l'Oracle; il replay dei
tredici libri è identico byte per byte (impronta 539b55e08479 → 62d61be3e097 per il campo nuovo di
`BookConfig`: un ricalcolo al primo giro, stessi numeri). Non corretto, scritto: con «dati mancanti» un ordine
ancora in coda dal giorno prima viene cancellato e non riemesso (vale per ogni libro quando manca la
volatilità; la motivazione lo dice).

### G. La lettura di partenza (non un risultato)

Le due letture in archivio, con la regola del punto 2 (orizzonte 90 giorni, cioè il 12-13 gennaio 2027, fra le
scadenze del 1° gennaio e del 1° febbraio): lo stretto normale 6,6% il 14 e 6,6% il 15; un incontro di alto
livello 46,8% e 45,6%; un accordo 11,0% e 11,7%; indice 21,5% e 21,3%. Una variazione di −0,2 punti in un
giorno, dentro la banda morta. Il mercato dell'incontro è il meno liquido dei tre (spread di 8-9 centesimi,
volume zero sulle date lontane): è lui che farà il rumore, e il filtro sullo spread lo lascia dentro di poco.

### H. L'archivio riempito all'indietro, e il primo test (15 ottobre, pomeriggio)

Il proprietario, la mattina dopo: «non funziona perché mancano dati». Vero: il libro confronta la lettura di
oggi con quella di una settimana prima e l'archivio aveva due giorni. E la frase «non esiste una storia di
questi mercati» era sbagliata: l'API pubblica le candele giornaliere di ogni mercato dal giorno in cui è stato
aperto (`GET /series/{serie}/markets/{mercato}/candlesticks`, intervallo 1440 minuti: offerta e domanda alla
chiusura del periodo, ultimo scambio, contratti del giorno, open interest; i mercati archiviati dopo il
regolamento su `/historical/markets/{mercato}/candlesticks`). Scaricate dal runner alle 17:59 UTC (`research-data`,
`kalshi-history`; `tests/fixtures/kalshi_history_2026-10-15.json` ne conserva le tre serie dell'indice):
lo stretto dal 18 marzo (la scala è nata il 17; date aggiunte il 14 aprile, l'8 giugno, l'8 luglio, il 24
agosto e l'8 ottobre; tre regolate NO: 15 agosto, 1° settembre, 1° ottobre), l'accordo dal 9 gennaio (il
mercato «entro il 1° gennaio 2027»; gli altri dal 14 aprile, 21 maggio, 21 luglio; due regolati NO), l'incontro
dal 24 settembre (sette mercati, uno regolato NO il 1° ottobre). Una candela giornaliera finisce a mezzanotte
di New York (04:00 UTC d'estate); la lettura del giro è delle 00:20 UTC, cioè le 20:20 di New York della sera
prima: sono entrambe «la sera prima del giorno di decisione», e si confrontano (lo stretto entro un centesimo
fra la candela e la lettura del 15; il mercato dell'incontro, sottile, 42% contro 47% a tre ore di distanza).

Che cosa fa il motore (`engine/desk/backfill.py`, `engine.cli kalshi-backfill`, il workflow manuale
`kalshi-backfill.yml`): per ogni giorno UTC in cui un mercato era aperto e la sua candela è completa, il record
che il giro avrebbe scritto (stesso formato, `backfilled: true` e la fonte), aggiunto all'archivio solo per i
giorni che non hanno una lettura del giro: una lettura viva non viene mai sostituita. Il volume cumulato è la
somma delle candele dal primo giorno (esatto per i mercati scaricati dall'apertura), `prev_mid` il mid della
candela prima; l'allerta legge solo le letture vive, l'Oracle non legge né l'uno né l'altro. L'indice sulla
storia: solo l'accordo fino al 17 marzo (7-15%); dal 18 marzo due componenti: 20% → 46% a metà aprile → il
massimo 57,5% il 15 giugno → 18% a metà luglio → il minimo 8,1% l'11 settembre → 21-25% in ottobre con
l'incontro come terza componente dal 24 settembre.

**Il primo test della regola del punto 5, com'è scritta** (`scripts/research_oracle.py`, sulla fixture e su
una copia del branch dati del 13 ottobre: la serie del WTI investibile del desk fino al 12 ottobre): per ogni seduta D dal 18 marzo al 12 ottobre la lettura del giorno D (la candela) contro il
riferimento di 7-10 giorni prima, la previsione, la taglia della regola (`F/10 × 0,50 × m / vol`, con m = 0,5
nei giorni in cui l'interruttore della bolla era acceso sul WTI, cioè quasi tutti), posizione presa alla
chiusura di D sul rendimento di D+1; costi del veicolo (1,27 $ per lotto e lato più 1,5 pb). 143 sedute,
previsione diversa da zero in 130 (57 short, 73 long).

| Misura | Valore |
|---|---|
| Taglia continua: Sharpe (errore standard) | **1,19 (±1,7)** |
| rendimento annuo, volatilità | +63%, 53% |
| Su 10 000 $ a lotti interi: risultato netto | **+2 771 $ (+27,7%)**, 48 operazioni, 162 $ di costi, al massimo 3 lotti |
| mese per mese (taglia continua) | mar −1,8%, apr +9,6%, mag −0,9%, giu +1,5%, **lug +36,2%**, ago −7,8%, set +7,6%, ott −8,4% |
| media di \|ΔD settimanale\|, mediana | 6,3 punti, 3,8 |
| media di \|F\| | **9,84** (la normalizzazione di Carver vale: la scala non si tocca) |

Come leggerlo: lo Sharpe sta entro un errore standard da zero; un mese su otto fa il risultato (luglio: il
rendimento del 7 e del 10 luglio con tre lotti); e il campione è lo stesso periodo in cui la regola è stata
pensata. Il prior di nessun vantaggio resta. La scala dichiarata al punto 5 si verifica qui invece che a
dicembre, con 143 sedute: la media di |F| è 9,84, cioè 10 - esattamente la normalizzazione che Carver chiede;
nulla da cambiare, e il riesame si ripete a dicembre sul record live.

**Una cosa vista guardando i dati, non registrata prima.** La correlazione fra la variazione GIORNALIERA
dell'indice (la giornata di New York D−1, dalla mezzanotte alla mezzanotte) e il rendimento del petrolio è
−0,27 sullo stesso giorno (le due finestre si sovrappongono la sera di D−1, dopo il regolamento), **−0,21 sul
giorno dopo** (D+1: t ≈ 2,5 su 143 sedute, la finestra che una decisione presa alle 14:35 di D può ancora
prendere), −0,10 il giorno prima; la variazione settimanale contro il rendimento della settimana dopo: +0,03.
Cioè: la mossa di un giorno ha il segno che la regola presume e sembra precedere il petrolio di un giorno; la
variazione di una settimana, quella che la regola registrata usa, non porta nulla all'orizzonte di una
settimana, e il risultato sopra viene dai giorni subito dopo una mossa. È un'osservazione fatta dopo aver
guardato, su 143 giorni: una regola sulla variazione giornaliera sarebbe un candidato nuovo da registrare e
provare sul record live, non una modifica a questo libro oggi. Il libro resta com'è scritto. La prima decisione
con un riferimento è quella del 15 ottobre: 21,3% contro 25,1% dell'8, −3,8 punti, previsione +7,5 (long, sotto
la convinzione normale, meno di un lotto intero con l'obiettivo dimezzato).

## 11. Fase 15 (15 ottobre 2026, 23:30 UTC): Kalshi come riferimento sugli altri mercati, registrato prima di misurare

Il proprietario: «improve su altri mercati e anche speculative kalshi sempre come riferimento». Letto così: portare
lo schema dell'Oracle (capitolo 10) - un indice letto sull'archivio dei mercati a eventi dà la **direzione**, il
veicolo dà la leva - sugli altri mercati del desk, con Kalshi come fonte della direzione. Questo capitolo è scritto
prima di qualunque misura: A dice quali mercati Kalshi esistono con una storia (scaricata il 15 ottobre), B le
regole, C il prior, D il filtro e che cosa si misura; i numeri stanno in E, scritti dopo e separati.

### A. I mercati che esistono (catalogo e storia scaricati dal runner il 15 ottobre, sera)

Il catalogo di Kalshi (15 011 serie, lette il 13 ottobre) è stato cercato per tema - Fed, inflazione, recessione e
lavoro, Treasury, oro, S&P, Cina e dazi, chip, politica energetica ed elettricità, Ucraina, raccolti, Taiwan - e
le serie plausibili sono state scaricate con le loro candele giornaliere dal 1° gennaio 2026 (`research-data`,
`kalshi-history --series ...`, quattro scarichi). Che cosa c'è:

- **Fed.** `KXFEDDECISION`: per ogni riunione del FOMC cinque esiti (taglio di oltre 25 pb, taglio di 25,
  invariato, rialzo di 25, rialzo di oltre 25), quotati a uno-due centesimi sulle due riunioni vicine; dodici
  riunioni elencate, dal 16 settembre 2026 al 26 gennaio 2028. La lista dei mercati non mostra più le riunioni
  regolate da più di qualche settimana (settembre c'è, luglio no), quindi la storia leggibile con una riunione
  entro 90 giorni parte dal **18 giugno 2026**; un secondo scarico che chiede i mercati regolati per nome è in
  corso mentre questo paragrafo viene scritto, e se allunga la storia si usa e si scrive. `KXFED`: la scala «limite
  superiore sopra x%» dopo ogni riunione (11-25 strike, le code quotate da un lato solo). `KXFEDFUNDSYEAR`: il
  tasso a fine anno dal 2027 al 2036, spread di 10-40 centesimi. `KXRATECUTCOUNT`: quanti tagli nel 2026, 21
  strike dal 1° gennaio (con la Fed che alza, 97% su zero). `KXFEDCUT`, `KXFEDHIKE`: il prossimo taglio o rialzo
  entro una data (pochi giorni di storia, o quasi certi). `KXDOTPLOT`: la mediana del dot plot, dal 18 settembre.
  Senza mercati elencati: KXRATECUTS, KXTERMINALRATE, KXLOWESTRATE e i ticker senza prefisso (FEDDECISION, FED).
- **Recessione e lavoro.** `KXRECSSNBER-26` («una recessione nel 2026», 4-5%, candele dal 1° gennaio) e `-27`
  («nel 2027», 20-21%, dal 24 aprile), spread di un centesimo. `KXNBERRECESSQ` (il trimestre d'inizio: solo
  trimestri passati), `KXIMFRECESS` (una recessione globale secondo l'FMI prima del 2027: 2-3%), `KXU3EOY` (la
  disoccupazione a fine anno 2026-2036, dal 3 settembre), `KXGOVTSHUTDOWN` (governo chiuso il 12 dicembre: 33%,
  dal 22 settembre). Senza mercati: KXQRECESS, KXGDPUSMIN, KXACPI, KXLCPIYOY, KXSHUTLENGTH, KXGOVSHUT.
- **Cina e dazi.** `KXTARIFFAGREE` (un accordo USA-Cina sui dazi prima di una data: 45 giorni di storia, regolato
  SÌ il 24 settembre), `KXTRUMPCHINA` (una visita di Trump in Cina prima del 1° dicembre o del 1° gennaio: 20
  giorni, 85-92%), `KXEFFTARIFF` (il dazio effettivo del terzo trimestre, scala «sopra x%» con spread di 10-60
  centesimi), `KXNEWTARIFFS` (nuovi dazi nel mese: un mercato al mese), `KXCHINARARE` (15 giorni, regolato).
  Senza mercati: KXCHINATARIFF, KXTARIFFSPRC, KXTARIFFENDPRC, KXTARIFFSEMI, KXH200CHINA, KXCHIPSREPEAL e gli altri
  dazi cercati. Taiwan: `KXTAIWANLVL4` e `KXXITAIWAN`, scale a scadenza dal 7 gennaio ma su anni (1,6-7% entro il
  2026).
- **Energia ed elettricità.** Un mercato ciascuno con scadenza lontana: `KXDATACENTER` (un data center nucleare su
  una base militare prima del 2030), `KXREACTOR` (un reattore approvato nel 2026: 5%), `KX48ETAXCREDIT` (il
  credito solare 48E reintrodotto prima del 2030), `KXUSSAUDINUCLEAR`; le moratorie sui data center dal 14
  ottobre. Senza mercati: KXEVCREDITS, KXIRACUTS, KXIRAREPEAL, KXLICENSENUCLEAR.
- **Ucraina e Russia, El Niño, raccolti**: nessun mercato elencato, o tre giorni di storia.

Esclusi per costruzione, come le scale sul Brent (capitolo 9 C): le **scale di prezzo** (oro, S&P 500, Nasdaq, il
rendimento del decennale, il rame a 15 minuti) sono il prezzo stesso letto da un'altra parte; e le scale sui
**dati mensili** (CPI, buste paga, disoccupazione del mese) sono previsioni di una stampa, non di una politica o di
un evento - un candidato possibile per un'altra registrazione, non per questa.

Conclusione di A, prima di misurare: esistono, con una storia leggibile, **due** indici a evento che riguardano gli
altri mercati del desk - il percorso della Fed e la probabilità di recessione - più l'indice di de-escalation del
capitolo 10. Per rame, semiconduttori, litio, energia pulita, uranio e agricoltura non esiste oggi un mercato Kalshi
con la storia e la liquidità per un indice: quei libri restano com'erano, e questo capitolo lo dice invece di
inventare un segnale.

### B. Le regole (`engine/desk/oracle.py`: un registro di indici, `INDICES`, costanti con lo stesso nome)

Lo schema è quello dell'Oracle, punto per punto: la lettura del giorno UTC D o la più recente entro 3 giorni, il
riferimento fra 7 e 10 giorni prima, la variazione sulle componenti presenti in **entrambe** le letture, quotazioni
a due lati entro 10 centesimi, banda morta, tetto ±20. Tre indici:

1. **`deescalation`**: com'è nel capitolo 10, nessuna modifica (la manica vale −2 per punto: positiva quando
   l'escalation sale).
2. **`fedpath`**, il percorso della Fed. Componenti: le riunioni del FOMC con decisione entro **90 giorni** dalla
   lettura (gli eventi `KXFEDDECISION-AAMMM`; la data della riunione è la chiusura dei suoi mercati). Il valore di
   una componente è la variazione attesa del tasso obiettivo a quella riunione, in punti base:
   Σ p_i × bp_i / Σ p_i sugli esiti quotati a due lati, con bp = −50 per «taglio di oltre 25», −25, 0, +25, +50 per
   «rialzo di oltre 25» (l'«oltre 25» vale 50: convenzione dichiarata); una riunione è leggibile con almeno due
   esiti quotati e Σ p_i ≥ 0,80 (quel che manca sono code da un centesimo); *(aggiunto in revisione, G)* un esito
   offerto ad almeno 0,95 senza nessuna domanda a 1,00 - la riunione «certa» nei giorni prima - vale la sua
   offerta, non un'assenza. Il giorno della riunione è ancora «entro l'orizzonte» (la lettura è della sera prima
   e i mercati sono aperti fino alla decisione); il giorno dopo non ha più un mercato. L'indice è la **somma** sulle riunioni
   nella finestra: la variazione attesa cumulata nei prossimi 90 giorni. La variazione settimanale è la somma,
   sulle riunioni presenti in entrambe le letture, di e_oggi − e_riferimento: la sorpresa della riunione stessa non
   c'è (il giorno dopo quella riunione non ha più un mercato), la rilettura delle riunioni successive sì. La manica
   vale F = **−1,0 × Δ** (`FORECAST_PER_BP = 1.0`): positiva quando le attese si fanno più accomodanti (meno rialzi
   o più tagli prezzati), tetto ±20 (venti punti base riletti in una settimana lo saturano; una riunione intera,
   venticinque, lo supera), banda morta **1 punto base**. Scala tonda dichiarata; Carver vuole |F| medio 10, si
   verifica in E e si tocca solo se fuori di un fattore due.
3. **`recession`**, la probabilità di recessione. Componenti: i mercati annuali `KXRECSSNBER-AA` («una recessione
   nel 20AA»), letti come una scala a scadenza: P(entro il 31 dicembre dell'anno Y) = 1 − Π(1 − p_y) sugli anni
   fino a Y (indipendenza fra gli anni: approssimazione dichiarata, con il 2026 al 4% irrilevante), interpolata
   all'orizzonte costante di **365 giorni** (la `horizon_probability` del capitolo 10 con l'orizzonte a 365: una
   recessione è un evento lento e i mercati sono annuali; i 90 giorni dell'Oracle cadrebbero quasi sempre dentro
   l'anno in corso, che si svuota da solo). L'indice è quella probabilità. La manica vale F = **+2,0 × Δ** (punti
   percentuali): positiva quando la probabilità di recessione sale, tetto ±20, banda morta 1 punto. *(Corretto in
   revisione, G: la variazione si legge sugli anni quotati in ENTRAMBE le letture, con la scala del riferimento
   rivalutata alla data di oggi - così un mercato annuale che nasce, si regola o passa la sua fine non è una
   mossa, com'era scritto per le scale intere; la prima versione faceva saltare l'indice di 4-8 punti a ogni
   cambio d'anno e a ogni mercato nuovo, a quotazioni ferme.)*

I libri (`config/books.yaml`, `role: candidate`, `forecast: kalshi`, `sleeves` con il **segno** per il veicolo). La
previsione del libro è la media, con i segni, delle maniche che oggi hanno un valore; se nessuna ha un valore il
libro non decide e tiene quel che ha; nessun moltiplicatore di diversificazione (due maniche su temi vicini: la
media, non 1,2).

- `oracle_bonds` su **TLT**: fedpath +1 (attese più accomodanti → long Treasury: Gürkaynak, Sack e Swanson 2005
  sulle sorprese di politica monetaria e i rendimenti), recession +1 (recessione più probabile → long Treasury: la
  fuga verso la qualità).
- `oracle_equities` su **SPY**: fedpath +1 (Bernanke e Kuttner 2005: un taglio a sorpresa di 25 pb vale circa +1%
  sull'indice in giornata), recession −1 (long solo quando la recessione si allontana).
- `oracle_gold` su **GLD**: fedpath +1 (tassi reali e oro: Erb e Harvey 2013), deescalation +1 - lo stesso segno
  del petrolio: la de-escalation che sgonfia il premio geopolitico sgonfia anche il rifugio (Baur e Lucey 2010
  sull'oro come rifugio), quindi il libro compra oro quando l'escalation sale.
- `oracle_utilities` su **XLU**: fedpath +1 (le utility come «quasi-obbligazioni»: una regola del pollice, non un
  parametro pubblicato).

Il lato: **solo long**. Robinhood dà il margine al 50% sui fondi (2x) e il modello del desk non vende un fondo allo
scoperto; il desk non ha qualificato un fondo inverso per questi mercati (SH, TBF: tabelle che il giro non scarica)
e in una fase che misura un segnale non si aggiunge un veicolo nuovo. Una previsione negativa è liquidità, come per
i satelliti; questo lascia metà del segnale sul tavolo, e si scrive. La taglia è la regola di tutti i libri,
`F/10 × 0,50 × m / vol`, con il tetto **2x** (il margine del fondo), **1x** prima di una chiusura, stop giornaliero
−15%, banda d'inerzia 10%, lotto una quota, `m` dell'allocatore come per ogni libro (oggi 0,5 su GLD per
l'interruttore della bolla). Con TLT al 15% di volatilità una previsione di 10 vale 1,7x: la leva è alta per un
fondo e questi libri staranno spesso al tetto - è la richiesta di «speculativo».

### C. Il prior

**Nessun vantaggio**, e con più forza che nel capitolo 10. I mercati Kalshi sulla Fed sono fra i più liquidi del
listino, ma i future sui fed funds (CME) e i Treasury prezzano la stessa informazione in secondi: la variazione
settimanale del percorso letto su Kalshi è, al meglio, il movimento dei Treasury della settimana scorsa letto da
un'altra parte - e allora il libro sui bond compra dopo un rialzo di una settimana, un momentum a sette giorni che
su Treasury ed S&P non è un premio documentato (il trend documentato è su uno-dodici mesi: Hurst, Ooi e Pedersen
2017). La recessione è un mercato sottile che si muove di un punto al mese: la banda morta lo terrà quasi sempre a
zero. Sull'oro la de-escalation può avere anche il segno opposto a quello dichiarato (meno guerra, meno
inflazione, tassi più bassi, oro su). Tutto questo è il motivo per cui i libri sono candidati speculativi e non
toccano una regola del nucleo.

### D. Il filtro e la misura, dichiarati prima

Si misura **una volta**, con `scripts/research_kalshi_books.py` - lo stesso codice del motore
(`oracle.forecast_on` con la specifica dell'indice, `Book`-style sizing) -, su: le candele scaricate il 15
ottobre (che diventano l'archivio, come nel capitolo 10 H: una lettura viva vince sulla candela) e le serie dei
fondi dal branch dati (chiusure aggiustate, le stesse dei libri satellite). Per ogni libro e seduta D: la
previsione del giorno D con le maniche e i segni del punto B, la taglia della regola con `m` dell'interruttore
della bolla sul veicolo, posizione presa alla chiusura di D sul rendimento di D+1; costi del veicolo (mezzo spread
1-2 pb per lato, finanziamento al 5,25% sulla parte a margine). La finestra è quella in cui l'indice esiste:
fedpath dal 18 giugno 2026 (vedi A), recession dal 9 gennaio (il riferimento è una settimana dopo il primo giorno),
deescalation dal 18 marzo; un libro con due maniche è misurato da quando almeno una ha un valore.

Il filtro è quello del capitolo 8 A: un libro con Sharpe netto **negativo** sulla storia disponibile non parte;
con Sharpe ≥ 0 parte come candidato speculativo. Con tre-nove mesi di dati l'errore standard dello Sharpe è 1-2: il
filtro distingue solo i segni, e si dice. I vicini si mostrano e non si scelgono: orizzonte 60 e 180 giorni per
fedpath, 180 e 730 per recession, scala ×½ e ×2 per ogni manica. Si scrive la media di |F| per manica. Che cosa li
ferma: come l'Oracle (capitolo 10 D): mai promossi dal ciclo di vita, ritirati a 24 mesi con Sharpe realizzato
sotto −0,5, il proprietario li rivede a sei mesi; la scala si rimisura a 60 letture vive per manica e si tocca solo
se fuori di un fattore due.

### E. Risultati (15 ottobre, 23:50 UTC - scritti dopo la misura; rifatti alle 02:00 UTC del 16 dopo la revisione G)

Lo scarico «con i mercati regolati chiesti per nome» non ha allungato la storia della Fed: l'API non elenca più
le riunioni di gennaio-luglio 2026 nemmeno con `status=settled` (sessanta mercati, dodici riunioni da settembre
2026 in avanti, come al primo scarico). La finestra di fedpath resta quindi dal 18 giugno (82 sedute, con una o
due riunioni nei 90 giorni; e fino al 29 luglio l'indice non vede la riunione del 29 luglio, che l'API ha
dimenticato: 29 sedute su 82 su un indice incompleto); recession dal 2 gennaio (197 sedute); deescalation dal 13
gennaio. Misura: `scripts/research_kalshi_books.py` sullo scarico delle 23:40 UTC, l'archivio vivo del branch
dati (537 record, che vince sulle candele) e le serie dei fondi fino al 14 ottobre; il rendimento di D+1 è quello
del 14 per l'ultima seduta. I numeri qui sotto sono quelli del codice corretto dalla revisione (G); la prima
misura, prima della correzione, dava gli stessi segni (equities +1,72, utilities +1,69, bonds −1,79, gold −1,66).

**Prima la scala, con la regola dichiarata al punto B.** Con le scale dichiarate (1 per punto base, 2 per punto)
la media di |F| è **3,90** per fedpath (61 sedute su 82 fuori dalla banda morta) e **4,72** per recession: entrambe
fuori dal 10 di Carver per più di un fattore due. Come scritto prima di misurare, la scala si tocca: fedpath
**2,5 per punto base**, recession **4 per punto** (numeri tondi; medie di |F| 8,68 e 8,55 dopo), nel codice
(`FORECAST_PER_BP`, `RECESSION_PER_POINT`) e qui. È la normalizzazione del segnale, ma non è neutra sul risultato
di un libro solo long con un tetto: una scala più alta sbatte più spesso sul 2x (utilities da +2,53 a +1,69,
bonds da −1,65 a −1,53, equities da +1,65 a +1,60, gold da −1,92 a −1,66); i verdetti del filtro sono gli stessi
con le due scale, e il riesame a 60 letture vive userà la stessa regola.

**I quattro libri, con le scale finali** (taglia continua, netto dei costi; a quote intere su 10 000 $; lo
Sharpe della manica da sola, con il segno del libro; i vicini, anch'essi alle scale finali):

| Libro | Sedute | Sharpe (es) | anno, vol | a quote intere | manica da sola | vicini |
|---|---|---|---|---|---|---|
| `oracle_bonds` TLT | 197 (2 gen - 14 ott) | **−1,53 (±1,7)** | −19%, 12% | −1 036 $ (−10,4%), 66 op. | fedpath −2,71; recession −1,91 | fedpath h60 −1,45, h180 −1,65, ×½ −1,73, ×2 −1,60; recession h180 −0,80, h730 −2,07, ×½ −1,43, ×2 −1,60 |
| `oracle_equities` SPY | 197 | **+1,60 (±1,7)** | +23%, 15% | +1 766 $ (+17,7%), 63 op. | fedpath +1,60; recession(−) +1,10 | fedpath h60 +1,46, h180 +1,13, ×½ +1,65, ×2 +1,71; recession h180 +1,07, h730 +1,10, ×½ +1,74, ×2 +1,72 |
| `oracle_gold` GLD | 190 (13 gen - 14 ott) | **−1,66 (±1,8)** | −29%, 17% | −2 227 $ (−22,3%), 70 op. | fedpath +1,89; deescalation −1,75 | fedpath h60 −1,61, h180 −1,05, ×½ −1,88, ×2 −1,26; deescalation ×½ −1,43, ×2 −1,20 |
| `oracle_utilities` XLU | 82 (18 giu - 14 ott) | **+1,69 (±2,7)** | +31%, 19% | +866 $ (+8,7%), 22 op. | (una manica) | h60 +2,64, h180 +1,57, ×½ +2,46, ×2 +0,61 |

Nel mercato il 32-44% dei giorni, esposizione media 0,35-0,77x, massima 2x su tutti. Mese per mese: equities
gen +2,1, feb −1,4, mar −1,2, **apr +11,5, mag +8,9**, giu −6,5, lug −0,7, ago +3,7, set −0,5, ott +2,3; utilities
giu −3,5, lug +6,7, ago +0,3, set −2,7, **ott +9,4**; bonds mar −9,9 e poi quasi solo negativi; gold **giu −21,5**
(il picco della de-escalation del 15 giugno letto come escalation nella seconda metà del mese, con l'oro che
scendeva).

**Il filtro, applicato come dichiarato**: `oracle_equities` e `oracle_utilities` partono (Sharpe netto
positivo); `oracle_bonds` e `oracle_gold` **non partono** (negativo). Non si tolgono maniche per far passare un
libro: su GLD la manica fedpath da sola avrebbe +1,89 e il libro com'era registrato −1,66, e resta fuori com'è
registrato; se un giorno si vorrà un libro sull'oro con la sola Fed, sarà una registrazione nuova.

Come leggerlo, prima di crederci: ogni errore standard è grande quanto lo Sharpe; due mesi su dieci fanno il
risultato di equities e un mese su cinque quello di utilities; la stessa manica con lo stesso segno (fedpath +1)
dà −2,7 sui Treasury e +1,6 / +1,9 / +1,7 su azioni, oro e utility nello stesso periodo - in un anno di guerra e
inflazione la rilettura «accomodante» della Fed ha accompagnato le azioni e non i bond lunghi, e 82-197 sedute
non distinguono un regime da un caso. Il prior di nessun vantaggio resta scritto; i due libri che partono sono
candidati speculativi che si giudicano sugli eseguiti veri, e il riesame delle scale è a 60 letture vive per
manica (fedpath intorno a metà gennaio 2027).

**Che cosa non si è fatto, e perché**: nessun libro su rame, semiconduttori, litio, energia pulita, uranio,
agricoltura (A: nessun mercato con storia); nessun lato short (B); nessuna manica sui dati mensili (A). Le scale
dei fondi inversi (SH, TBF) e le stampe mensili restano in `docs/PLAN.md` come candidati da registrare.

### F. Che cosa cambia nelle regole dure e nel motore

«Un libro candidato dichiarato speculativo» (capitolo 10 E) diventa: i libri candidati con `forecast: kalshi`,
ciascuno su una regola scritta qui prima della prima decisione e - da questa fase, perché l'archivio ora ha un
passato - **misurata una volta sull'archivio prima di partire**, con il filtro del capitolo 8 A; mai promossi,
senza backtest nel replay, giudicati sul record live. Il motore: `oracle.py` è un registro di indici (`INDICES`:
`deescalation`, `fedpath`, `recession`, ciascuno con la propria lettura dell'archivio e la propria scala), un
libro legge le maniche con il segno di `sleeves` e ne fa la media; il giro scarica e il backfill riempie ogni
serie che un indice legge (`ALL_SERIES`: `KXFEDDECISION` e `KXRECSSNBER` si aggiungono alle tre dello stretto);
la pagina mostra un blocco per indice con i libri che lo leggono e il segno; l'allerta di attività anomala
(capitolo 9 F) non giudica i mercati delle decisioni del FOMC (`watch.SCHEDULED_SERIES`): una mossa di dieci
punti il giorno della riunione è l'annuncio, non un'impronta. I tredici libri replicabili e il loro backtest non
cambiano (verificato rieseguendo il replay con il codice di main e con questo sullo stesso archivio: identici byte
per byte fuori dall'impronta). L'impronta cambia una volta - non per i due libri nuovi, che ne sono fuori, ma
perché il registro delle maniche Kalshi era finito nell'hash delle costanti del modulo dei libri (trovato in
revisione: ora `report.NOT_REPLAYED` lo tiene fuori, e un indice nuovo non costerà più un ricalcolo) - e il primo
giro ricalcola il backtest con gli stessi numeri.

### G. La revisione prima della messa in linea

Un revisore che non aveva visto scrivere il codice lo ha attaccato con script propri (i lettori sui record della
fixture e su record sintetici contro conti a mano, il desk con i libri satellite e quelli Kalshi sugli stessi
fondi, il giro live, l'esportazione, la pagina impacchettata, la misura rifatta). Corretto: **la scala annuale
della recessione si muoveva da sola** - al cambio d'anno il punto del 2026 cade e l'indice scende di 3-4 punti a
quotazioni ferme, e il 25 aprile 2026 la quotazione del mercato «nel 2027» appare e l'indice sale di 8 punti,
manica +20 per una settimana dentro la storia misurata - e ora la variazione si legge sugli anni quotati in
entrambe le letture con la scala del riferimento rivalutata a oggi (B); il giorno della riunione del FOMC era
escluso dall'orizzonte mentre il testo lo includeva (B); un esito quasi certo con offerta 0,99 e nessuna domanda
a 1,00 avrebbe reso illeggibile la riunione nei giorni prima (B, non ancora visto nell'archivio); la pagina cadeva
sul blocco d'errore che il motore scrive quando l'archivio non si legge (il blocco esiste perché «un blocco a
lato non costa mai la pagina», e la pagina lo faceva cadere); la riga di ogni libro Kalshi diceva «indice di
de-escalation, −2 per punto» anche per i libri su SPY e XLU (ora la riga porta le maniche con il segno); la
motivazione diceva «la media delle maniche (fedpath +, recession −)» anche quando una non aveva valore; il
riepilogo del job di riempimento contava le sole letture della de-escalation; una lista «regolati» fallita costava
alla storia l'intera serie; l'allerta avrebbe giudicato i mercati del FOMC (sopra); l'impronta (sopra). Scritto e
non cambiato: la stessa meccanica della scadenza che passa esiste, più piccola, nella scala della de-escalation del
capitolo 10 (il mercato «entro il 1° dicembre» al 4% che cade sposta l'indice di due punti a quotazioni ferme): è
la regola registrata della fase 14 e si tocca, se si tocca, al riesame di dicembre, scrivendolo. Verificato dal
revisore: le decisioni, gli eseguiti e le marcature dei libri del desk sono identici con e senza i libri Kalshi
sullo stesso desk; ogni libro Kalshi riceve solo le proprie chiavi; un indice che non si legge costa la sola sua
manica; i numeri di E si riproducono.

**In linea** dalle 01:27 UTC del 16 ottobre (PR #25): il primo giro ha ricalcolato il backtest una volta (stessi
numeri, i tre libri Kalshi esclusi), il giro delle 01:48 ha letto 331 mercati (le riunioni del FOMC e i due
mercati sulla recessione in più) e il job di riempimento delle 01:52 ha scritto 3 715 record per 288 giorni
(l'archivio ha 289 giorni: fedpath 121 letture dal 18 giugno, recession 289); la pagina mostra i tre indici con
la lettura del 15 contro quella dell'8 (de-escalation +7,5; percorso della Fed −13,4 su una rilettura
restrittiva di +5,3 pb; recessione 0 dentro la banda morta). La prima decisione dei due libri nuovi con un
riferimento è quella del 16 alle 15:00 di New York.


## 12. Fase 17 (16 ottobre 2026, 09:00 UTC): un analista Claude che legge le notizie, registrato prima della prima lettura

Il proprietario: «Ho chiamate API gratis Claude 200 $ con la University of Southern California: come posso usarle per
il progetto, e aprire e chiudere posizioni». Letto così: un modello di linguaggio che ogni giorno legge le notizie e
dà una direzione, e quattro libri di carta che la comprano con le regole di sempre. Questo capitolo è scritto prima
della prima lettura: nessuna lettura esiste (la chiave non è ancora fra i segreti del repository) e nessun numero
viene guardato prima di aver scritto le regole. Come per Kalshi, i libri sono **speculativi** e lo dicono ovunque.

### A. Che cosa si può fare, e che cosa no

Si può chiamare dal runner l'API di Claude (Messages API), una volta al giorno, con lo strumento di ricerca web
dell'API (`web_search_20250305`: cerca, legge e cita le pagine; 10 $ ogni mille ricerche più i token) e con l'output
strutturato (`output_config.format`, uno schema JSON che la risposta rispetta). Servono **due chiamate**: la prima
cerca e scrive un riassunto dei fatti con le fonti; la seconda, senza strumenti, legge quel riassunto e risponde con
una previsione per mercato. Non una sola, perché l'output strutturato non si combina con le citazioni che la ricerca
produce (l'API risponde 400).

Non si può fare un **backtest**. Un modello addestrato su testi fino a una certa data conosce i prezzi che una
ricostruzione del passato gli chiederebbe di prevedere: è il look-ahead bias che Glasserman e Lin (2023) misurano
sulle previsioni di rendimento ottenute da GPT, forte dentro il periodo di addestramento e assente fuori. Ogni replay
sarebbe contaminato, quindi non se ne fa nessuno: i libri si giudicano sui soli eseguiti dal vivo, come quelli su
Kalshi, e non entrano nel replay, nelle tabelle né nell'impronta.

### B. L'ipotesi, e il prior

L'ipotesi: le notizie del giorno, lette da un modello, inclinano le probabilità della settimana dopo. Lopez-Lira e
Tang (2023) trovano che il giudizio di un modello sui titoli di una società prevede il rendimento del giorno dopo,
con un effetto che si riduce negli anni, nei titoli grandi e con l'attenzione del pubblico. I mercati di questo desk
(il greggio, l'oro, l'S&P 500, i Treasury lunghi) sono fra i più liquidi del mondo e incorporano una notizia in
minuti, e la previsione qui guarda a cinque sedute, non a un giorno: il prior scritto qui è **nessun vantaggio**. Il
valore del libro, se c'è, è misurare quanto vale un analista automatico che legge le stesse notizie di tutti; la
misura chiede mesi, e la lettura di una settimana non dice nulla.

### C. La regola (`engine/desk/analyst.py`, `config/analyst.yaml`: costanti e chiavi con lo stesso nome)

- **Quando.** Una lettura per giorno UTC, nei giorni lavorativi americani, al primo giro dopo le **13:00 UTC** (le
  9 di New York con l'ora legale, le 8 senza): prima dell'apertura e prima delle decisioni del giorno (14:35 e 15:00
  di New York). Al massimo **3 tentativi** al giorno, a non meno di **30 minuti** l'uno dall'altro.
- **Modello.** `claude-sonnet-5-5` (configurabile). Il modello, i due testi e lo schema fanno un'impronta,
  `prompt_version`, scritta in ogni lettura: cambiarne uno è una regola nuova, da scrivere qui prima.
- **Prima chiamata, le notizie.** Ricerca web, al massimo **6 ricerche**: i fatti delle ultime 48 ore sul greggio
  (Brent, WTI, OPEC+, lo stretto di Hormuz e il conflitto con l'Iran, le scorte americane, la domanda), sull'oro,
  sulle azioni americane (S&P 500) e sui Treasury e la Fed; un paragrafo per mercato, ogni fatto con la data e la
  fonte; nessuna previsione e nessun consiglio.
- **Seconda chiamata, la previsione.** Dal solo riassunto, per ognuno dei quattro mercati (`oil`, `gold`,
  `equities`, `bonds`): una previsione **intera** per le cinque sedute successive sulla scala del desk, da −20 a
  +20, dove +10 è una convinzione normale al rialzo, −10 al ribasso, 0 nessuna opinione e ±20 il massimo, raro; la
  fiducia (`low`, `medium`, `high`); fino a tre ragioni tratte dal riassunto; il rischio principale per la tesi. Per
  i Treasury, positivo vuol dire prezzi su, cioè rendimenti giù. Una previsione fuori scala è tagliata a ±20 e la
  lettura lo segna (`clamped`); un mercato che manca nella risposta non ha previsione quel giorno.
- **I libri** (`forecast: claude`, una manica per mercato letta con il segno +1):
  `claude_oil` sul future micro WTI (/MCL), long e short, obiettivo di volatilità 0,50, tetto 10x e 3x prima di un
  weekend, freno giornaliero 15% (le regole dell'Oracle); `claude_gold` (GLD), `claude_equities` (SPY) e
  `claude_bonds` (TLT), **solo long**, tetto 2x (il margine di Robinhood su un fondo), obiettivo 0,50, freno 15%.
  La taglia è quella di ogni libro, `previsione / 10 × obiettivo × m / volatilità`, con l'allocatore (oggi
  l'interruttore della bolla dimezza MCL e GLD) e la fascia d'inerzia di Carver; la decisione è quella del veicolo,
  alla sua ora.
- **La lettura che una decisione legge.** La più recente presa nel giorno della decisione o nei **3 giorni** prima,
  mai una presa dopo (la decisione del giorno D non legge una lettura del giorno D+1). Più vecchia di 3 giorni:
  previsione 0, il libro si chiude. Nessuna lettura mai presa: nessuna decisione, il libro resta com'è (liquido).
- **Il budget.** Un tetto di spesa di **20 $ al mese** (configurabile). Il costo di ogni chiamata si calcola da ciò
  che l'API riporta (`usage`: token in ingresso, in uscita, in cache, ricerche) ai prezzi pubblicati per il modello
  (Sonnet 5.5, dalla pagina dei prezzi letta il 16 ottobre 2026: 2 $ per milione di token in ingresso, 10 $ in
  uscita, 2,50 $ la scrittura in cache, 0,10 $ la lettura dalla cache; 0,01 $ a ricerca) e si somma nel mese;
  raggiunto il tetto, nessuna chiamata fino al mese dopo.
  Stima prima di ogni lettura: 0,10-0,25 $ a lettura, 3-6 $ al mese: i 200 $ bastano per anni.
- **Che cosa si archivia.** `state/desk/claude-YYYY-MM.jsonl`, una riga per tentativo: giorno, ora, modello,
  `prompt_version`, stato, il riassunto con le fonti (URL e titolo), la previsione, l'uso e il costo di ciascuna
  chiamata, l'errore se c'è. **Mai la chiave.**
- **La scala.** Dopo 60 letture si controlla la normalizzazione di Carver (media di |F| intorno a 10 sui quattro
  mercati); se è fuori di un fattore due, la scala si cambia di un numero tondo, una volta, scrivendolo qui: la
  stessa regola dei capitoli 10 e 11.

### D. Che cosa lo ferma, e che cosa non fa

- Senza la chiave (`ANTHROPIC_API_KEY` fra i segreti del repository) il passo non parte e i quattro libri restano
  liquidi: non c'è nulla da spegnere. Con la chiave, il tetto di spesa; un rifiuto del modello, una risposta
  troncata o uno schema non rispettato non danno previsione quel giorno, e la lettura lo scrive.
- Il riesame del proprietario dopo sei mesi (aprile 2027); mai promossi nel nucleo (la promozione chiede il
  drawdown di un backtest che non esiste); il ritiro del ciclo di vita vale anche per loro (24 mesi con uno Sharpe
  realizzato sotto −0,5).
- Non legge i prezzi del desk né la previsione dei sette segnali (ne sarebbe un'eco); non decide le taglie (le
  regole del desk le decidono); non scrive ordini; non vede le posizioni dei libri. Le sue parole sono una lettura
  delle notizie, non un consiglio, e la pagina lo scrive.

### E. Che cosa cambia nelle regole dure

Una famiglia di previsione in più, `claude`, accanto a `desk` e `kalshi`: candidati speculativi, fuori dal replay,
dalle tabelle e dall'impronta, giudicati sui soli eseguiti dal vivo, mai nel nucleo.

### F. La revisione prima della messa in linea (16 ottobre, prima di ogni lettura)

Un agente che non aveva visto scrivere il codice lo ha attaccato con simulatori suoi (giri veri con un trasporto
finto, il ramo dati reale contro un remoto locale, la pagina in un browser con un `desk.json` avvelenato) e con la
documentazione dell'API alla mano. Le richieste sono quelle che la documentazione chiede (il modello, lo strumento
di ricerca, `output_config.format`, i prezzi, il campo delle ricerche nell'uso: nessuna darebbe un 400 alla prima
chiamata), la chiave non compare in nessuno dei file che quattro giri e un'esportazione scrivono, i diciassette libri
di prima hanno barre, eseguiti, marcature ed equity identici a `main`. Che cosa ha trovato, e che cosa cambia **prima
della prima lettura** (nessuna lettura esiste, quindi nessun risultato è stato guardato):

- **Un tentativo pagato poteva sparire** (il difetto che contava). Il giro scrive il tentativo nell'albero del ramo
  dati; un giro che fallisce dopo la chiamata, o un push che non arriva, fa ripartire quell'albero dal ramo e il
  tentativo va perso: il tetto dei tre tentativi e il budget non lo vedono più, e il giro dopo paga di nuovo (otto
  giri, sedici richieste pagate, nulla in archivio nel simulatore). Ora ogni tentativo va anche in un **registro
  fuori dall'albero** (`OPT_ANALYST_LEDGER`, nello spazio di lavoro del runner) e il giro dopo rimette in archivio
  quello che manca (`restore_from_ledger`). Resta scoperto solo un tentativo non spinto alla fine di un runner, che
  il successore può pagare una volta in più.
- **I limiti di token erano da modello che non pensa.** Claude Sonnet 5.5 pensa da sé (il pensiero adattivo è il suo
  comportamento predefinito, con lo sforzo predefinito `high`) e il pensiero conta in `max_tokens`: 6 000 e 3 000
  avrebbero troncato. Ora 16 000 per ciascuna chiamata (si pagano solo i token generati); i limiti entrano in
  `prompt_version`. Lo sforzo resta quello predefinito del modello.
- **Un riassunto troncato passava per buono**, contro il testo del punto D: ora `max_tokens` (o il contesto
  esaurito) sul riassunto è un tentativo fallito, come già lo era sulla previsione.
- **Le ricerche erano 6 per richiesta, non per lettura**: una ripresa dopo una pausa ne avrebbe concesse altre 6.
  Ora ogni ripresa ha solo quelle rimaste, e senza ricerche rimaste non si riprende.
- **Il tempo non aveva un tetto vero**: ora la lettura intera ha `timeout_s` = 420 secondi, ogni richiesta solo
  quelli rimasti e nessuna parte con meno di 20.
- **Un mercato poteva mancare per sempre**: con la risposta come elenco, un mercato omesso ogni giorno avrebbe
  lasciato il suo libro fermo sulla posizione senza mai scadere. Lo schema è ora un oggetto con i quattro mercati
  **tutti obbligatori** (stessi campi: previsione, fiducia, ragioni, rischio); `prompt_version` cambia di
  conseguenza.
- **«Le ultime 48 ore» di lunedì cominciavano sabato** e lasciavano fuori la seduta di venerdì. Ora il riassunto
  cerca le notizie dalla mattina (13:00 UTC) del giorno lavorativo americano precedente, e almeno 48 ore indietro:
  il lunedì da venerdì mattina, il giorno dopo una festa dal giorno lavorativo prima.
- Minori, corretti: la chiave tolta da un messaggio **prima** di tagliarlo (tagliato prima, un pezzo sarebbe
  rimasto) e anche negli errori archiviati; la chiave data al solo passo del giro nel workflow, non alle azioni di
  terzi che lo precedono; la decisione di un libro senza previsione dice perché con le parole dell'analista; le
  parole lunghe del modello vanno a capo su un telefono.
- Lasciato com'è, scritto: una richiesta che scade può essere fatturata senza che il suo uso ci arrivi (il budget
  non la vede; la console di Anthropic sì); una pausa doppia rimanda il turno intero com'è finora, e la prima che
  capiterà nell'archivio dirà se è giusto (con 6 ricerche è improbabile); la ricerca web deve essere abilitata per
  l'organizzazione che ha i crediti, altrimenti ogni lettura fallisce con l'errore dell'API scritto in pagina.

## 13. Fase 18 (18 ottobre 2026, 02:30 UTC): punti di svolta e regimi di volatilità, registrati prima di misurare

La domanda del proprietario (16-17 ottobre): «rendere il Dinamico più forte». La risposta del 16, dopo la letteratura
recente, era che nessun ritocco del segnale porta lontano (Sepp e Lucic, luglio 2026: tre modi di seguire il trend su
84 future, 1999-2026, 0,47-0,55 di Sharpe netto, come l'indice dei fondi trend; Valeyre, luglio 2026: un solo filtro
fa quanto un insieme complicato, circa 0,2 su un mercato e 1,24 su settanta) e che le leve vere sono più mercati e
gli interessi sulla liquidità (capitolo 8 D). Fra le idee sul segnale il proprietario ne ha scelte due, scritte qui
**prima di calcolare un solo numero sui nostri dati**: i punti di svolta di Goulding, Harvey e Mazzoleni (A) e
l'attenuazione per regime di volatilità di Carver (B). La parte E è un controllo dei dati dello stretto, non una
regola.

### A. Punti di svolta (Goulding, Harvey e Mazzoleni, *Breaking Bad Trends*, Financial Analysts Journal 2023)

Che cosa fa l'articolo: su 43 future (11 indici azionari, 8 obbligazionari, 24 materie prime), con dati mensili dal
1990 al 2022, un trend lento (la media dei 12 rendimenti mensili precedenti) e uno veloce (gli ultimi 2). Quando hanno
lo stesso segno (toro, orso) la posizione è quella del trend a 12 mesi; quando non sono d'accordo (correzione: lento
positivo e veloce negativo; rimbalzo: il contrario) la posizione mescola i due con pesi stimati sulla storia della
stessa attività dopo ciascuno stato. Lo Sharpe passa da 0,64 a 0,80 sul 1990-2022 e da 0,06 a 0,22 dopo il 2009, la
rotazione da 229% a 399% l'anno, e la regola resta meglio del trend statico finché i costi stanno sotto 29 pb.

La regola come la misuro (`scripts/research_phase18.py`):

1. Rendimenti mensili (dall'ultima chiusura di un mese all'ultima del successivo) della serie su cui la manica legge:
   per i libri sul greggio (BNO e /MCL) la serie investibile lunga del WTI, quella da cui la manica dell'asimmetria è
   già letta (il fondo Brent ha sedici anni, pochi per stimare i pesi); per ogni satellite il suo fondo.
2. All'inizio del mese m: lento = media dei 12 rendimenti mensili prima di m, veloce = media dei 2 prima di m. Stato:
   toro (lento ≥ 0 e veloce ≥ 0), orso (tutti e due < 0), correzione (lento ≥ 0, veloce < 0), rimbalzo (lento < 0,
   veloce ≥ 0).
3. Posizione: toro +1, orso −1; in correzione e in rimbalzo w_s = MEDIA[r|s] / (C · MEDIA[r²|s]), limitata a
   [−1, 1], con C = (f_toro · MEDIA[r|toro] − f_orso · MEDIA[r|orso]) / MEDIA[r²|toro o orso] e f la frequenza di
   ciascuno dei due stati fra i mesi toro o orso. Sono le equazioni (8)-(10) dell'articolo scritte come posizioni:
   w_Co = 1 − 2a_Co e w_Re = 2a_Re − 1, sicché limitare a in [0, 1] è limitare w in [−1, 1]; il secondo momento al
   denominatore è il restringimento verso la posizione senza informazione (a = ½, w = 0) che l'articolo descrive. Con
   C ≤ 0 (il trend non ha mai pagato nei mesi toro e orso di quell'attività) w = 0.
4. I pesi si stimano ogni 30 mesi, contati dal primo mese che ha uno stato, su tutti i mesi completi prima, e solo
   con almeno 12 mesi in ciascuno dei quattro stati; prima di allora la posizione è quella statica (correzione +1,
   rimbalzo −1: il segno del trend a 12 mesi).
5. Previsione = 10 × posizione (10 è la previsione media della scala di Carver), ferma per tutto il mese m, dalla
   decisione del primo giorno di borsa di m.
6. Prende il posto della manica `trend` (le quattro EWMAC) in ogni libro che si rigioca; il resto è identico (le altre
   maniche, i pesi, i moltiplicatori, il rischio, l'allocatore).

Vicini riportati, che non decidono: (a) il veloce a 1 mese (appendice D dell'articolo); (b) i libri su BNO con la
serie del fondo; (c) il solo segno del trend a 12 mesi al posto della manica: è la base dell'articolo, e la
differenza fra la regola e questo vicino è l'effetto che l'articolo misura, sui nostri libri; (d) la stima ogni 12 e
ogni 60 mesi.

### B. Attenuazione per regime di volatilità (Carver, *Advanced Futures Trading Strategies*, 2023, strategia 13)

Che cosa fa il libro: la previsione di trend e di carry di ogni strumento si moltiplica per un numero fra 0,5 e 2,
alto quando la volatilità dello strumento è bassa rispetto alla sua storia e basso quando è alta, perché il trend
rende meno nei regimi agitati. Una ricostruzione indipendente (QuantConnect, luglio 2020 - luglio 2023) passa da 0,75
a 0,94 di Sharpe; Goulding e colleghi, sopra, non trovano legame fra la volatilità e la resa del trend.

La regola come la misuro:

1. σ_t: la volatilità del desk (deviazione esponenziale dei rendimenti giornalieri, span 35, annualizzata), senza il
   pavimento del 10% che serve al dimensionamento.
2. σ%_t = 0,3 × (media di σ sugli ultimi 2 500 giorni) + 0,7 × σ_t; V_t = σ%_t / (media di σ% sugli ultimi 2 500
   giorni); medie mobili semplici con almeno 10 osservazioni, come nel codice di Carver (*The state of vol*, 2023).
3. Q_t = la quota dei valori passati di V che stanno sotto V_t (0 per il più basso mai visto, quasi 1 per il più
   alto); M_t = media esponenziale con span 10 di (2 − 1,5 × Q_t).
4. M_t diviso 1,25, il suo valore atteso (con Q uniforme la media di 2 − 1,5Q è 1,25): il metodo di Carver fissa gli
   scalari perché la previsione media resti 10, e dopo l'attenuazione il suo libro dà infatti al carry uno scalare
   nuovo (23); dividere per 1,25 fa lo stesso senza stimare nulla. Intervallo finale 0,4-1,6.
5. Si applica a ogni manica del libro (alle sette, o alle tre dei satelliti), sul valore che il desk calcola, poi di
   nuovo limitato a ±20. Il regime dei libri sul greggio si legge sul WTI lungo (come l'asimmetria), quello di ogni
   satellite sul suo fondo; nei giorni in cui M non esiste ancora, M = 1.

Vicini riportati, che non decidono: (a) M senza la divisione per 1,25 (il moltiplicatore com'è pubblicato); (b) la
sola manica `trend`; (c) i libri su BNO con la volatilità del fondo.

### C. Il prior, scritto prima

- A: il guadagno dell'articolo è contro il segno di un trend a 12 mesi; la manica del desk mescola già quattro
  velocità (da due settimane a un anno) e quella dell'accelerazione legge proprio un trend che rallenta; su un mercato
  solo i pesi si stimano male. Mi aspetto da −0,05 a +0,05 di Sharpe contro la manica attuale, sui tredici libri come
  sui tre del greggio; contro il solo segno a 12 mesi (vicino c) il verso dell'articolo, da +0,05 a +0,15.
- B: Carver lo misura su un centinaio di future; Goulding e colleghi non vedono il legame; sul greggio gli anni calmi
  2023-2025 sono stati quelli in perdita. Mi aspetto da −0,05 a +0,05.
- Nessuna delle due, credo, passa la regola di decisione qui sotto.

### D. La misura e la regola di decisione

- Il replay del desk (`engine.desk.backtest.run_backtest`: costi veri, esecuzione all'apertura dopo la decisione,
  l'allocatore acceso) sui tredici libri che si rigiocano, con e senza la regola, tutto il resto identico; i dati del
  ramo `data` al commit d53517d5 (18 ottobre, 00:53 UTC), il codice di `main` 8f68a215 più lo script.
- Due portafogli a capitale uguale (`report.portfolio_stats`): i tre libri del greggio (prudente, dinamico, spinto) e
  tutti e tredici, ciascuno sulla sua finestra comune, tagliata a metà per le due metà. Si riportano anche i tredici
  libri uno per uno, il dinamico per primo.
- **Una regola entra** (per tutti i libri, perché le maniche sono comuni) se lo Sharpe dei tredici libri sale di
  almeno 0,05 in entrambe le metà, se quello dei tre del greggio non scende di più di 0,05 in nessuna delle due, e se
  a costi doppi il guadagno sui tredici resta positivo sull'intera finestra. Altrimenti non entra. I vicini si
  riportano e non cambiano il verdetto. Se entrano tutte e due, si misurano insieme, ed entrano insieme solo se così
  fanno meglio di ciascuna da sola.
- In questa fase nessuna regola cambia i libri dal vivo: una che passa si propone al proprietario e si accende in un
  passo a parte, con i test e una revisione.

### E. Che cosa misura la regola dello stretto (controllo dei dati, 17-18 ottobre)

La regola dello stretto (capitolo 9, D4 ii: transiti sotto il 10% del normale, tetti del fine settimana dimezzati)
legge `n_tanker` di IMF PortWatch, cioè le petroliere che attraversano lo stretto **con il transponder AIS acceso**:
in ottobre 0,4 al giorno contro 48 nella media del 2025 (tutte le navi: 3,5 contro 85), da cui l'1,15% del normale
che ogni decisione cita. Gli altri conteggi dicono altro:

- Kpler (26 giugno): 895 attraversamenti fra il 1° marzo e il 19 maggio, circa 11 al giorno, il 40,6% con l'AIS
  spento e il 53% sul corridoio definito dall'Iran; interferenze sul GNSS fino a 3 000 navi in un giorno.
- Lloyd's List: circa 12 al giorno a fine agosto (navi da carico sopra le 10 000 tonnellate), con dati «incompleti per
  il ritardo nell'identificare i transiti al buio»; Drewry: a fine agosto la maggioranza degli attraversamenti era «al
  buio» o di rotta ignota (Al Jazeera, 3 settembre).
- UKMTO: 254 transiti nei sette giorni al 16 ottobre, soprattutto rinfusiere; CENTCOM: 400 navi e 100 milioni di
  barili nella stessa settimana; Kpler: 11,6 milioni di barili al giorno nella settimana al 13 ottobre, il 68% dei 17,1
  di prima della guerra (Al Jazeera, 17 ottobre). Gli Stati Uniti hanno motivi per dire di più, l'Iran per dire di
  meno.

Conclusione: l'1% è la quota delle petroliere **visibili**, non delle petroliere. Con gli altri conteggi lo stretto
porta fra il 10 e il 40% del traffico normale di navi e forse più di metà del petrolio: la soglia del 10% non è su ciò
che la serie misura. Oggi il tetto dimezzato non tocca nessun libro (le esposizioni più alte sono lo 0,40x del
dinamico contro un tetto di 0,75x e un lotto di /MCL dello spinto contro 1,5x). Il mercato Kalshi «lo stretto torna
normale», una delle tre componenti dell'indice di de-escalation (capitolo 10), si risolve sulla stessa serie («la
media a 7 giorni dei transiti riportati da IMF PortWatch sopra 60»): è una scommessa sul conteggio AIS, non sul
traffico. Che cosa cambia: le parole (ogni decisione dirà «transiti di petroliere visibili sull'AIS»), il commento
dell'adattatore e `docs/DATA_SOURCES.md`; la regola resta com'è stata dichiarata finché il proprietario non decide se
tenerla, toglierla o cambiarle la misura.

### F. Risultati (18 ottobre, 02:50 UTC: scritti dopo la misura)

La registrazione qui sopra è il commit 231d504d, spinto alle 02:31 UTC prima che lo script girasse (il titolo diceva
03:30 per errore: l'ora è corretta qui, il resto del testo è quello del commit). Lo script è
`scripts/research_phase18.py`. Prima dei numeri, tre controlli: tagliare i dati a sei date a caso su BNO, WTI, GLD e
SPY non cambia nessun valore precedente delle due regole (nessuno sguardo al futuro); il replay del desk com'è
riproduce il backtest pubblicato (0,78 i tre del greggio, 1,23 i tredici, 0,80 il Dinamico); la manica nuova ha la
scala della vecchia (media di |F| fra 8,3 e 10,0 contro 7,3-10,8 delle quattro EWMAC, correlazione fra le due
0,48-0,69) e l'attenuazione divisa per 1,25 vale in media fra 0,91 e 1,08 secondo il veicolo.

Sharpe; i tre del greggio dall'11 agosto 2011 al 16 ottobre 2026 (metà al 14 marzo 2019), i tredici dal 31 gennaio
2013 (metà al 9 dicembre 2019):

| Variante | 3 greggio | 1ª metà | 2ª metà | 13 libri | 1ª metà | 2ª metà | Dinamico |
|---|---|---|---|---|---|---|---|
| Il desk com'è | 0,78 | 0,78 | 0,78 | 1,23 | 1,02 | 1,42 | 0,80 |
| **A**, punti di svolta come registrati | 0,83 | 0,89 | 0,78 | 1,27 | 1,16 | **1,38** | 0,82 |
| A.a, il veloce a 1 mese | 0,82 | 0,84 | 0,81 | 1,27 | 1,12 | 1,41 | 0,84 |
| A.b, BNO sulla serie del fondo | 0,83 | 0,91 | 0,77 | 1,27 | 1,17 | 1,36 | 0,83 |
| A.c, solo il segno del trend a 12 mesi | 0,82 | 0,86 | 0,79 | 1,27 | 1,15 | 1,39 | 0,84 |
| A.d, stima ogni 12 / ogni 60 mesi | 0,83 / 0,83 | 0,88 / 0,88 | 0,78 / 0,78 | 1,27 / 1,27 | 1,16 / 1,15 | 1,37 / 1,37 | 0,82 / 0,82 |
| **B**, attenuazione come registrata | 0,76 | 0,77 | 0,74 | **1,17** | 1,02 | **1,30** | 0,83 |
| B.a, senza la divisione per 1,25 | 0,79 | 0,80 | 0,78 | 1,20 | 1,05 | 1,34 | 0,87 |
| B.b, solo la manica trend | 0,79 | 0,81 | 0,77 | 1,23 | 1,06 | 1,39 | 0,83 |
| B.c, BNO con la volatilità del fondo | 0,72 | 0,72 | 0,73 | 1,14 | 0,97 | 1,28 | 0,73 |
| A / B / il desk, a costi doppi | 0,79 / 0,71 / 0,73 | | | 1,23 / 1,11 / 1,17 | | | 0,81 / 0,81 / 0,76 |

I verdetti, con la regola del punto D:

- **A non entra.** Sui tredici libri +0,04 sull'intera finestra, ma tutto nella prima metà (+0,14): nella seconda
  −0,04, sotto il +0,05 richiesto. I tre del greggio +0,05 (+0,11 e +0,00); a costi doppi i tredici +0,06. E il
  meccanismo dell'articolo non c'entra: il solo segno del trend a 12 mesi (il vicino c, la base dell'articolo) fa lo
  stesso, e la differenza fra la regola e la sua base, che nell'articolo vale +0,16 su 43 future, qui è +0,01 sul
  greggio, −0,00 sui tredici e −0,02 sul Dinamico. Quello che sposta i numeri è sostituire le quattro EWMAC giornaliere
  con un trend mensile più lento, e quel guadagno sta nel 2011-2019: nel 2019-2026 è nullo sul greggio e negativo sui
  tredici. È un'osservazione fatta dopo aver guardato, non una regola; se mai la si volesse, andrebbe registrata da sé.
- **B non entra.** I tredici libri −0,06 (+0,00 e −0,12), il greggio −0,02, a costi doppi −0,06; la perdita massima
  dei tredici passa dal 10% al 12%. Il Dinamico da solo sale (0,83, e 0,87 senza la divisione per 1,25), ma è un libro
  su tredici e i tre del greggio insieme scendono: guardato dopo, non conta. Leggere il regime sul fondo (B.c)
  peggiora tutto; la sola manica trend (B.b) non cambia nulla.
- La perdita massima del Dinamico peggiora con tutte e due (−44% con A, −49% con B, contro −41%).
- Il prior era scritto così, ed è andata così: nessuna delle due passa. Nessun libro cambia e l'impronta del
  backtest resta b66ecbb82b75. Le leve che restano sono quelle del 16 ottobre: più mercati nello stesso conto e gli
  interessi sulla liquidità.

## 14. Fase 19 (22 ottobre 2026, 19:19 UTC): un conto da 1 000 $ che punta a 10x in tre mesi, registrato prima di misurare

**La richiesta del proprietario** (22 ottobre): «un altro conto che parte da 1 000 dollari e ha come obiettivo circa
10 000 in 3 mesi, super aggressivo, super con leva, che si può riattivare con un click», con cose che si possono
fare su Robinhood, la direzione del Dinamico più Kalshi, e le regole di rischio del progetto sospese per lui solo.

**La risposta onesta, data prima di scrivere una riga** (e misurata sul replay del Dinamico, approssimata, prima di
questo capitolo: i rendimenti giornalieri del dinamico moltiplicati per k, la leva mai sopra 10x, 751 finestre di
tre mesi dal 2011): a 8 volte il rischio di oggi il 10x arriva nel 4,4% delle finestre, tutte in cinque episodi di
grandi trend (autunno 2014, 2015, fine 2020, fine 2021, inizio 2026); il risultato mediano è −24% e il 29% delle
finestre perde più di metà; a 20 volte il 10x scende all'1,6%, metà delle finestre perde più di metà e una su dieci
si azzera. Nessuna strategia fa 10x in tre mesi in modo affidabile: a questo rischio decide la fortuna del periodo.

**Perché le opzioni comprate su USO.** Con 1 000 $ Robinhood non presta (il margine chiede almeno 2 000 $, regola
FINRA); un /MCL vale dieci volte il conto e il suo margine (7-10% del contratto) se lo prende quasi tutto, quindi una
mossa contraria del 3-7% chiude il conto; un ETF a leva 2x (UCO, SCO) arriva al massimo a due volte il petrolio, e a
quella leva il 10x in tre mesi nella storia quasi non si vede (lo 0,1% delle finestre già a 4 volte il rischio di
oggi); i contratti a evento chiederebbero un simulatore che il desk non ha. Un'opzione comprata ha la perdita massima nel premio e un guadagno convesso: è l'unico strumento con cui
1 000 $ possono fare 10x. USO ha opzioni con scadenze settimanali e strike di un dollaro, il desk ne scarica già le
quotazioni reali Cboe ogni ora, e il libro si replica su Robinhood e su Alpaca (le opzioni comprate sono il livello
2; sul conto di prova di Alpaca sono attive di serie e l'API è gratuita).

### A. La regola (`engine/desk/rocket.py`, `rocket_book` in `config/books.yaml`: costanti e chiavi con lo stesso nome)

1. **Il conto.** 1 000 $ a ogni vita. A 50 $ o meno (il 5%, come ogni conto) il libro è morto e si ferma; un
   pulsante (il workflow `rocket-reset`) lo fa ripartire da 1 000 $ in una vita nuova, e le vite di prima restano.
2. **Quando.** Una volta per seduta americana, al primo giro dopo le 15:00 di New York con una catena di USO meno
   vecchia di 45 minuti (come il libro delle opzioni).
3. **Il segnale, letto per il giorno D.** Il Dinamico: la previsione combinata del fondo Brent (BNO) come la legge il
   dinamico (le sette maniche, i pesi del desk); direzione long se ≥ +5, short se ≤ −5, nessuna altrimenti (5 è metà
   della convinzione normale, la soglia del libro delle opzioni). Kalshi: la variazione settimanale dell'indice di
   de-escalation come la legge l'oracle (la lettura del giorno UTC D contro quella di 7-10 giorni prima,
   `oracle.forecast_for`): è d'accordo con un long quando la variazione è sotto zero (le probabilità di distensione
   scendono: il petrolio sale), con uno short quando è sopra; zero o illeggibile, nessun accordo. La sua ampiezza non
   conta (la fascia morta dell'oracle è per la sua manica). **Una scommessa nuova si apre solo con una direzione del
   Dinamico e l'accordo di Kalshi.**
4. **La scommessa.** Long: call su USO; short: put su USO. La scadenza quotata più vicina a 30 giorni fra 21 e 45.
   Lo strike: fra le opzioni quotate non più larghe del 25% del loro prezzo medio, quella con il delta più vicino a
   0,30 il cui costo sta in metà del conto; se a 0,30 non ci sta, la successiva più lontana dal prezzo, fino a delta
   0,10; nessuna ci sta, nessuna scommessa (la pagina dice perché). I contratti: quanti ne stanno in metà del conto al
   prezzo di esecuzione (l'ask meno un quarto dello spread) più 0,05 $ di commissioni a contratto, e mai un nozionale
   in delta (delta × 100 × contratti × prezzo) sopra 10 volte il conto all'apertura (la regola dei 10x di ogni
   conto). Una posizione alla volta.
5. **Tenere e uscire, a ogni decisione.** Si vende (al bid più un quarto dello spread) quando la direzione del
   Dinamico non è più quella della scommessa (|previsione| sotto 5 o segno opposto): Kalshi decide solo l'entrata.
   Con 7 giorni o meno alla scadenza si vende e, se le condizioni dell'entrata ci sono, si compra la successiva con
   la regola 4 lo stesso giorno. Nessun obiettivo di guadagno: un 10x viene solo da un vincente lasciato correre.
   Un'opzione non venduta entro la scadenza (nessuna quotazione fresca) si regola al valore intrinseco sulla
   chiusura ufficiale, come nel libro delle opzioni.

### B. Che cosa è sospeso per questo libro, e che cosa no

Sospesi, per decisione del proprietario del 22 ottobre: il 5% di perdita massima per struttura e le due strutture
del libro delle opzioni (qui metà del conto per scommessa, una alla volta) e, nello spirito, il tetto di rischio
dell'obiettivo di volatilità al 50% (questo libro non ne ha, e il suo rischio è molto sopra quello di qualunque
altro). Restano: quotazioni vere di meno di 45 minuti, mai un prezzo che il flusso non ha quotato, il nozionale in
delta entro 10 volte il conto, il pavimento del 5%, le esecuzioni dal lato sfavorevole dello spread. È fuori dai
totali del desk e da quelli dei candidati, in un gruppo suo («Lottery»), non viene mai promosso e non ha righe nelle
tabelle del backtest.

### C. La misura (una volta, su un modello: decide nulla)

`scripts/research_rocket.py`: il modello del replay del libro delle opzioni (Black-Scholes a tasso zero sull'OVX;
per le put lo smile della catena dell'8 ottobre 2026, per le call la volatilità at the money, perché uno smile delle
call non c'è; spread quotato = 6% del prezzo medio + 2 pb del sottostante; esecuzione a un quarto dello spread dal
lato sfavorevole; contratti frazionari; 21 sedute alla scadenza all'entrata, si rinnova a 5), sul prezzo rettificato
di USO e la previsione combinata di BNO. Due periodi: (a) dal 2011 con il solo Dinamico (prima del 18 marzo 2026
l'indice Kalshi non esiste: è il vicino «senza Kalshi»), (b) dal 18 marzo al 16 ottobre 2026 con l'accordo di Kalshi
letto sull'archivio (la regola registrata, sette mesi). Per ciascuno, finestre di 63 sedute (tre mesi) che partono
da 1 000 $ ogni 5 sedute: la quota che arriva a 10x, a 2x, che perde metà, che muore, e la mediana. Vicini: metà del
conto sostituita da un quarto e dal tutto; delta 0,15 e 0,50; costi doppi. **Nessun numero cambia la regola**: il
libro è chiesto come una lotteria, e i suoi numeri vanno nella sua descrizione e sulla sua pagina, con il segno ≈.

### D. Il prior, scritto prima

Un 10x in tre mesi in pochi per cento delle finestre, solo dentro grandi trend; il risultato mediano di tre mesi una
perdita fra il 40 e il 70%; la morte (sotto il 5%) fra il 20 e il 40% delle finestre; il filtro di Kalshi dimezza le
scommesse e in sette mesi non cambia le probabilità in modo misurabile. In media le opzioni sul petrolio comprate
perdono (capitolo 4): questo libro paga il premio per la possibilità di un 10x, e lo paga quasi sempre.

### E. Risultati (22 ottobre, 19:40 UTC: scritti dopo la misura, che è un modello ≈)

La registrazione qui sopra è il commit 55ccdfce, spinto alle 19:19 UTC prima che lo script girasse. I dati: il ramo
`data` al commit d53517d5, 4 118 sedute di USO, OVX e previsione di BNO fino al 15 ottobre, 147 letture Kalshi.
Finestre di tre mesi da 1 000 $:

| Variante | Periodo | Finestre | 10x | 2x | perde metà | muore | mediana | la migliore |
|---|---|---|---|---|---|---|---|---|
| **La regola** (metà del conto, delta 0,30) | 2011-2026, solo Dinamico | 811 | **1,8%** | 10,5% | 50,1% | 0% | **0,50** | 37x |
| | 2026, con Kalshi | 17 | 0% | 0% | 64,7% | 0% | 0,33 | 1,3x |
| | 2026, solo Dinamico | 17 | 0% | 0% | 64,7% | 0% | 0,31 | 1,3x |
| Un quarto del conto | 2011-2026 | 811 | 0,2% | 7,8% | 10,2% | 0% | 0,73 | 11x |
| Tutto il conto | 2011-2026 | 811 | 2,2% | 9,5% | 65,7% | 18,1% | 0,34 | 65x |
| | 2026, con Kalshi | 17 | 0% | 0% | 88,2% | 70,6% | 0,00 | 0,8x |
| Delta 0,15 | 2011-2026 | 811 | 2,2% | 8,4% | 63,9% | 0,4% | 0,38 | 52x |
| Delta 0,50 | 2011-2026 | 811 | 1,0% | 12,0% | 31,7% | 0% | 0,62 | 18x |
| Costi doppi | 2011-2026 | 811 | 1,8% | 9,9% | 53,6% | 0% | 0,47 | 34x |

Come leggerlo:

- **Il 10x arriva in meno di due finestre su cento, e solo nei crolli**: tutte le finestre che ci arrivano partono
  fra settembre e novembre 2014, a fine maggio 2015 e fra gennaio e febbraio 2020. Sono put comprate in tre crolli
  del petrolio: la convessità paga quando il prezzo cade di colpo e la volatilità implicita, già alta, non basta a
  prezzarlo.
- **Il risultato tipico è la metà del conto in tre mesi**, e metà delle finestre perde più di metà. Morire sotto il 5%
  in tre mesi è raro con metà del conto per scommessa (il prior diceva 20-40%: sbagliato, una perdita dimezza il conto
  e non lo azzera), ma tutto il conto per scommessa muore in una finestra su cinque.
- **Nel 2026 nessuna delle 17 finestre arriva a 2x**: dopo il 18 marzo il petrolio è salito e sceso di decine di dollari
  con una volatilità implicita intorno al 50%, e le opzioni costavano quanto il movimento che poi c'è stato. Kalshi
  non ha dimezzato le scommesse (3,4 per finestra con e senza): ritarda l'entrata di qualche giorno, non la toglie.
- I vicini dicono la stessa cosa: più rischio per scommessa alza di poco la probabilità del 10x e molto quella di
  perdere tutto; un quarto del conto tiene la mediana a 0,73 e il 10x quasi sparisce.
- **Il modello compra contratti frazionari, il libro vero contratti interi.** Sulla catena vera di USO del 16 ottobre
  (fondo a 161,78, volatilità implicita a 30 giorni 46%, scadenza scelta il 13 novembre, 28 giorni) con 1 000 $ la
  regola compra una call 175 a delta 0,31 per 418,80 $ con le commissioni, o una put 152,5 a delta 0,30 per 392,55 $:
  il 39-42% del conto, non la metà. Con 600 $ scende a delta 0,20-0,23 (249-290 $), con 400 $ a delta 0,16-0,17
  (195-197 $). Il contratto più economico che la regola ammette quel giorno (la put 138 quotata 1,07-1,23, delta 0,11)
  costa 119,05 $: sotto i 238 $ nessuna opzione sta in metà del conto e il libro non può più scommettere, anche se non
  è "morto" per la regola del 5%. La sua pagina lo dice (`monitor.stuck`) e una vita nuova si apre con il pulsante. Il
  pavimento pratico è quindi intorno a un quarto del capitale, non al 5%, e lo 0% di morti del modello va letto così:
  con contratti interi il conto non si azzera, si ferma prima.

Il verdetto è quello dichiarato: la misura non decide nulla. Il libro parte com'è registrato, e la sua pagina dice
queste probabilità con il segno ≈.

### F. Più probabilità di 10x? Registrazione del 22 ottobre (20:45 UTC), prima di misurare

**La richiesta del proprietario** (22 ottobre, 13:26 e 13:28 Los Angeles): «study improvements to make it work better,
more chances», e «focus e leva anche su mercati stile Kalshi o Polymarket».

**Il tetto, prima di ogni variante.** In un gioco EQUO (atteso zero) la probabilità di portare una posta a N volte è
al più 1/N, con qualsiasi strategia (il patrimonio è una martingala e il teorema di arresto la inchioda: Dubins e
Savage, *How to Gamble If You Must*, 1965; esercizi 8 e 32 della dispensa di Hannig sul casinò: timido o audace,
P = x/a); in un gioco SFAVOREVOLE è meno, e la strategia migliore è quella audace, bold play (teorema 3 della stessa
dispensa; da metà dell'obiettivo si arriva con probabilità p, la probabilità di vincere una mano). Da 1 000 a 10 000:
**al più il 10% senza vantaggio**, e la mediana a 0,50x in tre mesi di 14 E dice che la regola è sfavorevole dopo i
costi. Solo un vantaggio NETTO positivo (il Dinamico meno il premio delle opzioni e lo spread) alza quel tetto: le
varianti che aggiungono rischio spostano probabilità dal centro alle code, non oltre il 10%; solo quelle che cambiano
il vantaggio (comprare quando le opzioni costano poco, scommettere sui segnali forti) possono.

**Le varianti registrate, ciascuna con l'autore e il parametro pubblicato** (una misura per variante; i vicini si
mostrano, non si scelgono):

1. **Gioco audace e traguardo** (Dubins e Savage 1965): tutto il conto per scommessa (già in 14 E come vicino) e
   **fermarsi al tocco del 10x**: per ogni variante si misura anche P(tocco del 10x durante la finestra), che è la
   probabilità di chi si ferma al traguardo, con il "bank at 10x" come regola (al tocco la vita finisce).
2. **Comprare solo quando le opzioni costano poco** (Goyal e Saretto, *Cross-section of option returns and
   volatility*, JFE 2009: le opzioni con volatilità implicita sotto quella realizzata rendono di più, quelle sopra
   perdono): nuova scommessa solo se OVX − volatilità realizzata del WTI (la serie del desk, EWMA di Carver) è sotto
   zero; vicini +5 e −5 punti.
3. **Soglia del segnale**: ±10, la "convinzione normale" della scala di Carver, al posto di ±5; vicino ±15.
4. **Opzioni più lunghe** (Dao, Nguyen, Deremble, Lempérière, Bouchaud e Potters, *Tail protection for long
   investors: trend convexity at work*, 2016: il trend following ha il profilo di uno straddle con scadenza pari
   all'orizzonte del trend; le maniche del Dinamico vanno da 8/32 a 64/256 giorni): 42 sedute (~60 giorni) con
   rinnovo a 10; vicino 63 sedute (~90 giorni) con rinnovo a 15.
5. **Rinnovo sul vincente** (rolling up; la piramide delle regole dei Turtle, Faith 2007): quando il delta
   dell'opzione supera 0,70 (in the money, regola pratica) si vende e si ricompra a delta 0,30 nella stessa
   direzione con la regola della posta, senza chiedere l'accordo di Kalshi (è una continuazione); vicino 0,80.
6. **La previsione del WTI al posto del Brent**: USO segue il WTI; la previsione combinata della serie MCL (quella
   del libro spinto) al posto di BNO. Stesse sette maniche, stessi pesi.
7. **Contratti interi al prezzo di oggi**: il modello di 14 E compra frazioni di contratto; ogni variante si misura
   anche con contratti interi, il prezzo di USO riportato a quello di oggi (162,66 $) all'inizio di ogni finestra,
   a lettera meno un quarto dello spread per 100 azioni più 0,05 $ a contratto.

**I mercati a eventi (Kalshi; Polymarket).** I fatti, prima della misura:

- Un contratto a 10 centesimi paga 10x se si avvera: la convessità sta nel prezzo, non c'è margine (Kalshi e
  Robinhood chiedono tutto il collaterale). Kalshi è legale negli USA (CFTC) e Robinhood la offre; **Polymarket
  non è aperto agli utenti americani** (29 aprile 2026: chiede alla CFTC di riaprire; «Polymarket US» su QCEX,
  dicembre 2025, solo sport e lista d'attesa): il proprietario non può replicarlo, e i suoi mercati sul petrolio
  non aggiungono nulla a quelli di Kalshi. Per il libro resta Kalshi.
- I costi: commissione taker di Kalshi = round up(0,07 × C × P × (1 − P)): a 10 ¢ sono 0,63 ¢ a contratto, il 6,3%
  del premio, a 50 ¢ l'1,75 ¢, il 3,5%; lo spread sui mercati liquidi 1-3 ¢, cioè il **10-30% del premio a 10 ¢**;
  su Robinhood dal 1° giugno 2026 la commissione è il 10% di k × P × (1 − P) × C (5% con Gold) più 1 ¢ a contratto
  per lato sui contratti di Kalshi. Le opzioni su USO costano il 5% dello spread più 0,05 $: **un contratto a
  evento a 10 ¢ costa da due a sei volte un'opzione, per dollaro di premio.**
- La distorsione favorito-sfavorito: su circa 300 000 contratti scambiati su Kalshi (Whelan, gennaio 2026) i
  contratti sotto i 10 ¢ si avverano meno spesso di quanto serve per pareggiare, con perdite medie grandi, peggio
  per chi prende l'offerta; i prezzi più alti vanno meglio. È la stessa distorsione delle scommesse ippiche (Thaler
  e Ziemba 1988). **Comprare i contratti a 10 ¢ "per fare 10x" è il lato sistematicamente perdente**, più di quanto
  lo siano le opzioni comprate.
- L'unica tesi con un vantaggio misurato è quella del Rocket: la direzione del Dinamico sul prezzo del petrolio. Le
  scale di prezzo di Kalshi (WTI e Brent per giorno, settimana e mese) sono contratti binari sul petrolio: «WTI
  sopra X il giorno D». Comprarli nella direzione del Dinamico è l'unica variante a eventi con una ragione.

**La misura registrata sui contratti a eventi** (sui candlestick giornalieri scaricati dal runner, `research-data`
`kalshi-history`: KXBRENTW, KXWTIW, KXBRENTMON, KXWTIMONTHLY dal 1° gennaio 2026; poi KXBRENTD e KXWTI dal 1°
luglio, le serie giornaliere): per ogni mercato risolto e ogni giorno di lettura con il Dinamico che ha una
direzione (±5), comprare il contratto nella direzione del segnale (YES di «sopra X» per un long, NO per uno short,
alla lettera più la commissione taker) e tenerlo alla risoluzione. Si riportano, per fasce di prezzo (≤ 15 ¢,
15-40 ¢, 40-70 ¢, > 70 ¢): il numero di scommesse, la quota che si avvera, il rendimento medio per dollaro, il
moltiplicatore medio e, da 1 000 $ con metà del conto per scommessa, quante finestre di tre mesi arrivano a 10x.
Il confronto: lo stesso senza segnale (ogni giorno, entrambe le direzioni), che misura la distorsione
favorito-sfavorito su questi stessi dati.

**La regola di decisione, dichiarata.** Una variante delle opzioni entra nella regola del Rocket (prima della sua
prima decisione, come registrazione 14 H) solo se (a) alza P(tocco del 10x) in ENTRAMBE le metà della storia
(2011-2018 e 2019-2026), (b) non alza di oltre 10 punti la quota di finestre che perde metà o più sull'intera
storia, (c) è il parametro pubblicato, non il vicino migliore. Un lato Kalshi del Rocket si registra solo se le
scommesse nella direzione del Dinamico sui contratti fra 15 e 70 ¢ hanno un rendimento atteso positivo dopo
commissioni e spread su almeno 100 scommesse risolte; altrimenti non si fa, e si scrive perché.

**Il prior, scritto prima.** Il gioco audace e il traguardo al tocco alzano P(10x) di circa la metà del suo valore,
con un morto ogni cinque vite; il filtro di volatilità toglie un terzo delle scommesse e muove poco; la soglia ±10
abbassa P(10x) (meno scommesse); le opzioni più lunghe rendono la mediana meno brutta e il 10x simile; il rinnovo
sul vincente alza le due code; la previsione del WTI dà lo stesso del Brent; i contratti interi abbassano il 10x e
mettono «troppo piccolo per scommettere» al posto dei morti. Sui contratti a eventi la distorsione favorito-sfavorito
supera il vantaggio del Dinamico sotto i 15 ¢ e fra 15 e 70 ¢ il rendimento atteso è intorno a zero: nessun lato
Kalshi entra.

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
- [ProShares, scheda di SCO](https://www.proshares.com/our-etfs/leveraged-and-inverse/sco) (obiettivo −2x al
  giorno sull'indice Bloomberg Commodity Balanced WTI Crude Oil, commissione 0,95%, modulo K-1, avvertenza sui
  periodi più lunghi di un giorno) · [SCO su Robinhood](https://robinhood.com/us/en/stocks/SCO)

Dati:

- [IMF PortWatch](https://portwatch.imf.org) (transiti negli stretti)
- [Kalshi, API pubblica: il mercato](https://docs.kalshi.com/api-reference/market/get-market) (`volume_24h_fp`,
  `previous_yes_bid_dollars` «un giorno fa», `yes_bid_size_fp`) ·
  [le operazioni](https://docs.kalshi.com/api-reference/market/get-trades) (`count_fp`, `taker_outcome_side`,
  `is_block_trade`)
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
- Carver, sistema `rob_system` di pysystemtrade: [configurazione con regole e scalari](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/systems/provided/rob_system/config.yaml)
  (accelerazione 16-32-64: 7,82 / 5,56 / 3,90; skew 180 e 365 giorni: 4,59 / 2,35 con medie a 45 e 90 giorni),
  regole [accel](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/systems/provided/rules/accel.py),
  [breakout](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/systems/provided/rules/breakout.py),
  [carry](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/systems/provided/rules/carry.py) e
  [fattori](https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/systems/provided/rules/factors.py)
- Koijen, Moskowitz, Pedersen, Vrugt, [Carry](http://docs.lhpedersen.com/Carry.pdf);
  Gorton, Hayashi, Rouwenhorst, [The fundamentals of commodity futures returns](https://www.nber.org/system/files/working_papers/w13249/w13249.pdf);
  Boons e Prado, [Basis-momentum](https://conference.nber.org/conf_papers/f89296/f89296.pdf);
  AQR, [Commodities for the long run](https://www.nber.org/system/files/working_papers/w22793/w22793.pdf)
- Kang, Rouwenhorst, Tang, [A tale of two premiums](https://conference.nber.org/conf_papers/f69870/f69870.pdf) (posizionamento;
  [versione pubblicata, Journal of Finance 2020](https://ideas.repec.org/a/bla/jfinan/v75y2020i1p377-417.html), solo riassunto)
- Ellwanger, [Driven by fear? The tail risk premium in the crude oil futures market](https://conference.nber.org/conf_papers/f89605.pdf);
  Chevallier e Sévi, [A fear index to predict oil futures returns](https://services.bepress.com/feem/paper813)
  ([altra copia](https://hal-amu.archives-ouvertes.fr/hal-01463111v1); solo riassunto)
- Idee provate con i parametri ricordati dagli articoli, **non riletti in questa sessione**: Asness,
  Moskowitz, Pedersen (valore); Fuertes, Miffre, Rallis (trend e struttura a termine); Basu e Miffre (pressione
  di copertura); Hong e Yogo (open interest); Ye, Zyren, Shore (scorte); Bollerslev, Tauchen, Zhou (premio di
  volatilità, sulle azioni). Sono tutte fra gli scartati: un parametro ricordato male non cambia un libro.
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
- Sul libro speculativo su Kalshi (10): Snowberg, Wolfers e Zitzewitz, *Partisan impacts on the economy:
  evidence from prediction markets and close elections*, Quarterly Journal of Economics 2007 (ricordato, non
  riletto: il comovimento entro minuti fra le probabilità dei mercati a eventi e i future azionari la notte
  delle elezioni del 2004); Wolfers e Zitzewitz, *Prediction markets*, Journal of Economic Perspectives 2004
  (ricordato); Carver, *Systematic Trading* (lo scalare della previsione stimato perché la media di |F| sia 10)
- Sugli indici Kalshi degli altri mercati (11): Bernanke e Kuttner, *What explains the stock market's reaction to
  Federal Reserve policy?*, Journal of Finance 2005 (ricordato, non riletto: una sorpresa di −25 pb vale circa
  +1% sull'indice in giornata); Gürkaynak, Sack e Swanson, *Do actions speak louder than words? The response of
  asset prices to monetary policy actions and statements*, International Journal of Central Banking 2005
  (ricordato); Erb e Harvey, *The golden dilemma*, Financial Analysts Journal 2013 (ricordato: oro e tassi
  reali); Baur e Lucey, *Is gold a hedge or a safe haven?*, Financial Review 2010 (ricordato);
  [Kalshi, API pubblica: i mercati di una serie](https://docs.kalshi.com/api-reference/market/get-markets) (la
  lista non elenca più i mercati regolati da qualche settimana: letto il 15 ottobre 2026 sulle riunioni del FOMC)
- Sull'analista Claude (12): Lopez-Lira e Tang, [Can ChatGPT forecast stock price movements? Return
  predictability and large language models](https://arxiv.org/abs/2304.07619) (arXiv 2304.07619); Glasserman e
  Lin, [Assessing look-ahead bias in stock return predictions generated by GPT sentiment
  analysis](https://arxiv.org/abs/2309.17322) (arXiv 2309.17322); Anthropic,
  [lo strumento di ricerca web dell'API](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
  e [l'output strutturato](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) (letti il 16
  ottobre 2026: `web_search_20250305` senza esecuzione di codice, 10 $ ogni mille ricerche; `output_config.format`,
  incompatibile con le citazioni); [i prezzi](https://platform.claude.com/docs/en/about-claude/pricing) (letti lo
  stesso giorno)
- Sull'allerta di attività anomala (9 F): Meulbroek, *An empirical analysis of illegal insider trading*, Journal
  of Finance 1992 (ricordato, non riletto); Rothschild e Sethi, *Trading strategies and market microstructure:
  evidence from a prediction market*, Journal of Prediction Markets 2016 (ricordato); i casi pubblici:
  [The Block, 28 febbraio 2026: sei portafogli nuovi guadagnano un milione su Polymarket poche ore prima dei raid sull'Iran (Bubblemaps)](https://www.theblock.co/post/391650/fresh-accounts-netted-1-million-on-polymarket-hours-before-us-airstrikes-on-iran-bubblemaps) ·
  [Israele incrimina due persone per le scommesse su Polymarket sulle operazioni militari del giugno 2025](https://www.yahoo.com/news/articles/israel-indicts-two-over-secret-222721126.html)
- Sui punti di svolta e sull'attenuazione (13): Goulding, Harvey e Mazzoleni,
  [Breaking bad trends](https://people.duke.edu/~charvey/Research/Published_Papers/P167_Breaking_bad_trends.pdf),
  Financial Analysts Journal 2023 (letto il 17 ottobre 2026; nel testo estratto dal PDF le equazioni (8)-(10) hanno i
  segni rovinati, e al punto 13 A.3 sono riscritte come posizioni); Carver, *Advanced Futures Trading Strategies*,
  Harriman House 2023, strategia 13 (non riletto: la regola viene dalla
  [ricostruzione di QuantConnect](https://www.quantconnect.com/research/15989/futures-trend-following-and-carry-in-different-risk-regimes/),
  che cita le pagine del libro, e dal codice di Carver in
  [The state of vol](https://qoppac.blogspot.com/2023/10/the-state-of-vol.html)); Sepp e Lucic,
  [The science and practice of trend-following systems](https://arxiv.org/abs/2607.19497) (luglio 2026); Valeyre,
  [Breaking the trend: how to avoid cherry-picked signals](https://arxiv.org/html/2504.10914v13) (luglio 2026);
  Moskowitz, Sabbatucci, Tamoni e Uhl,
  [Nonlinear time series momentum](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5933974) (SSRN, rivisto a
  settembre 2026: letto il solo riassunto, la forma della non linearità non è nel testo accessibile; non provato)
- Sullo stretto (13 E): [Kpler, 26 giugno 2026](https://www.kpler.com/blog/hormuz-transits-are-now-outside-the-imo-lane-what-that-means-for-risk-assessment) ·
  [Al Jazeera, 3 settembre 2026](https://www.aljazeera.com/news/2026/9/3/how-much-oil-is-going-through-hormuz-how-data-doesnt-match-us-claims) ·
  [Al Jazeera, 17 ottobre 2026](https://www.aljazeera.com/news-analysis/2026/10/17/us-and-iran-both-claim-control-of-strait-of-hormuz-amid-attacks-on-vessels)
- Sul Rocket (14): Dubins e Savage, *How to Gamble If You Must: Inequalities for Stochastic Processes* (1965;
  ricordato) e la dispensa di Hannig, [Casino](https://hannig.cloudapps.unc.edu/STOR054/handouts/Casino.pdf) (teorema
  3: il gioco audace è ottimo nel gioco sfavorevole; esercizi 8 e 32: nel gioco equo P = x/a con il gioco timido e
  con quello audace; F(1/2) = p); Goyal e Saretto, *Cross-section of option returns and volatility*, Journal of
  Financial Economics 2009 (ricordato: il rendimento delle opzioni decresce nella differenza fra volatilità
  implicita e realizzata); Dao, Nguyen, Deremble, Lempérière, Bouchaud e Potters, *Tail protection for long
  investors: trend convexity at work* (2016; ricordato); Faith, *Way of the Turtle* (2007; ricordato: la piramide
  sul vincente); Thaler e Ziemba, *Anomalies: parimutuel betting markets*, Journal of Economic Perspectives 1988
  (ricordato: la distorsione favorito-sfavorito); [Whelan, *Makers and Takers: The Economics of the Kalshi
  Prediction Market*, 27 gennaio 2026](https://www.karlwhelan.com/?p=2402) (circa 300 000 contratti: sotto i 10 ¢
  si avverano meno di quanto serve a pareggiare, peggio per chi prende); le commissioni di Kalshi
  [calcolate con la tabella del 10 ottobre 2026](https://chudi.dev/blog/kalshi-fees) (taker round up(0,07 × C × P ×
  (1 − P)), maker 0,0175) e [Finder, Kalshi contro Robinhood, 29 settembre 2026](https://www.finder.com/investments/kalshi-vs-robinhood)
  (Robinhood dal 1° giugno 2026: 10% di k × P × (1 − P) × C, 5% con Gold, più 1 ¢ a contratto per lato su Kalshi;
  tre sedi: Kalshi, ForecastEx, Rothera); [Cointelegraph, 29 aprile 2026](https://cointelegraph.com/news/polymarket-cftc-approval-main-platform-us-report)
  (Polymarket chiede alla CFTC di riaprire agli americani; «Polymarket US» su QCEX dal dicembre 2025, solo sport,
  lista d'attesa); [Alpaca, opzioni](https://docs.alpaca.markets/docs/options-trading) (sul conto di prova le
  opzioni sono attive di serie; livello 2: comprare call e put; `time_in_force` `day` o `gtc`).
