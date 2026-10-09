# Validazione e anti-overfitting

## I libri del desk (ottobre 2026)

I quattro libri non passano dal ciclo di promozione descritto più sotto, e non per aggirarlo: quel ciclo misura
se una strategia *scelta fra molte* ha qualcosa, e il suo verdetto sul primo sistema è stato «nessuna».

Fino all'8 ottobre 2026 qui non c'era scelta: tre regole pubblicate, parametri pubblicati, pesi uguali. **Da
allora una scelta c'è**, e va detta: i segnali sono sette perché quattro sono stati tenuti fra sedici provati.
Le difese sono queste, e nessuna è una prova:

- ogni candidato aveva una ragione pubblicata e i parametri del suo autore, scritti prima di guardare il
  risultato, ed è stato provato una volta sola;
- sono riportati tutti e sedici, con i loro numeri, non solo i quattro tenuti (`docs/RESEARCH.md`);
- i tenuti reggono a un giorno di ritardo, ai parametri vicini e nelle due metà del campione; due con numeri
  migliori di alcuni tenuti (open interest, scorte) sono fuori perché cambiano segno con la finestra accanto;
- la combinazione usata non è la cella migliore della tabella delle combinazioni;
- la stima che non sceglie nulla (tutti i candidati a pesi uguali) è pubblicata accanto: 0,61 contro 0,70 sul
  WTI. Circa metà del miglioramento resta anche così; l'altra metà può essere selezione;
- gli obiettivi di volatilità dei libri non sono stati alzati.

`python -m engine.cli desk-backtest` rifà il conto con lo stesso motore del paper trading (lotti interi,
commissioni e spread dello strumento, interessi sul margine, roll cinque sedute prima della scadenza, prezzi
veri di SCO per il lato short del libro dinamico) e scrive `state/desk/backtest.json`. Il lavoro settimanale lo
rigenera, e lo rigenera il primo giro dopo un cambio di regole: il file porta l'impronta delle regole con cui è
stato calcolato.

| Libro | Periodo | Rend. annuo | Vol. | Sharpe | t | Perdita max | Costi doppi | Stessa chiusura | P(−25%) | P(−50%) in un anno |
|---|---|---|---|---|---|---|---|---|---|---|
| Prudente | 2011-2026 | +4,9% | 9,3% | 0,56 | 2,2 | −23,3% | 0,54 | 0,50 | 0,0% | 0,0% |
| Dinamico | 2011-2026 | +19,6% | 28,5% | 0,77 | 3,0 | −49,6% | 0,73 | 0,70 | 29,4% | 0,1% |
| Spinto | 1986-2026 | +22,2% | 54,5% | 0,64 | 4,1 | −86,0% | 0,57 | 0,69 | 88,9% | 21,9% |
| BNO tenuto | 2011-2026 | +4,0% | 34,9% | 0,29 | 1,1 | −87,1% | | | | |
| WTI tenuto | 1986-2026 | +4,2% | 39,0% | 0,31 | 1,9 | −98,7% | | | | |

Con i tre segnali di prima gli stessi libri davano 0,39 / 0,37 / 0,45. Il dinamico senza il lato short, con i
sette segnali, dà 0,54.

