# Report di backtest

Generato il 2026-10-17T08:20:33.477721Z · finestra 2011-10-17 → 2026-10-16 (14,9 anni, 3752 osservazioni) · capitale iniziale 10000 $

_Simulazione a scopo di studio: paper trading, nessun consiglio finanziario._

## Metriche per strategia

| Strategia | Sharpe | Sortino | CAGR | Vol | Max DD | Hit rate | PSR | Oper. | Peso |
|---|---|---|---|---|---|---|---|---|---|
| Master (portafoglio) | -0,71 | -0,87 | -2,8% | 3,8% | -38,6% | 29,2% | 0,00 | 765 | 1,000 |
| Momentum multi-orizzonte filtrato dal carry | 0,03 | 0,03 | 0,0% | 1,0% | -3,5% | 10,6% | 0,54 | 276 | 0,000 |
| Breakout da compressione | -0,50 | -0,61 | -0,2% | 0,4% | -4,0% | 1,0% | 0,02 | 101 | 0,000 |
| Trend adattivo (Kalman) | 0,02 | 0,03 | -0,1% | 5,8% | -22,7% | 34,3% | 0,53 | 929 | 0,000 |
| Carry e roll yield | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Spread di calendario M1-M3 | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Butterfly di curva 1-3-6 | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Conferma fisica | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Brent-WTI (cointegrazione) | -0,36 | -0,40 | -0,7% | 1,9% | -12,7% | 4,9% | 0,03 | 255 | 0,000 |
| Crack spread 3-2-1 | -0,64 | -0,84 | -1,3% | 2,0% | -21,9% | 7,4% | 0,01 | 551 | 0,000 |
| Fair value macro | -0,47 | -0,60 | -0,5% | 1,0% | -8,1% | 1,5% | 0,03 | 75 | 0,000 |
| Sorpresa sulle scorte EIA | -0,60 | -0,70 | -0,2% | 0,4% | -3,6% | 1,3% | 0,00 | 138 | 0,000 |
| Playbook OPEC+ | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Premio geopolitico | -0,65 | -0,86 | -1,4% | 2,1% | -24,0% | 6,4% | 0,01 | 569 | 0,000 |
| Stagionalità seria | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Posizionamento COT | -0,58 | -0,77 | -1,5% | 2,6% | -21,2% | 8,6% | 0,01 | 393 | 0,000 |
| Regime di volatilità | -0,59 | -0,70 | -0,8% | 1,4% | -14,2% | 2,6% | 0,00 | 257 | 0,000 |
| Reversal di breve | -0,09 | -0,14 | -0,1% | 0,8% | -2,9% | 1,6% | 0,36 | 134 | 0,000 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Convinzione degli insider (Form 4) | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |

## Risultati per regime

| Strategia | Backwardation + vol alta + rischio geopolitico | Contango + eccesso d'offerta + trend ribassista | Range a bassa volatilità | Shock/crash | Squeeze rialzista | Transizione |
|---|---|---|---|---|---|---|
| Master (portafoglio) | n/d | -1,10 | -0,27 | -0,62 | 0,34 | -3,83 |
| Momentum multi-orizzonte filtrato dal carry | n/d | 0,13 | -0,07 | 1,37 | 0,50 | -1,50 |
| Breakout da compressione | n/d | -0,53 | -0,43 | n/d | -1,25 | -0,71 |
| Trend adattivo (Kalman) | n/d | -0,28 | 0,00 | 1,03 | 0,71 | -0,49 |
| Carry e roll yield | n/d | n/d | n/d | n/d | n/d | n/d |
| Spread di calendario M1-M3 | n/d | n/d | n/d | n/d | n/d | n/d |
| Butterfly di curva 1-3-6 | n/d | n/d | n/d | n/d | n/d | n/d |
| Conferma fisica | n/d | n/d | n/d | n/d | n/d | n/d |
| Brent-WTI (cointegrazione) | n/d | -0,04 | -0,31 | -0,82 | -0,87 | -1,53 |
| Crack spread 3-2-1 | n/d | -1,41 | -0,48 | 0,67 | 0,59 | -1,76 |
| Fair value macro | n/d | -0,40 | 0,07 | -1,74 | 0,13 | -0,68 |
| Sorpresa sulle scorte EIA | n/d | 0,13 | -0,97 | -0,63 | -1,69 | 1,51 |
| Playbook OPEC+ | n/d | n/d | n/d | n/d | n/d | n/d |
| Premio geopolitico | n/d | -0,79 | -0,66 | -0,89 | 0,81 | -1,71 |
| Stagionalità seria | n/d | n/d | n/d | n/d | n/d | n/d |
| Posizionamento COT | n/d | -1,02 | -0,51 | 1,24 | 0,21 | -1,17 |
| Regime di volatilità | n/d | -0,26 | -0,43 | -0,67 | 0,71 | -2,64 |
| Reversal di breve | n/d | -0,26 | 0,06 | -1,19 | 1,31 | -0,85 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | n/d | n/d | n/d | n/d |
| Convinzione degli insider (Form 4) | n/d | n/d | n/d | n/d | n/d | n/d |

