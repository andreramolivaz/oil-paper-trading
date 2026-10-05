# Validazione e anti-overfitting

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