Segnali e fonti su un conto di riferimento senza effetto lotto, obiettivo di volatilità 15% (Sharpe con
esecuzione all'apertura successiva):

| | Trend | Accel. | Skew | Carry | Carry-mom. | Rame | Dollaro | Prezzo | Curva | Altri mercati | Prima (tre) | Adesso (sette) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BNO, solo long | 0,28 | 0,47 | 0,33 | 0,22 | 0,23 | 0,56 | 0,61 | 0,39 | 0,24 | 0,66 | 0,35 | 0,48 |
| BNO, short con SCO | 0,41 | 0,54 | 0,38 | 0,36 | 0,32 | 0,60 | 0,87 | 0,55 | 0,38 | 0,86 | 0,46 | 0,76 |
| WTI, long e short | 0,29 | 0,28 | 0,44 | 0,47 | 0,18 | 0,50 | 0,43 | 0,46 | 0,44 | 0,49 | 0,43 | 0,65 |

Che cosa dicono questi numeri, senza abbellirli:

- **Sul Brent i libri passano t = 2, di poco per il prudente.** Quindici anni restano pochi: 0,56 è a 2,2
  errori standard da zero. Il libro sul WTI ha quarant'anni di storia, e lì t = 4.
- **Sul fondo Brent rame e dollaro fanno quasi tutto** (0,66 da soli contro 0,39 del prezzo e 0,24 della
  curva). Sul WTI in quarant'anni le tre fonti valgono uguale (0,46 / 0,44 / 0,49): è la misura più affidabile
  delle due, e dice che il 2010-2026 è stato un periodo favorevole a quei due segnali.
- **Il lato short del dinamico viene da tre anni** (2014, 2015, 2020). Negli altri tredici, in dieci ha tolto.
- **Gli ultimi anni:** prudente 2023 −5,0%, 2024 −2,6%, 2025 −8,8%, 2026 +44,8%; dinamico −12,7%, −11,9%,
  −16,8%, +94,8%. Tre anni negativi di fila con la previsione di prima, tre anni negativi di fila con questa.
- **Il libro spinto è ciò che dichiara.** −86% di perdita massima, un giorno a −31%.
- **La rovina è stimata per difetto:** il bootstrap non può contenere un giorno peggiore del peggiore già visto.

Il libro delle opzioni non ha un backtest: ha una **simulazione su modello** (≈), che dà uno Sharpe fra 0,39 e
0,71 secondo lo smile e i costi ipotizzati, +2,1-5,0% l'anno, perdita massima −18%, 11 operazioni l'anno. La
nuova previsione non l'ha migliorata (0,63 prima, 0,63 dopo). È marcata come approssimazione ovunque compaia e
non decide nulla.

Ciò che i test garantiscono (`tests/test_desk_*.py`, `tests/test_tick_job.py`):

- troncare i dati al giorno T non cambia nessuna previsione fino a T, né nelle funzioni di segnale né nel
  costruttore delle serie;
- nessuna decisione, per qualunque previsione, volatilità e prezzo, punta a più del tetto del libro o di 10x
  (test di proprietà);
- un ordine si esegue sulla prima barra che *inizia* dopo la decisione, mai su quella in corso;
- il rendimento in un giorno di roll è quello del contratto detenuto, mai la differenza fra due contratti;
- un giro ripetuto non duplica né decisioni né eseguiti; un contratto mai visto non viene marcato su barre
  vecchie di giorni;
- una nuova decisione sostituisce l'ordine ancora in coda e non si somma a esso: due decisioni senza una barra
  in mezzo lasciano un solo ordine e, dopo l'esecuzione, la posizione voluta e non il doppio;
- una barra viene mostrata ai broker solo quando il flusso in ritardo l'ha consegnata tutta: quindici minuti
  dopo la sua fine, contati dal momento in cui la tabella è stata scaricata e non dall'orologio (se uno
  scaricamento fallisce, la barra letta mentre si formava non viene scambiata per una barra finita);
