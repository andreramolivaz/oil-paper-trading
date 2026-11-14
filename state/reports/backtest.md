# Report di backtest

Generato il 2026-11-14T11:09:44.345663Z · finestra 2011-11-14 → 2026-11-13 (14,9 anni, 3752 osservazioni) · capitale iniziale 10000 $

_Simulazione a scopo di studio: paper trading, nessun consiglio finanziario._

## Metriche per strategia

| Strategia | Sharpe | Sortino | CAGR | Vol | Max DD | Hit rate | PSR | Oper. | Peso |
|---|---|---|---|---|---|---|---|---|---|
| Master (portafoglio) | -0,69 | -0,85 | -2,6% | 3,8% | -37,2% | 29,6% | 0,00 | 886 | 1,000 |
| Momentum multi-orizzonte filtrato dal carry | 0,17 | 0,23 | 0,2% | 1,0% | -3,2% | 11,2% | 0,74 | 264 | 0,000 |
| Breakout da compressione | -0,44 | -0,56 | -0,2% | 0,5% | -4,2% | 1,1% | 0,04 | 108 | 0,000 |
| Trend adattivo (Kalman) | 0,01 | 0,01 | -0,1% | 5,7% | -24,3% | 35,5% | 0,52 | 938 | 0,000 |
| Carry e roll yield | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Spread di calendario M1-M3 | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Butterfly di curva 1-3-6 | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Conferma fisica | 0,08 | 0,15 | 0,0% | 0,0% | -0,0% | 0,0% | 0,63 | 2 | 0,000 |
| Brent-WTI (cointegrazione) | -0,33 | -0,38 | -0,7% | 1,9% | -12,1% | 4,9% | 0,04 | 261 | 0,000 |
| Crack spread 3-2-1 | -0,69 | -0,89 | -1,3% | 1,9% | -22,1% | 7,5% | 0,00 | 530 | 0,000 |
| Fair value macro | -0,50 | -0,61 | -0,5% | 0,9% | -8,1% | 1,6% | 0,02 | 83 | 0,000 |
| Sorpresa sulle scorte EIA | -0,61 | -0,72 | -0,2% | 0,4% | -3,5% | 1,2% | 0,00 | 132 | 0,000 |
| Playbook OPEC+ | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Premio geopolitico | -0,63 | -0,84 | -1,3% | 2,0% | -22,9% | 6,3% | 0,01 | 564 | 0,000 |
| Stagionalità seria | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Posizionamento COT | -0,59 | -0,78 | -1,5% | 2,5% | -21,4% | 8,6% | 0,01 | 399 | 0,000 |
| Regime di volatilità | -0,58 | -0,69 | -0,8% | 1,4% | -14,3% | 2,9% | 0,01 | 274 | 0,000 |
| Reversal di breve | -0,17 | -0,23 | -0,2% | 0,9% | -3,0% | 2,1% | 0,26 | 163 | 0,000 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Convinzione degli insider (Form 4) | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |

## Risultati per regime

