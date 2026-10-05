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