- margine, soglia di perdita giornaliera e chiusura del conto si valutano una volta per ogni momento, su barre
  finite di ciò che il libro tiene: mai su una quotazione, sulla barra di un altro simbolo, su una barra di un
  giorno contabile già chiuso o su una barra più vecchia dei prezzi con cui quella posizione è già stata
  valutata (una tabella che manca per un giorno, quando torna, esegue gli ordini che la aspettavano e
  nient'altro; le barre perse dello stesso giorno, invece, sono notizie e si rileggono in ordine);
- «quanto sono vecchi i prezzi di un libro» si misura sull'orario dei prezzi, non su quello del giro che li ha
  letti: un giro fuori orario (ogni riavvio ne fa uno, a un minuto qualunque) non fa saltare la barra
  successiva, e un giro che non trova prezzi non dichiara «prezzi di adesso»;
- l'ora della decisione che scatta mentre il giro sta scaricando i dati rimanda la decisione al giro dopo,
  che scarica prima le tabelle; un ordine rifiutato dal broker non viene contato fra quelli in coda, e la
  motivazione lo dice;
- un roll avviene solo con i due contratti prezzati nella stessa mezz'ora, e un libro rimasto sul contratto
  vecchio non decide sul nuovo;
- un giro chiede solo le tabelle che la sua decisione legge, e dopo un rifiuto di Yahoo per troppe richieste
  smette di chiedere per due minuti invece di insistere;
- una struttura di opzioni non perde mai più del rischio definito all'apertura;
- i sette segnali sono causali uno per uno e insieme, anche su un archivio abbastanza lungo da avere lo skew;
  rame e dollaro sono letti alla chiusura del giorno *prima* (un salto del rame nel giorno D non sposta la
  previsione del giorno D), e senza le loro tabelle tacciono solo loro;
- la combinazione per fonte e il suo gemello scalare danno lo stesso numero riga per riga, con buchi in un
  segnale, in una fonte intera o in entrambi;
- un libro con il lato short non supera il suo tetto da nessuna delle due parti, non compra mai il fondo
  inverso a margine e, se ne tiene più di quanto il fine settimana consente, viene riportato sotto il tetto e
  non solo al bordo della fascia;
- quando un libro cambia lato la vendita si esegue prima dell'acquisto e l'acquisto la aspetta: se le barre
  delle due tabelle arrivano a un giro di distanza il libro resta per quel giro senza posizione, mai con tutti
  e due i lati;
- la vendita di un fondo chiude ciò che trova e mai di più: se nel frattempo la posizione è stata ridotta (da
  un taglio per il tetto di leva) l'ordine si riduce con lei, e un fondo non va mai sotto zero;
- i tetti del lato short valgono al prezzo di esecuzione e dopo ogni valutazione, non solo al prezzo della
  decisione: un'apertura in salto non porta il fondo inverso oltre il valore del conto né l'esposizione oltre
  il tetto (il broker conta il fondo inverso per due volte i suoi dollari);
- senza un prezzo recente del fondo inverso il lato short non si apre e non cresce; un fondo inverso rimasto
  in un libro che non lo prevede più viene valutato al suo prezzo e venduto alla decisione successiva;
- il fondo inverso è prezzato, eseguito e marcato sulle sue barre, mai su quelle del fondo che accompagna, e le
  sue barre non arrivano ai libri che non lo usano;
- di rame e dollaro si legge una riga solo quando uno scaricamento di un giorno successivo l'ha confermata, e
  le due tabelle si scaricano al giro della prima decisione della giornata, ciascuna giudicata per conto suo;
- un backtest calcolato con altre regole, o mentre mancava una tabella poi arrivata, viene ricalcolato al giro
  successivo; se il controllo o il calcolo falliscono, il giro dei libri non ne risente.

**Revisione indipendente.** Prima di andare in linea, il codice dei sette segnali e del lato short è stato
riletto da un agente che non lo aveva visto scrivere, con il compito di romperlo. Ha trovato sette difetti,
tutti riprodotti e corretti: la soglia di perdita valutata sulla barra di un altro simbolo con una quotazione
vecchia di mezz'ora; SCO valutato sulla tabella di BNO dopo un cambio di configurazione; i tetti del lato short
rispettati solo al prezzo della decisione; una gamba eseguita e l'altra no quando una tabella arriva in
ritardo (due lati insieme, oppure un fondo venduto allo scoperto); barre in ritardo rilette come notizie
contro il capitale d'apertura di oggi (difetto vecchio quanto il desk); il controllo del backtest fuori dalla
sua protezione; il rame usato come spia anche per il dollaro. Verificando le correzioni su scaricamenti veri
sono emerse altre due cose di Yahoo, corrette anch'esse: una barra letta mentre si forma sembra finita se lo
scaricamento successivo fallisce, e la riga giornaliera del giorno in cui si scarica non è la chiusura di quel
giorno. Nessuna di queste correzioni ha cambiato un numero del backtest (il risultato è identico cifra per
cifra): riguardano tutte condizioni degradate del funzionamento dal vivo, che un replay giornaliero non
incontra mai.

**Seconda revisione.** Un secondo agente ha attaccato le correzioni con simulatori scritti da lui: prezzi al
minuto come verità, flusso in ritardo di 10-15 minuti, scaricamenti che falliscono, riavvii a un minuto
qualunque, fine settimana, azzeramenti, il lato short tolto e rimesso; circa 45.000 giri. Ha trovato il
difetto che contava: la regola «una barra è finita quando lo dice l'orario dello scaricamento» **dal vivo non
girava**, perché il costruttore delle serie buttava via quell'orario prima che il giro potesse leggerlo. Tutti
i test lo avevano messo a mano sulle tabelle di prova, e passavano. Con il 20% di scaricamenti falliti il
simulatore contava 44-59 barre lette a metà e 14-20 controlli di rischio su prezzi vecchi per ogni otto giorni.
Corretto, e ora la regola è provata passando dall'archivio, come in produzione. Dalla stessa revisione: il
permesso di contare una vendita in coda valeva anche per i conti del primo sistema (ora lo chiede solo chi lo
usa, e il broker del primo sistema è identico a prima su 3.000 sequenze casuali); una posizione aperta da un
ordine eseguito in ritardo aspettava una barra prima del primo controllo; la decisione presa su tabelle non
aggiornate quando l'ora scatta durante lo scaricamento; un acquisto di SCO in coda sopravviveva alla rimozione
del lato short dalla configurazione; ordini rifiutati contati come in coda e due frasi false nella
motivazione; l'impronta del backtest non vedeva `config/risk.yaml`. I simulatori, rilanciati sul codice
corretto, segnalano solo due comportamenti voluti: l'ultima barra di ieri che arriva quando il giorno
contabile è già cambiato, e la chiusura giornaliera usata come ripiego che viene datata «adesso». Un terzo
passaggio dello stesso revisore sulle sole correzioni (stessi simulatori, stessi volumi, più 38.880
combinazioni di orario per la regola dell'ora di decisione e 4.000 sequenze sul broker a confronto con la
versione precedente) non ha trovato altro che un annullamento senza nota, aggiunta.

Che cosa non è stato verificato da nessuno: il comportamento di Yahoo oltre i giorni osservati (5-9 ottobre
2026), il libro delle opzioni in questa revisione, e la pagina oltre una lettura.

Per i punti aggiunti con i sette segnali, il lato short e le due revisioni, sessantadue difetti sono stati
reintrodotti nel codice uno alla volta (la vendita dopo l'acquisto, il fondo inverso valutato sulla tabella
sbagliata, il rame letto nello stesso giorno, lo skew con la media dell'intero campione, il peso del fondo
inverso ignorato all'esecuzione, le barre in ritardo rilette come notizie, l'orario dello scaricamento buttato
via prima del giro, e così via): per sessanta almeno un test fallisce; gli altri due sono regole scritte due
volte, e toglierne una copia non cambia il comportamento.

I test di proprietà (ordini e salti di prezzo a caso contro i tetti di leva) estraggono gli stessi esempi a
ogni esecuzione, perché una croce rossa deve voler dire che qualcosa si è rotto e non che è uscito un caso
sfortunato. È successo: una proprietà scritta male, che scambiava il riacquisto di una posizione corta per un
ordine che aggiunge rischio, ha superato 150 esempi casuali per ore e poi è fallita due volte di fila. Corretta
la proprietà, la ricerca a caso è stata ripetuta su 120 semi diversi (18.000 conti) senza trovare altro; si
rifà con `HYPOTHESIS_PROFILE=explore pytest --hypothesis-seed=N`.

---

## Il primo sistema: ciclo di vita e risultati

Con 18 strategie e molti parametri il rischio principale non è sbagliare un segnale: è innamorarsi di un
backtest. Questo documento descrive come il sistema si difende e dove leggere i risultati.

## Il ciclo di vita di una strategia

```
ricerca ──► incubazione ──► attiva ──► ritirata
            (conto ombra,   (peso nel   (decadimento
             peso zero)     master)      rilevato)
```

Una strategia entra nel master **solo** se supera tutte queste condizioni, verificate dal job settimanale:

| Condizione | Soglia | Perché |
|---|---|---|
| Deflated Sharpe Ratio | probabilità > 0,95 | Corregge lo Sharpe per il numero di tentativi, la non normalità e la lunghezza del campione (Bailey e López de Prado 2014). Il registro dei tentativi (`state/trials.jsonl`) conta ogni variante provata: niente cherry picking. |
| Probability of Backtest Overfitting | < 0,5 | CSCV su tutte le combinazioni: misura quanto spesso la variante migliore in-sample finisce sotto la mediana out-of-sample. Sopra 0,5 la selezione non ha informazione. |
| Sharpe out-of-sample a costi raddoppiati | > 0 | Se il margine sparisce raddoppiando i costi, il margine non c'era. |
| Esperienza | ≥ 100 operazioni **o** ≥ 3 anni | Una serie corta non distingue abilità da fortuna. |

Il decadimento di una strategia già attiva viene rilevato con un CUSUM unilaterale sulla performance live
standardizzata rispetto all'attesa di backtest: all'allarme la strategia passa a *ritirata* e il peso va a zero.

## Metodo

- **Walk-forward**: ogni stima (regime, modelli di previsione, pesi) usa solo il passato. Il test
  `tests/test_no_lookahead.py` verifica che troncare i dati al giorno T non cambi nessuna decisione presa fino
  a T, su tre fronti indipendenti: percorso point-in-time contro percorso veloce, dati futuri aggiunti, e una
  pubblicazione EIA che non deve essere visibile prima della sua ora di uscita.
- **CV purged con embargo** e **CPCV** per le stime che richiedono validazione incrociata, con le etichette
  sovrapposte escluse dal training (López de Prado, capitoli 7 e 12).
- **Costi sempre inclusi**, con sensibilità a costi raddoppiati in ogni tabella.
- **Risultati per regime e per crisi**: 1990-91, 2008, 2014-16, 2020, 2022 e il 2026 in corso.
- **Stress test** storici e sintetici (riapertura improvvisa, escalation, gap di fine settimana).
- **Monte Carlo del rischio di rovina** con bootstrap a blocchi sui rendimenti reali, per calibrare la politica
  di leva sotto un vincolo esplicito di probabilità di rovina.

## Dove leggere i risultati

| Dove | Cosa |
|---|---|
| `out/backtest/backtest.md` | Report leggibile: metriche per strategia, per regime, per crisi, costi, DSR, PBO, istogramma della leva, stress test |
| `state/validation.json` | Lo stesso contenuto in JSON, letto dal motore per decidere i cicli di vita e dalla dashboard |
| `state/trials.jsonl` | Registro di ogni variante valutata (append-only), denominatore del Deflated Sharpe |
| Dashboard → Strategie | Classifica dei conti ombra e, per ciascuna, perché ha o non ha peso |

## Come rileggere i numeri senza ingannarsi

1. **Uno Sharpe alto su pochi mesi non significa niente.** Guardare prima il numero di operazioni e gli anni.
2. **PBO vicino a 0,5 significa che scegliere la migliore non aiuta**: è il valore che ci si aspetta quando la
   selezione non ha informazione, non un errore del codice.
3. **Se una previsione non batte il random walk, la dashboard lo scrive.** Theil U sotto 1 e Diebold-Mariano
   significativo sono il minimo per dire che un modello serve.
4. **Le strategie a peso zero non sono rotte**: sono in incubazione e lavorano sul conto ombra finché non
   hanno abbastanza storia. Il motivo è scritto per esteso accanto a ciascuna.

## Esito della validazione al 5 ottobre 2026

Questi sono i numeri veri dell'ultima esecuzione completa (`out/backtest/backtest.md`, finestra
2008-01-02 → 2026-10-02, 18,5 anni, 4665 osservazioni, capitale iniziale 10 000 $). Vanno letti come sono.