| Strategia | Contango + eccesso d'offerta + trend ribassista | Range a bassa volatilità | Shock/crash | Squeeze rialzista | Transizione |
|---|---|---|---|---|---|
| Master (portafoglio) | -1,38 | -0,16 | -0,92 | 0,41 | -2,85 |
| Momentum multi-orizzonte filtrato dal carry | 0,58 | 0,34 | -0,13 | 0,71 | -1,78 |
| Breakout da compressione | -0,59 | -0,39 | n/d | -0,16 | -1,01 |
| Trend adattivo (Kalman) | -0,25 | 0,36 | -0,21 | 0,29 | -0,71 |
| Carry e roll yield | n/d | n/d | n/d | n/d | n/d |
| Spread di calendario M1-M3 | n/d | n/d | n/d | n/d | n/d |
| Butterfly di curva 1-3-6 | n/d | n/d | n/d | n/d | n/d |
| Conferma fisica | 0,14 | n/d | n/d | n/d | n/d |
| Brent-WTI (cointegrazione) | -0,19 | -0,43 | -0,94 | 0,26 | 0,67 |
| Crack spread 3-2-1 | -0,86 | -0,65 | 0,54 | -0,50 | -1,90 |
| Fair value macro | -0,33 | -0,44 | -1,32 | 0,12 | -0,68 |
| Sorpresa sulle scorte EIA | -0,51 | -0,67 | -0,69 | -1,27 | 0,38 |
| Playbook OPEC+ | n/d | n/d | n/d | n/d | n/d |
| Premio geopolitico | -1,19 | -0,31 | -0,07 | -0,53 | -1,57 |
| Stagionalità seria | n/d | n/d | n/d | n/d | n/d |
| Posizionamento COT | -0,99 | -0,47 | -0,69 | 1,43 | -1,08 |
| Regime di volatilità | -0,13 | -0,63 | 0,11 | -0,08 | -2,43 |
| Reversal di breve | -0,37 | 0,37 | -1,25 | -0,86 | -0,54 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | n/d | n/d | n/d |
| Convinzione degli insider (Form 4) | n/d | n/d | n/d | n/d | n/d |

_Valori: Sharpe annualizzato nel regime._

## Risultati per crisi

| Episodio | Disponibile | Rend. totale | Sharpe | Max DD |
|---|---|---|---|---|
| Guerra del Golfo | no — solo 0 giorni di backtest nell'episodio (minimo 5) | — | — | — |
| Crisi finanziaria | no — solo 0 giorni di backtest nell'episodio (minimo 5) | — | — | — |
| Eccesso d'offerta OPEC | sì | 2,7% | 0,41 | -5,2% |
| Attacco ad Abqaiq | sì | -0,1% | -3,70 | -0,1% |
| Covid e WTI negativo | sì | -5,8% | -1,51 | -7,3% |
| Invasione russa dell'Ucraina | sì | 0,1% | 0,11 | -1,4% |
| Guerra USA/Israele-Iran e Hormuz | sì | 0,9% | 0,63 | -1,3% |
| Tregua di giugno-luglio 2026 | sì | -0,5% | -1,70 | -0,9% |

## Sensibilità ai costi

| Strategia | Sharpe 1x | Sharpe 2x | CAGR 2x | Pareggio (bps) |
|---|---|---|---|---|
| Master (portafoglio) | -0,69 | -0,78 | -3,0% | -18,9 |
| Momentum multi-orizzonte filtrato dal carry | 0,17 | 0,06 | 0,1% | 3,8 |
| Breakout da compressione | -0,44 | -0,55 | -0,3% | -10,7 |
| Trend adattivo (Kalman) | 0,01 | -0,01 | -0,2% | 1,1 |
| Carry e roll yield | n/d | -6,81 | -0,1% | 0,0 |
| Spread di calendario M1-M3 | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Butterfly di curva 1-3-6 | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Conferma fisica | 0,08 | 0,06 | 0,0% | 9,3 |
| Brent-WTI (cointegrazione) | -0,33 | -0,37 | -0,7% | -24,9 |
| Crack spread 3-2-1 | -0,69 | -0,72 | -1,4% | -54,8 |
| Fair value macro | -0,50 | -0,51 | -0,5% | -140,6 |
| Sorpresa sulle scorte EIA | -0,61 | -0,67 | -0,2% | -26,1 |
| Playbook OPEC+ | n/d | -0,32 | -0,0% | 0,0 |
| Premio geopolitico | -0,63 | -0,65 | -1,3% | -92,9 |
| Stagionalità seria | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Posizionamento COT | -0,59 | -0,60 | -1,5% | -84,2 |
| Regime di volatilità | -0,58 | -0,66 | -0,9% | -18,7 |
| Reversal di breve | -0,17 | -0,22 | -0,2% | -7,3 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | 0,0% | n/d |
| Convinzione degli insider (Form 4) | n/d | -2,20 | -0,2% | 0,0 |

## DSR e PBO

