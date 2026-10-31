# Report di backtest

Generato il 2026-10-31T10:13:08.309093Z · finestra 2011-10-31 → 2026-10-30 (14,9 anni, 3752 osservazioni) · capitale iniziale 10000 $

_Simulazione a scopo di studio: paper trading, nessun consiglio finanziario._

## Metriche per strategia

| Strategia | Sharpe | Sortino | CAGR | Vol | Max DD | Hit rate | PSR | Oper. | Peso |
|---|---|---|---|---|---|---|---|---|---|
| Master (portafoglio) | -0,70 | -0,85 | -2,7% | 3,8% | -37,9% | 29,4% | 0,00 | 840 | 1,000 |
| Momentum multi-orizzonte filtrato dal carry | 0,08 | 0,11 | 0,1% | 1,0% | -3,9% | 11,0% | 0,62 | 276 | 0,000 |
| Breakout da compressione | -0,43 | -0,55 | -0,2% | 0,5% | -4,2% | 1,1% | 0,04 | 106 | 0,000 |
| Trend adattivo (Kalman) | 0,03 | 0,04 | -0,0% | 5,7% | -24,9% | 35,1% | 0,54 | 932 | 0,000 |
| Carry e roll yield | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Spread di calendario M1-M3 | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Butterfly di curva 1-3-6 | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Conferma fisica | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Brent-WTI (cointegrazione) | -0,34 | -0,38 | -0,7% | 1,9% | -12,3% | 4,8% | 0,04 | 247 | 0,000 |
| Crack spread 3-2-1 | -0,69 | -0,88 | -1,3% | 2,0% | -22,2% | 7,5% | 0,00 | 536 | 0,000 |
| Fair value macro | -0,47 | -0,58 | -0,4% | 0,9% | -7,8% | 1,6% | 0,03 | 83 | 0,000 |
| Sorpresa sulle scorte EIA | -0,59 | -0,70 | -0,2% | 0,4% | -3,4% | 1,3% | 0,01 | 134 | 0,000 |
| Playbook OPEC+ | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Premio geopolitico | -0,65 | -0,86 | -1,3% | 2,0% | -23,5% | 6,4% | 0,01 | 568 | 0,000 |
| Stagionalità seria | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Posizionamento COT | -0,55 | -0,73 | -1,4% | 2,5% | -20,5% | 8,7% | 0,02 | 404 | 0,000 |
| Regime di volatilità | -0,61 | -0,73 | -0,9% | 1,4% | -15,1% | 2,8% | 0,00 | 271 | 0,000 |
| Reversal di breve | -0,14 | -0,20 | -0,1% | 0,9% | -2,9% | 1,9% | 0,29 | 152 | 0,000 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |
| Convinzione degli insider (Form 4) | n/d | n/d | 0,0% | 0,0% | 0,0% | 0,0% | n/d | 0 | 0,000 |

## Risultati per regime

| Strategia | Backwardation + vol alta + rischio geopolitico | Contango + eccesso d'offerta + trend ribassista | Range a bassa volatilità | Shock/crash | Squeeze rialzista | Transizione |
|---|---|---|---|---|---|---|
| Master (portafoglio) | -1,65 | -1,64 | -0,39 | -0,69 | 1,23 | -3,41 |
| Momentum multi-orizzonte filtrato dal carry | -1,60 | 0,28 | -0,17 | 1,28 | 1,12 | -1,29 |
| Breakout da compressione | n/d | -0,68 | -0,31 | n/d | -0,16 | -1,12 |
| Trend adattivo (Kalman) | -2,00 | -0,66 | -0,06 | 0,76 | 1,39 | -0,30 |
| Carry e roll yield | n/d | n/d | n/d | n/d | n/d | n/d |
| Spread di calendario M1-M3 | n/d | n/d | n/d | n/d | n/d | n/d |
| Butterfly di curva 1-3-6 | n/d | n/d | n/d | n/d | n/d | n/d |
| Conferma fisica | n/d | n/d | n/d | n/d | n/d | n/d |
| Brent-WTI (cointegrazione) | 2,10 | -0,31 | -0,31 | -0,83 | 0,33 | -0,79 |
| Crack spread 3-2-1 | -3,36 | -1,13 | -0,73 | 0,30 | 0,71 | -1,93 |
| Fair value macro | n/d | -0,16 | 0,18 | -2,09 | 0,12 | -0,71 |
| Sorpresa sulle scorte EIA | n/d | 0,14 | -0,68 | -0,45 | -1,48 | 0,98 |
| Playbook OPEC+ | n/d | n/d | n/d | n/d | n/d | n/d |
| Premio geopolitico | -0,70 | -1,11 | -0,39 | -0,52 | -0,69 | -1,18 |
| Stagionalità seria | n/d | n/d | n/d | n/d | n/d | n/d |
| Posizionamento COT | 0,14 | -0,91 | -0,40 | -0,22 | -0,29 | -0,96 |
| Regime di volatilità | n/d | -0,28 | -0,57 | -0,22 | 0,02 | -2,42 |
| Reversal di breve | n/d | -0,40 | 0,46 | -1,20 | -1,79 | -1,12 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | n/d | n/d | n/d | n/d |
| Convinzione degli insider (Form 4) | n/d | n/d | n/d | n/d | n/d | n/d |