**Nessuna strategia è stata promossa. Il master resta fermo.**

| Numero | Valore | Come leggerlo |
|---|---|---|
| Sharpe del master | **-0,77** | Il portafoglio, così com'è, perde denaro nel backtest |
| CAGR del master | **-3,0%** | Su 18,5 anni, con 884 operazioni e hit rate 28,4% |
| Max drawdown del master | **-44,3%** | Mai vicino alla soglia di conto morto, ma inaccettabile |
| Miglior conto ombra | **S3 Sharpe 0,17**, poi S1 0,10 | Non distinguibili da zero: PSR 0,77 e 0,67 |
| DSR di ogni strategia | **0,00** | Nessuna supera la soglia 0,95 dopo il deflazionamento su 18 prove |
| PBO (CSCV, 12 870 combinazioni) | **0,457** | Vicino a 0,5: selezionare la migliore non trasferisce nulla fuori campione |
| Strategie promosse | **nessuna** | Tutte e 18 in incubazione, peso zero, solo conto ombra |

Conseguenze, già visibili in dashboard:

* Il master è in contanti a 10 000 $ e la Home lo dichiara («Nessuna strategia attiva: il master resta fermo»).
* La leva ha superato 1x in **1 giorno su 4666** (massimo 1,25x): il gate del §9 si comporta come richiesto.
* Nove strategie su diciotto hanno chiuso **zero operazioni** in 18,5 anni, perché le feature che richiedono
  (mesi lontani della curva, scorte, macro, calendario OPEC+, stagionalità, opzioni) non sono disponibili su
  tutta la finestra. Le loro tesi non sono state smentite: **non sono state testate**. È una differenza
  importante e non va nascosta dietro un «peso zero».