_Valori: Sharpe annualizzato nel regime._

## Risultati per crisi

| Episodio | Disponibile | Rend. totale | Sharpe | Max DD |
|---|---|---|---|---|
| Guerra del Golfo | no — solo 0 giorni di backtest nell'episodio (minimo 5) | — | — | — |
| Crisi finanziaria | no — solo 0 giorni di backtest nell'episodio (minimo 5) | — | — | — |
| Eccesso d'offerta OPEC | sì | 2,0% | 0,30 | -5,4% |
| Attacco ad Abqaiq | sì | -0,1% | -3,69 | -0,1% |
| Covid e WTI negativo | sì | -6,2% | -1,59 | -7,8% |
| Invasione russa dell'Ucraina | sì | 0,4% | 0,32 | -1,4% |
| Guerra USA/Israele-Iran e Hormuz | sì | 0,9% | 0,67 | -1,3% |
| Tregua di giugno-luglio 2026 | sì | -0,5% | -1,70 | -0,9% |

## Sensibilità ai costi

| Strategia | Sharpe 1x | Sharpe 2x | CAGR 2x | Pareggio (bps) |
|---|---|---|---|---|
| Master (portafoglio) | -0,71 | -0,80 | -3,1% | -19,3 |
| Momentum multi-orizzonte filtrato dal carry | 0,03 | -0,08 | -0,1% | 0,6 |
| Breakout da compressione | -0,50 | -0,61 | -0,3% | -11,6 |
| Trend adattivo (Kalman) | 0,02 | -0,00 | -0,2% | 2,1 |
| Carry e roll yield | n/d | -6,90 | -0,1% | 0,0 |
| Spread di calendario M1-M3 | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Butterfly di curva 1-3-6 | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Conferma fisica | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Brent-WTI (cointegrazione) | -0,36 | -0,39 | -0,8% | -27,3 |
| Crack spread 3-2-1 | -0,64 | -0,67 | -1,3% | -52,3 |
| Fair value macro | -0,47 | -0,48 | -0,5% | -150,4 |
| Sorpresa sulle scorte EIA | -0,60 | -0,66 | -0,3% | -25,6 |
| Playbook OPEC+ | n/d | -0,26 | -0,0% | 0,0 |
| Premio geopolitico | -0,65 | -0,67 | -1,4% | -99,3 |
| Stagionalità seria | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Posizionamento COT | -0,58 | -0,60 | -1,6% | -85,0 |
| Regime di volatilità | -0,59 | -0,67 | -0,9% | -20,5 |
| Reversal di breve | -0,09 | -0,14 | -0,1% | -4,9 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | 0,0% | n/d |
| Convinzione degli insider (Form 4) | n/d | -1,82 | -0,2% | 0,0 |

## DSR e PBO

| Strategia | Sharpe | DSR (prob.) | SR0 | Prove |
|---|---|---|---|---|
| Master (portafoglio) | -0,71 | 0,000 | 0,056 | 30 |
| Momentum multi-orizzonte filtrato dal carry | 0,03 | 0,000 | 0,056 | 30 |
| Breakout da compressione | -0,50 | 0,000 | 0,056 | 30 |
| Trend adattivo (Kalman) | 0,02 | 0,000 | 0,056 | 30 |
| Carry e roll yield | n/d | n/d | n/d | 30 |
| Spread di calendario M1-M3 | n/d | n/d | n/d | 30 |
| Butterfly di curva 1-3-6 | n/d | n/d | n/d | 30 |
| Conferma fisica | n/d | n/d | n/d | 30 |
| Brent-WTI (cointegrazione) | -0,36 | 0,000 | 0,056 | 30 |
| Crack spread 3-2-1 | -0,64 | 0,000 | 0,056 | 30 |
| Fair value macro | -0,47 | 0,000 | 0,056 | 30 |
| Sorpresa sulle scorte EIA | -0,60 | 0,000 | 0,056 | 30 |
| Playbook OPEC+ | n/d | n/d | n/d | 30 |
| Premio geopolitico | -0,65 | 0,000 | 0,056 | 30 |
| Stagionalità seria | n/d | n/d | n/d | 30 |
| Posizionamento COT | -0,58 | 0,000 | 0,056 | 30 |
| Regime di volatilità | -0,59 | 0,000 | 0,056 | 30 |
| Reversal di breve | -0,09 | 0,000 | 0,056 | 30 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | n/d | 30 |
| Convinzione degli insider (Form 4) | n/d | n/d | n/d | 30 |

