# Validazione e anti-overfitting

## I libri del desk (ottobre 2026)

I quattro libri non passano dal ciclo di promozione descritto più sotto, e non per aggirarlo: quel ciclo misura
se una strategia *scelta fra molte* ha qualcosa, e il suo verdetto sul primo sistema è stato «nessuna». Qui non
c'è scelta: tre regole pubblicate, parametri pubblicati, pesi uguali. La difesa dall'overfitting è non avere
niente da adattare, e mostrare tutto ciò che potrebbe smentire il risultato.

`python -m engine.cli desk-backtest` rifà il conto con lo stesso motore del paper trading (lotti interi,
commissioni e spread dello strumento, interessi sul margine, roll cinque sedute prima della scadenza) e scrive
`state/desk/backtest.json`. Il lavoro settimanale lo rigenera.

| Libro | Periodo | Rend. annuo | Vol. | Sharpe | t | Perdita max | Costi doppi | Stessa chiusura | P(−25%) | P(−50%) in un anno |
|---|---|---|---|---|---|---|---|---|---|---|
| Prudente | 2011-2026 | +3,0% | 8,5% | 0,39 | 1,5 | −25,4% | 0,37 | 0,35 | 0,0% | 0,0% |
| Dinamico | 2011-2026 | +5,1% | 17,4% | 0,37 | 1,5 | −47,4% | 0,35 | 0,33 | 8,4% | 0,0% |
| Spinto | 1986-2026 | +10,6% | 49,2% | 0,45 | 2,9 | −89,0% | 0,37 | 0,51 | 88,0% | 20,3% |
| BNO tenuto | 2011-2026 | +4,0% | 34,9% | 0,29 | 1,1 | −87,1% | | | | |
| WTI tenuto | 1986-2026 | +4,2% | 39,0% | 0,31 | 1,9 | −98,7% | | | | |

Componenti su un conto di riferimento senza effetto lotto (apertura successiva / stessa chiusura):

| | Trend | Carry | Carry-momentum | Le tre |
|---|---|---|---|---|
| BNO, solo long | 0,28 / 0,25 | 0,22 / 0,19 | 0,23 / 0,18 | 0,36 / 0,31 |
| WTI, long e short | 0,29 / 0,29 | 0,47 / 0,51 | 0,18 / 0,33 | 0,43 / 0,50 |

Che cosa dicono questi numeri, senza abbellirli:

- **Nessun libro sul Brent arriva a t = 2.** Quindici anni non bastano per uno Sharpe di 0,4. Il libro sul WTI
  ci arriva perché ha quarant'anni di storia, non perché sia migliore.
- **Il vantaggio sul comprare e tenere è nel rischio, non nel rendimento.** Il libro prudente rende meno del
  fondo tenuto (3,0% contro 4,0%) con un quarto della volatilità e un terzo della perdita massima.
- **Gli ultimi anni:** prudente 2023 −3,8%, 2024 −5,1%, 2025 −6,8%, 2026 +27,0%.
- **Il libro spinto è ciò che dichiara.** −89% di perdita massima, un giorno a −30%.
- **La rovina è stimata per difetto:** il bootstrap non può contenere un giorno peggiore del peggiore già visto.

Il libro delle opzioni non ha un backtest: ha una **simulazione su modello** (≈), che dà uno Sharpe fra 0,42 e
0,71 secondo lo smile e i costi ipotizzati, +1,9-4,2% l'anno, perdita massima −14%, 9 operazioni l'anno. È
marcata come approssimazione ovunque compaia e non decide nulla.

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
- una barra viene mostrata ai broker solo quando il flusso in ritardo l'ha consegnata tutta (quindici minuti
  dopo la sua fine);
- un roll avviene solo con i due contratti prezzati nella stessa mezz'ora, e un libro rimasto sul contratto
  vecchio non decide sul nuovo;
- un giro chiede solo le tabelle che la sua decisione legge, e dopo un rifiuto di Yahoo per troppe richieste
  smette di chiedere per due minuti invece di insistere;
- una struttura di opzioni non perde mai più del rischio definito all'apertura.

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