Non si guarda una seconda volta lo stesso campione per trovare un sottoinsieme che funziona: sarebbe
esattamente il data snooping che PBO e DSR servono a misurare. La strada onesta è ridurre il numero di prove,
estendere la copertura delle feature e lasciare correre i conti ombra in avanti, su dati che non esistevano
quando il codice è stato scritto.

## Calibrazione del rischio (Monte Carlo su dati veri)

20 000 percorsi × 250 giorni, bootstrap a blocchi stazionari (Politis-Romano, blocco medio 10) sui rendimenti
giornalieri reali dello spot EIA Dated Brent 1987-05-20 → 2026-09-29 (9 096 rendimenti, vol annualizzata 42,8%),
costi 2,5 bps per unità di turnover di leva, rovina = -50%, seed 20261005.

| Politica | Leva media | P(rovina) | Crescita geom. | Mediana finale | DD p99 |
|---|---|---|---|---|---|
| Costante 1x | 1,00 | 0,0875 | +6,00%/a | 10 613 $ | 0,737 |
| **Motore, configurazione attuale** | **0,41** | **0,0000** | **+3,34%/a** | **10 337 $** | **0,332** |
| Kelly pieno (`kelly_fraction` 1,0) | 1,13 | 0,0955 | +0,75%/a | 10 075 $ | 0,694 |

