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
3. Curva del Brent reale: fra qualche mese la pendenza di BNO non avrà più bisogno della procura WTI.
4. Contratti a evento contro la probabilità implicita nelle opzioni, in piccolo.
5. Replica di Ewald e altri (2025) sulla stagionalità infragiornaliera del Brent, l'unico lavoro che dichiara
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