_Valori: Sharpe annualizzato nel regime._

## Risultati per crisi

| Episodio | Disponibile | Rend. totale | Sharpe | Max DD |
|---|---|---|---|---|
| Guerra del Golfo | no — solo 0 giorni di backtest nell'episodio (minimo 5) | — | — | — |
| Crisi finanziaria | no — solo 0 giorni di backtest nell'episodio (minimo 5) | — | — | — |
| Eccesso d'offerta OPEC | sì | 2,6% | 0,39 | -5,7% |
| Attacco ad Abqaiq | sì | -0,1% | -3,69 | -0,1% |
| Covid e WTI negativo | sì | -6,0% | -1,56 | -7,6% |
| Invasione russa dell'Ucraina | sì | 0,7% | 0,62 | -1,4% |
| Guerra USA/Israele-Iran e Hormuz | sì | 0,8% | 0,58 | -1,3% |
| Tregua di giugno-luglio 2026 | sì | -0,5% | -1,70 | -0,9% |

## Sensibilità ai costi

| Strategia | Sharpe 1x | Sharpe 2x | CAGR 2x | Pareggio (bps) |
|---|---|---|---|---|
| Master (portafoglio) | -0,70 | -0,79 | -3,0% | -19,2 |
| Momentum multi-orizzonte filtrato dal carry | 0,08 | -0,03 | -0,0% | 1,9 |
| Breakout da compressione | -0,43 | -0,54 | -0,3% | -10,5 |
| Trend adattivo (Kalman) | 0,03 | 0,00 | -0,1% | 2,8 |
| Carry e roll yield | n/d | -6,85 | -0,1% | 0,0 |
| Spread di calendario M1-M3 | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Butterfly di curva 1-3-6 | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Conferma fisica | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Brent-WTI (cointegrazione) | -0,34 | -0,37 | -0,7% | -25,5 |
| Crack spread 3-2-1 | -0,69 | -0,72 | -1,4% | -55,2 |
| Fair value macro | -0,47 | -0,48 | -0,4% | -131,2 |
| Sorpresa sulle scorte EIA | -0,59 | -0,65 | -0,2% | -25,2 |
| Playbook OPEC+ | n/d | -0,30 | -0,0% | 0,0 |
| Premio geopolitico | -0,65 | -0,66 | -1,4% | -96,3 |
| Stagionalità seria | n/d — serie di turnover non ricostruibile: costi x2 non calcolabili | n/d | n/d | n/d |
| Posizionamento COT | -0,55 | -0,57 | -1,5% | -79,4 |
| Regime di volatilità | -0,61 | -0,69 | -1,0% | -20,0 |
| Reversal di breve | -0,14 | -0,20 | -0,2% | -6,5 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | 0,0% | n/d |
| Convinzione degli insider (Form 4) | n/d | -2,20 | -0,2% | 0,0 |

## DSR e PBO

| Strategia | Sharpe | DSR (prob.) | SR0 | Prove |
|---|---|---|---|---|
| Master (portafoglio) | -0,70 | 0,000 | 0,056 | 30 |
| Momentum multi-orizzonte filtrato dal carry | 0,08 | 0,001 | 0,056 | 30 |
| Breakout da compressione | -0,43 | 0,000 | 0,056 | 30 |
| Trend adattivo (Kalman) | 0,03 | 0,000 | 0,056 | 30 |
| Carry e roll yield | n/d | n/d | n/d | 30 |
| Spread di calendario M1-M3 | n/d | n/d | n/d | 30 |
| Butterfly di curva 1-3-6 | n/d | n/d | n/d | 30 |
| Conferma fisica | n/d | n/d | n/d | 30 |
| Brent-WTI (cointegrazione) | -0,34 | 0,000 | 0,056 | 30 |
| Crack spread 3-2-1 | -0,69 | 0,000 | 0,056 | 30 |
| Fair value macro | -0,47 | 0,000 | 0,056 | 30 |
| Sorpresa sulle scorte EIA | -0,59 | 0,000 | 0,056 | 30 |
| Playbook OPEC+ | n/d | n/d | n/d | 30 |
| Premio geopolitico | -0,65 | 0,000 | 0,056 | 30 |
| Stagionalità seria | n/d | n/d | n/d | 30 |
| Posizionamento COT | -0,55 | 0,000 | 0,056 | 30 |
| Regime di volatilità | -0,61 | 0,000 | 0,056 | 30 |
| Reversal di breve | -0,14 | 0,000 | 0,056 | 30 |
| Opzioni sintetiche (approssimazione) | n/d | n/d | n/d | 30 |
| Convinzione degli insider (Form 4) | n/d | n/d | n/d | 30 |