Con gli scenari sintetici del §4.5 iniettati (riapertura di Hormuz nel 15% dei percorsi, escalation nel 25%):
1x → P(rovina) 0,1029; motore → P(rovina) 0,0000.

Il vincolo `max_ruin_probability: 0.05` di `config/risk.yaml` è **rispettato**. Kelly pieno quasi triplica la
rovina e **abbassa** la crescita (variance drag): è la ragione del «mai Kelly pieno» del brief, misurata.
La griglia di calibrazione indica come alternativa `kelly_fraction` 0,33 con `es99_budget` 0,06
(P(rovina) 0,0008, crescita +4,10%/a); la configurazione resta a **0,25 / 0,12**, la più prudente delle due,
e il cambiamento è una decisione del proprietario, non del codice. Da notare che a Kelly 0,25 il budget di ES
è **lasco**: il termine di Kelly è quello che vincola, quindi valori di `es99_budget` fra 0,03 e 0,20 danno lo
stesso risultato alla volatilità di oggi.

Verifiche sugli episodi storici reali (stesso spot): Guerra del Golfo 1991-01-17 attesa -33%, osservata
**-30,3%**; Abqaiq 2019-09-16 attesa +15%, osservata **+11,7%** sullo spot (+14,6% sul settlement front);
Covid 2020-03-09 attesa -24%, osservata **-22,5%**. Un long 1x sopravvive a tutti gli 8 episodi configurati
senza margin call; un long 3x viene liquidato dallo scenario di riapertura a -30% in tre giorni.
