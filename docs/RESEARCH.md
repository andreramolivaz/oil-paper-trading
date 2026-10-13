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