PBO (CSCV, 19 varianti, 12870 combinazioni, fonte: strategie ombra usate come varianti): **0,797** · pendenza IS→OOS -1,020 · probabilità di Sharpe OOS negativo 0,799

## Ciclo di vita

| Strategia | Stato | Peso | Spiegazione |
|---|---|---|---|
| Momentum multi-orizzonte filtrato dal carry | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,08 < 0 |
| Breakout da compressione | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,61 < 0 |
| Trend adattivo (Kalman) | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,00 < 0 |
| Carry e roll yield | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -6,90 < 0 |
| Spread di calendario M1-M3 | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d |
| Butterfly di curva 1-3-6 | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d |
| Conferma fisica | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d |
| Brent-WTI (cointegrazione) | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,39 < 0 |
| Crack spread 3-2-1 | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,67 < 0 |
| Fair value macro | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,48 < 0 |
| Sorpresa sulle scorte EIA | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,66 < 0 |
| Playbook OPEC+ | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,26 < 0 |
| Premio geopolitico | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,67 < 0 |
| Stagionalità seria | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d |
| Posizionamento COT | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,60 < 0 |
| Regime di volatilità | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,67 < 0 |
| Reversal di breve | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,14 < 0 |
| Opzioni sintetiche (approssimazione) | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d |
| Convinzione degli insider (Form 4) | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -1,82 < 0 |

Strategie a peso zero:

- **Momentum multi-orizzonte filtrato dal carry**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,08 < 0
- **Breakout da compressione**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,61 < 0
- **Trend adattivo (Kalman)**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,00 < 0
- **Carry e roll yield**: Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -6,90 < 0
- **Spread di calendario M1-M3**: Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d
- **Butterfly di curva 1-3-6**: Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d
- **Conferma fisica**: Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d
- **Brent-WTI (cointegrazione)**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,39 < 0
- **Crack spread 3-2-1**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,67 < 0
- **Fair value macro**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,48 < 0
- **Sorpresa sulle scorte EIA**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,66 < 0
- **Playbook OPEC+**: Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,26 < 0
- **Premio geopolitico**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,67 < 0
- **Stagionalità seria**: Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d
- **Posizionamento COT**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,60 < 0
- **Regime di volatilità**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,67 < 0
- **Reversal di breve**: Peso zero: DSR 0,00 < 0,95; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -0,14 < 0
- **Opzioni sintetiche (approssimazione)**: Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi n/d
- **Convinzione degli insider (Form 4)**: Peso zero: DSR n/d; PBO 0,80 > 0,5; Sharpe OOS a costi doppi -1,82 < 0

## Istogramma della leva

Leva media 0,11x · mediana 0,06x · p95 0,42x · massima 0,96x · giorni sopra 1x: 0 su 3753

| Fascia | Giorni | Quota |
|---|---|---|
| 0-0,25x | 3276 | 87,3% |
| 0,25-0,5x | 389 | 10,4% |
| 0,5-0,75x | 75 | 2,0% |
| 0,75-1x | 13 | 0,3% |
| 1-1,5x | 0 | 0,0% |
| 1,5-2x | 0 | 0,0% |
| 2-3x | 0 | 0,0% |
| 3-5x | 0 | 0,0% |
| 5-10x | 0 | 0,0% |

## Stress test

Posizione testata: long 1x su 10.000 $ · fonte prezzi: brent_spot (n/d) (asof 2026-10-14)

| Scenario | Rend. totale | Max DD | Peggior seduta | Margin call | Liquidazione |
|---|---|---|---|---|---|
| Guerra del Golfo | -13,1% | 57,3% | -30,3% | no | no |
| Crisi finanziaria | -68,4% | 76,6% | -15,5% | no | no |
| Eccesso d'offerta OPEC | -67,1% | 77,4% | -7,8% | no | no |
| Attacco ad Abqaiq | -3,4% | 15,3% | -4,1% | no | no |
| Covid e WTI negativo | -38,3% | 84,7% | -47,5% | no | no |
| Invasione russa dell'Ucraina | 12,7% | 26,5% | -12,5% | no | no |
| Guerra USA/Israele-Iran e Hormuz | 63,8% | 50,4% | -15,4% | no | no |
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
- DSR non calcolato per S7: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S12: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S14: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S18: Sharpe non definito (serie piatta o troppo corta)
- DSR non calcolato per S21: Sharpe non definito (serie piatta o troppo corta)