| Strategia | Sharpe | DSR (prob.) | SR0 | Prove |
|---|---|---|---|---|
| Master (portafoglio) | -0,69 | 0,000 | 0,057 | 31 |
| Momentum multi-orizzonte filtrato dal carry | 0,17 | 0,002 | 0,057 | 31 |
| Breakout da compressione | -0,44 | 0,000 | 0,057 | 31 |
| Trend adattivo (Kalman) | 0,01 | 0,000 | 0,057 | 31 |
| Carry e roll yield | n/d | n/d | n/d | 31 |
| Spread di calendario M1-M3 | n/d | n/d | n/d | 31 |
| Butterfly di curva 1-3-6 | n/d | n/d | n/d | 31 |
| Conferma fisica | 0,08 | 0,000 | 0,057 | 31 |
| Brent-WTI (cointegrazione) | -0,33 | 0,000 | 0,057 | 31 |
| Crack spread 3-2-1 | -0,69 | 0,000 | 0,057 | 31 |
| Fair value macro | -0,50 | 0,000 | 0,057 | 31 |
| Sorpresa sulle scorte EIA | -0,61 | 0,000 | 0,057 | 31 |
| Playbook OPEC+ | n/d | n/d | n/d | 31 |
| Premio geopolitico | -0,63 | 0,000 | 0,057 | 31 |
| Stagionalità seria | n/d | n/d | n/d | 31 |
| Posizionamento COT | -0,59 | 0,000 | 0,057 | 31 |
| Regime di volatilità | -0,58 | 0,000 | 0,057 | 31 |
| Reversal di breve | -0,17 | 0,000 | 0,057 | 31 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | n/d | 31 |
| Convinzione degli insider (Form 4) | n/d | n/d | n/d | 31 |

PBO (CSCV, 19 varianti, 12870 combinazioni, fonte: strategie ombra usate come varianti): **0,496** · pendenza IS→OOS -0,846 · probabilità di Sharpe OOS negativo 0,532

## Ciclo di vita

| Strategia | Stato | Peso | Spiegazione |
|---|---|---|---|
| Momentum multi-orizzonte filtrato dal carry | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95 |
| Breakout da compressione | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,55 < 0 |
| Trend adattivo (Kalman) | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,01 < 0 |
| Carry e roll yield | incubation | 0,000 | Peso zero: DSR n/d; Sharpe OOS a costi doppi -6,81 < 0 |
| Spread di calendario M1-M3 | incubation | 0,000 | Peso zero: DSR n/d; Sharpe OOS a costi doppi n/d |
| Butterfly di curva 1-3-6 | incubation | 0,000 | Peso zero: DSR n/d; Sharpe OOS a costi doppi n/d |
| Conferma fisica | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95 |
| Brent-WTI (cointegrazione) | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,37 < 0 |
| Crack spread 3-2-1 | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,72 < 0 |
| Fair value macro | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,51 < 0 |
| Sorpresa sulle scorte EIA | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,67 < 0 |
| Playbook OPEC+ | incubation | 0,000 | Peso zero: DSR n/d; Sharpe OOS a costi doppi -0,32 < 0 |
| Premio geopolitico | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,65 < 0 |
| Stagionalità seria | incubation | 0,000 | Peso zero: DSR n/d; Sharpe OOS a costi doppi n/d |
| Posizionamento COT | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,60 < 0 |
| Regime di volatilità | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,66 < 0 |
| Reversal di breve | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,22 < 0 |
| Opzioni sintetiche (approssimazione) | incubation | 0,000 | Peso zero: DSR n/d; Sharpe OOS a costi doppi n/d |
| Convinzione degli insider (Form 4) | incubation | 0,000 | Peso zero: DSR n/d; Sharpe OOS a costi doppi -2,20 < 0 |

Strategie a peso zero:

- **Momentum multi-orizzonte filtrato dal carry**: Peso zero: DSR 0,00 < 0,95
- **Breakout da compressione**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,55 < 0
- **Trend adattivo (Kalman)**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,01 < 0
- **Carry e roll yield**: Peso zero: DSR n/d; Sharpe OOS a costi doppi -6,81 < 0
- **Spread di calendario M1-M3**: Peso zero: DSR n/d; Sharpe OOS a costi doppi n/d
- **Butterfly di curva 1-3-6**: Peso zero: DSR n/d; Sharpe OOS a costi doppi n/d
- **Conferma fisica**: Peso zero: DSR 0,00 < 0,95
- **Brent-WTI (cointegrazione)**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,37 < 0
- **Crack spread 3-2-1**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,72 < 0
- **Fair value macro**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,51 < 0
- **Sorpresa sulle scorte EIA**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,67 < 0
- **Playbook OPEC+**: Peso zero: DSR n/d; Sharpe OOS a costi doppi -0,32 < 0
- **Premio geopolitico**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,65 < 0
- **Stagionalità seria**: Peso zero: DSR n/d; Sharpe OOS a costi doppi n/d
- **Posizionamento COT**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,60 < 0
- **Regime di volatilità**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,66 < 0
- **Reversal di breve**: Peso zero: DSR 0,00 < 0,95; Sharpe OOS a costi doppi -0,22 < 0
- **Opzioni sintetiche (approssimazione)**: Peso zero: DSR n/d; Sharpe OOS a costi doppi n/d
- **Convinzione degli insider (Form 4)**: Peso zero: DSR n/d; Sharpe OOS a costi doppi -2,20 < 0

## Istogramma della leva

Leva media 0,10x · mediana 0,06x · p95 0,41x · massima 0,97x · giorni sopra 1x: 0 su 3753

| Fascia | Giorni | Quota |
|---|---|---|
| 0-0,25x | 3275 | 87,3% |
| 0,25-0,5x | 399 | 10,6% |
| 0,5-0,75x | 71 | 1,9% |
| 0,75-1x | 8 | 0,2% |
| 1-1,5x | 0 | 0,0% |
| 1,5-2x | 0 | 0,0% |
| 2-3x | 0 | 0,0% |
| 3-5x | 0 | 0,0% |
| 5-10x | 0 | 0,0% |

## Stress test

Posizione testata: long 1x su 10.000 $ · fonte prezzi: brent_spot (n/d) (asof 2026-11-11)

| Scenario | Rend. totale | Max DD | Peggior seduta | Margin call | Liquidazione |
|---|---|---|---|---|---|
| Guerra del Golfo | -13,1% | 57,3% | -30,3% | no | no |
| Crisi finanziaria | -68,4% | 76,6% | -15,5% | no | no |
| Eccesso d'offerta OPEC | -67,1% | 77,4% | -7,8% | no | no |
| Attacco ad Abqaiq | -3,4% | 15,3% | -4,1% | no | no |
| Covid e WTI negativo | -38,3% | 84,7% | -47,5% | no | no |
| Invasione russa dell'Ucraina | 12,7% | 26,5% | -12,5% | no | no |
| Guerra USA/Israele-Iran e Hormuz | 19,3% | 50,4% | -15,4% | no | no |
| Tregua di giugno-luglio 2026 | 3,1% | 18,8% | -4,9% | no | no |
| scenario sintetico: riapertura -20% in 3 giorni | -20,0% | 20,0% | -7,2% | no | no |
| scenario sintetico: riapertura -30% in 3 giorni | -30,0% | 30,0% | -11,2% | no | no |
| scenario sintetico: escalation +10% in una seduta | 10,0% | 0,0% | 10,0% | no | no |
| scenario sintetico: escalation +15% in una seduta | 15,0% | 0,0% | 15,0% | no | no |
| scenario sintetico: gap di weekend +8% | 8,0% | 0,0% | 8,0% | no | no |
| scenario sintetico: gap di weekend -8% | -8,0% | 8,0% | -8,0% | no | no |

## Parti non calcolate

- DSR non calcolato per S4: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S5: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S6: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S12: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S14: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S18: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S21: Sharpe non definito (serie piatta o troppo corta)