PBO (CSCV, 19 varianti, 12870 combinazioni, fonte: strategie ombra usate come varianti): **0,742** · pendenza IS→OOS -0,781 · probabilità di Sharpe OOS negativo 0,751

## Ciclo di vita

| Strategia | Stato | Peso | Spiegazione |
|---|---|---|---|
| Momentum multi-orizzonte filtrato dal carry | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,03 < 0 |
| Breakout da compressione | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,54 < 0 |
| Trend adattivo (Kalman) | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5 |
| Carry e roll yield | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -6,85 < 0 |
| Spread di calendario M1-M3 | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d |
| Butterfly di curva 1-3-6 | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d |
| Conferma fisica | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d |
| Brent-WTI (cointegrazione) | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,37 < 0 |
| Crack spread 3-2-1 | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,72 < 0 |
| Fair value macro | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,48 < 0 |
| Sorpresa sulle scorte EIA | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,65 < 0 |
| Playbook OPEC+ | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,30 < 0 |
| Premio geopolitico | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,66 < 0 |
| Stagionalità seria | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d |
| Posizionamento COT | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,57 < 0 |
| Regime di volatilità | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,69 < 0 |
| Reversal di breve | incubation | 0,000 | Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,20 < 0 |
| Opzioni sintetiche (approssimazione) | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d |
| Convinzione degli insider (Form 4) | incubation | 0,000 | Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -2,20 < 0 |

Strategie a peso zero:

- **Momentum multi-orizzonte filtrato dal carry**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,03 < 0
- **Breakout da compressione**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,54 < 0
- **Trend adattivo (Kalman)**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5
- **Carry e roll yield**: Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -6,85 < 0
- **Spread di calendario M1-M3**: Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d
- **Butterfly di curva 1-3-6**: Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d
- **Conferma fisica**: Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d
- **Brent-WTI (cointegrazione)**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,37 < 0
- **Crack spread 3-2-1**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,72 < 0
- **Fair value macro**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,48 < 0
- **Sorpresa sulle scorte EIA**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,65 < 0
- **Playbook OPEC+**: Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,30 < 0
- **Premio geopolitico**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,66 < 0
- **Stagionalità seria**: Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d
- **Posizionamento COT**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,57 < 0
- **Regime di volatilità**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,69 < 0
- **Reversal di breve**: Peso zero: DSR 0,00 < 0,95; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -0,20 < 0
- **Opzioni sintetiche (approssimazione)**: Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi n/d
- **Convinzione degli insider (Form 4)**: Peso zero: DSR n/d; PBO 0,74 > 0,5; Sharpe OOS a costi doppi -2,20 < 0

## Istogramma della leva

Leva media 0,10x · mediana 0,06x · p95 0,41x · massima 0,96x · giorni sopra 1x: 0 su 3753

| Fascia | Giorni | Quota |
|---|---|---|
| 0-0,25x | 3273 | 87,2% |
| 0,25-0,5x | 399 | 10,6% |
| 0,5-0,75x | 71 | 1,9% |
| 0,75-1x | 10 | 0,3% |
| 1-1,5x | 0 | 0,0% |
| 1,5-2x | 0 | 0,0% |
| 2-3x | 0 | 0,0% |
| 3-5x | 0 | 0,0% |
| 5-10x | 0 | 0,0% |

## Stress test

Posizione testata: long 1x su 10.000 $ · fonte prezzi: brent_spot (n/d) (asof 2026-10-27)

| Scenario | Rend. totale | Max DD | Peggior seduta | Margin call | Liquidazione |
|---|---|---|---|---|---|
| Guerra del Golfo | -13,1% | 57,3% | -30,3% | no | no |
| Crisi finanziaria | -68,4% | 76,6% | -15,5% | no | no |
| Eccesso d'offerta OPEC | -67,1% | 77,4% | -7,8% | no | no |
| Attacco ad Abqaiq | -3,4% | 15,3% | -4,1% | no | no |
| Covid e WTI negativo | -38,3% | 84,7% | -47,5% | no | no |
| Invasione russa dell'Ucraina | 12,7% | 26,5% | -12,5% | no | no |
| Guerra USA/Israele-Iran e Hormuz | 59,4% | 50,4% | -15,4% | no | no |
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
