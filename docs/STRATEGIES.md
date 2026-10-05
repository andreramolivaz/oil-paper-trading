# Universo delle strategie

Ogni strategia ha un conto ombra da 10.000 $ (sempre a leva ≤ 1x) usato per la classifica, e un ciclo di vita:
**ricerca → incubazione (peso zero nel master) → attiva → ritirata** (decadimento rilevato con CUSUM sulla
performance live). Il passaggio a "attiva" richiede: Deflated Sharpe Ratio > 0 sull'insieme dei tentativi,
PBO < 50%, Sharpe out-of-sample positivo dopo costi raddoppiati, e almeno 100 operazioni o 3 anni di storia.
Le strategie che non superano i test **restano a peso zero** e la dashboard lo dice.

Convenzioni comuni: segnale = direzione, probabilità calibrata, rendimento e volatilità attesi sull'orizzonte, stop,
motivazione in italiano. I parametri sono in `config/strategies.yaml`. Tutte le feature sono point-in-time
(`engine/features/catalog.py`). Le serie di prezzo per i segnali sono roll-adjusted; il P&L usa il contratto reale.

Stato di validazione: vedi `docs/VALIDATION.md` (generato dalla fase 6) e la pagina **Strategie** della dashboard.

---

## Trend e momentum

### S1 · Momentum multi-orizzonte filtrato dal carry
- **Tesi.** I flussi di hedging dei produttori e consumatori sono lenti e prevedibili; le notizie fondamentali
  (OPEC, scorte, guerre) si propagano nei prezzi nell'arco di settimane. Il time-series momentum su 1–12 mesi cattura
  questa diffusione. Il carry (struttura della curva) è il filtro: un trend rialzista in backwardation è "pagato"
  (roll yield positivo) ed è coerente con scorte basse; un trend rialzista in contango lotta contro il carry.
- **Chi perde.** Hedger che vendono forward indipendentemente dal prezzo; investitori mean-reversion precoci;
  chi ribilancia meccanicamente contro il trend (indici commodity a pesi fissi).
- **Segnale.** Media dei segni di `tsmom_10..252` pesati per orizzonte, vol-scaled (`rv_yz_21`). Size piena se
  `sign(trend) == sign(slope_m1_m6)`, dimezzata se divergono, zero se `cot_crowding` > 0,9 nella stessa direzione.
- **Regimi favorevoli.** Trend persistenti (`hurst_100` > 0,55, `vr_20` > 1): "Contango + eccesso d'offerta +
  trend ribassista", "Squeeze rialzista". Sfavorevoli: "Range a bassa volatilità", "Shock/crash" (whipsaw).
- **Invalidazione.** Hurst < 0,45 per 60 giorni; inversione del segno dello slope senza inversione del trend;
  Sharpe rolling a 2 anni < 0 dopo costi.

### S2 · Breakout da compressione
- **Tesi.** La volatilità è ciclica: a periodi di compressione (vol realizzata nel decile basso) seguono espansioni.
  Quando il prezzo esce da un canale Donchian/ATR in regime compresso, l'informazione nuova non è ancora prezzata e
  gli stop di chi era in range alimentano il movimento.
- **Chi perde.** Venditori di volatilità e strategie range-bound che restano corte gamma oltre la rottura.
- **Segnale.** `vol_pctl_1y` < 0,25 nei 10 giorni precedenti **e** `donchian_pos_55` fuori da [0,1] (chiusura
  oltre il canale), conferma da volume > media 20g se disponibile. Stop a 2 ATR, trailing a 3 ATR.
- **Regimi favorevoli.** "Range a bassa volatilità" in uscita. Sfavorevoli: vol già alta (`vol_pctl_1y` > 0,7).
- **Invalidazione.** Tre falsi breakout consecutivi; hit rate < 35% su 50 operazioni.

### S3 · Trend adattivo (Kalman)
- **Tesi.** Un filtro di Kalman sul livello e sulla pendenza del prezzo stima il trend con rumore di osservazione
  che cresce con la volatilità: in regimi rumorosi reagisce lentamente, in regimi ordinati rapidamente. Riduce i
  whipsaw dei trend classici con finestre fisse.
- **Chi perde.** Trend follower a finestra fissa che vengono stoppati dal rumore; contrarian che combattono trend
  ordinati.
- **Segnale.** Pendenza filtrata / sua deviazione standard > soglia; rapporto segnale/rumore (Q/R) scalato con
  `rv_yz_21` e con la confidenza del regime. Size ∝ |pendenza normalizzata|, vol-scaled.
- **Regimi favorevoli.** Trend ordinati; neutro in transizione. Sfavorevoli: "Shock/crash".
- **Invalidazione.** Pendenza che cambia segno più di 6 volte in 60 giorni.

## Struttura a termine e carry

### S4 · Carry e roll yield
- **Tesi.** La backwardation segnala scorte basse e convenience yield alto (teoria dello storage): comprare il
  front e rollare incassa il roll yield, e le scorte basse rendono i prezzi fragili al rialzo. Il contango segnala
  eccesso d'offerta e paga per stare corti. Il carry è uno dei pochi premi al rischio documentati nelle commodity.
- **Chi perde.** Index investor long-only che rollano in contango; produttori che vendono differito in backwardation.
- **Segnale.** `roll_yield_ann` e il suo percentile 5 anni `roll_yield_pctl`: long se > 60°, short se < 40°,
  size ∝ |percentile − 0,5|, vol-scaled. Se la curva è proxy (`curve_approx` = 1) la size è dimezzata.
- **Regimi favorevoli.** Entrambi i regimi strutturali (backwardation con scorte basse, contango con glut).
  Sfavorevoli: "Shock/crash" (curva disordinata).
- **Invalidazione.** Correlazione roll yield–rendimento successivo < 0 su 3 anni rolling.

### S5 · Spread di calendario
- **Tesi.** Gli spread M1–M2, M1–M3 e Dec–Dec riflettono la tensione fisica pronta. Quando la stretta si consolida
  (spread che si allarga con scorte in calo e premio spot) lo spread ha momentum; dopo i picchi da evento (notizia
  geopolitica) il front torna indietro più del differito: mean reversion.
- **Chi perde.** Chi copre esposizioni fisiche all'ultimo momento (paga il pronto); speculatori che inseguono il
  picco del front.
- **Segnale.** Momentum dello spread (`spread_chg_5` concorde con `crude_stocks_vs_5y` < 0 e `spot_front_premium` > 0)
  → long spread (long M1 / short M3). Z-score dello spread > 2,5 in concomitanza con `geo_spike` = 1 e scorte
  non in calo → short spread (mean reversion). Posizione multi-gamba, beta flat basso.
- **Regimi favorevoli.** "Backwardation + vol alta + rischio geopolitico" (entrambe le gambe della logica).
- **Invalidazione.** Curva disponibile solo come proxy (nessuna operazione finché non c'è curva Brent reale per
  almeno 120 giorni: la strategia parte in incubazione).

### S6 · Butterfly di curva
- **Tesi.** La curvatura (M1 − 2·M3 + M6) è dominata da flussi tecnici (roll degli indici, hedge delle raffinerie
  su scadenze specifiche) e torna verso la forma di equilibrio: relative value a basso beta sul prezzo flat.
- **Chi perde.** Flussi di roll calendarizzati e hedger vincolati a una scadenza.
- **Segnale.** Z-score 1 anno di `butterfly_1_3_6`; entra oltre ±2, esce a 0,5; half-life stimata OU deve essere
  < 20 giorni. Tre gambe (+1, −2, +1).
- **Regimi favorevoli.** Qualsiasi regime con curva reale e liquida; sfavorevole: "Shock/crash".
- **Invalidazione.** Half-life > 40 giorni o curva proxy. Parte in incubazione fino a 120 giorni di curva reale.

### S7 · Conferma fisica
- **Tesi.** Un rally del flat price con spread fermi è premio speculativo/finanziario: tende a sgonfiarsi. Un rally
  con spread che si allargano insieme è una stretta fisica reale: va seguito. È anche la condizione 4 del gate
  della leva (§9) per i long sui picchi geopolitici.
- **Chi perde.** Chi compra il titolo di giornale senza guardare la curva; chi vende la stretta vera troppo presto.
- **Segnale.** `ret_5` > +1,5σ: se `spread_chg_5` ≤ 0 e `spot_front_premium` non sale → short (fade) con stop
  stretto; se `spread_chg_5` > 0 e `cushing_vs_5y` < 0 → long (follow). Specchio per i ribassi.
- **Regimi favorevoli.** "Backwardation + vol alta + rischio geopolitico". Sfavorevole: "Range a bassa volatilità".
- **Invalidazione.** Curva proxy (nessun fade senza spread reali).

## Inter-market e relative value

### S8 · Brent–WTI
- **Tesi.** Il differenziale è ancorato dall'arbitraggio dell'export USA (costo di trasporto Cushing–costa–Europa) e
  si allontana con scorte di Cushing estreme, colli di bottiglia logistici e premio geopolitico mediorientale
  (che colpisce il Brent più del WTI). Cointegrazione con z-score e soglie dipendenti dal regime.
- **Chi perde.** Chi tratta i due greggi come identici; esportatori/importatori con hedge rigidi.
- **Segnale.** Residuo della regressione rolling (3 anni) di Brent su WTI con driver `cushing_vs_5y`, `geo_index`,
  `crude_exports`: `brent_wti_z` oltre ±2 (±2,5 in regime geopolitico) → mean reversion sullo spread; uscita a 0,5.
- **Regimi favorevoli.** Tutti tranne "Shock/crash" (cointegrazione instabile).
- **Invalidazione.** Test di Johansen/ADF sul residuo non significativo per 2 finestre consecutive.

### S9 · Crack spread
- **Tesi.** I prodotti guidano il greggio quando la domanda finale sorprende: un rally dei crack (RBOB/HO vs greggio)
  anticipa l'aumento delle lavorazioni e della domanda di greggio. I crack estremi tornano verso i costi di
  raffinazione perché i margini attirano/espellono capacità rapidamente.
- **Chi perde.** Raffinatori con hedge statici; trader di greggio che ignorano i prodotti.
- **Segnale.** `product_lead` (prodotti − greggio a 5g) > 1σ → long greggio a 1 settimana; `crack_321_z` > 2,5 →
  short crack (short prodotti / long greggio), < −2,5 → long crack. Crack del diesel come conferma per il Brent.
- **Regimi favorevoli.** Stagione di guida e manutenzioni (crack volatili); sfavorevole: "Shock/crash".
- **Invalidazione.** Lead-lag (correlazione `product_lead` con `ret_5` successivo) non positiva su 3 anni.

### S10 · Fair value macro
- **Tesi.** Il petrolio ha beta variabili nel tempo verso dollaro, tassi reali, azionario e rame (domanda globale).
  Un filtro di Kalman stima i beta; quando il prezzo si scosta dal fair value macro senza conferma fondamentale
  (scorte, curva) tende a tornare. In regime geopolitico il residuo è dominato dal premio al rischio: si opera solo
  se il residuo non è spiegato dal `geo_index`.
- **Chi perde.** Flussi macro indiscriminati (risk-on/risk-off) che trascinano il petrolio fuori dai fondamentali.
- **Segnale.** `macro_fv_z` oltre ±2 e `geo_spike` = 0 e scorte non estreme → mean reversion verso il fair value.
- **Regimi favorevoli.** "Range a bassa volatilità", "Contango + eccesso d'offerta". Sfavorevole: geopolitico.
- **Invalidazione.** R² del modello < 0,2 su 2 anni rolling.

## Fondamentali ed eventi

### S11 · Sorpresa sulle scorte EIA
- **Tesi.** Il mercato reagisce alla sorpresa rispetto alle attese, non al livello. Non avendo il consenso, lo
  modelliamo: variazione attesa = stagionalità (media 5 anni della settimana) + tendenza recente + aggiustamento per
  import/lavorazioni. La sorpresa (specie su Cushing e sulla domanda implicita) genera un drift di 1–3 giorni perché
  i fondi aggiustano gradualmente.
- **Chi perde.** Chi tratta solo sul numero del titolo (crude) ignorando prodotti, Cushing e domanda implicita.
- **Segnale.** Mercoledì dopo le 10:30 ET: `stock_surprise_z` combinato (crude 40%, Cushing 25%, domanda implicita
  25%, lavorazioni 10%): sotto −1 → long 1–3 giorni; sopra +1 → short. Size ∝ |z|, cap 1x.
- **Regimi favorevoli.** Mercati guidati dai fondamentali; in regime geopolitico il peso è dimezzato.
- **Invalidazione.** Accuratezza direzionale post-report < 50% su 52 settimane (test binomiale).

### S12 · Playbook OPEC+
- **Tesi.** Prima delle riunioni la volatilità implicita sale e il mercato si posiziona; il drift post-decisione
  dura 2–5 giorni perché le dichiarazioni vengono digerite (quote effettive vs annunciate, compliance). La direzione
  pre-evento segue la tendenza del posizionamento e della curva, ma con leva ridotta per il rischio binario.
- **Chi perde.** Chi mantiene leva piena attraverso l'evento; chi inverte sulla prima reazione.
- **Segnale.** Da `config/events.yaml`: nei 2 giorni prima, posizione ridotta nella direzione del trend 21g
  (`tsmom_21`) se concorde con lo slope; nei 3 giorni dopo, segue il segno del rendimento del giorno dell'evento
  se > 1σ (drift). La leva master è tagliata da `L_evento`.
- **Regimi favorevoli.** Tutti; la componente drift è più forte in "Contango + eccesso d'offerta".
- **Invalidazione.** Drift post-evento non significativo su 12 riunioni consecutive.

### S13 · Premio geopolitico
- **Tesi.** I picchi di notizie (Iran, Hormuz, Houthi, sanzioni) creano un premio al rischio immediato con momentum
  iniziale di 1–3 giorni; se la curva e il fisico (spread, premio spot) non confermano l'interruzione reale
  dell'offerta, il premio si sgonfia nelle 1–3 settimane successive. Regole asimmetriche: entrata rapida sul picco,
  uscita immediata sulle notizie di de-escalation (il mercato scende più in fretta di quanto sale).
- **Chi perde.** Chi compra il picco e lo tiene senza conferma fisica; chi è corto durante l'escalation con stop
  troppo stretti.
- **Segnale.** `geo_spike` = 1 → long 2 giorni con stop 1,5 ATR. Dopo 3 giorni: se `spread_chg_5` ≤ 0 e
  `spot_front_premium` non sale → short (fade) fino a `geo_index` sotto la media 20g; `deescalation_flag` = 1 →
  chiudi ogni long e valuta short a 3 giorni.
- **Regimi favorevoli.** "Backwardation + vol alta + rischio geopolitico". Sfavorevole: "Range a bassa volatilità".
- **Invalidazione.** Ritorno medio post-picco non significativo su 30 episodi.

### S14 · Stagionalità seria
- **Tesi.** Manutenzioni di raffineria (feb–mar, set–ott), driving season (mag–ago), uragani (ago–ott), domanda di
  riscaldamento (nov–feb) creano pattern di scorte e crack. Solo gli effetti che superano test out-of-sample con
  correzione per test multipli vengono usati, e solo come **inclinazione** (tilt ±25% della size) mai da soli.
- **Chi perde.** Nessuno in modo diretto: è un premio di rischio stagionale pagato da chi deve coprirsi in date fisse.
- **Segnale.** Rendimento medio atteso per settimana dell'anno su 20 anni, filtrato per significatività
  (t > 2 dopo Bonferroni) → tilt sulle altre strategie; mai posizione autonoma.
- **Regimi favorevoli.** Mercati guidati dai fondamentali. Disattivato in "Shock/crash" e geopolitico.
- **Invalidazione.** Perdita di significatività nella finestra più recente (10 anni).

### S15 · Posizionamento COT
- **Tesi.** I managed money ai massimi di net length (percentile > 90% a 3 anni) con trend in esaurimento (slope del
  trend che si appiattisce, `vr_5` < 1) segnalano affollamento: lo sbilancio deve essere smontato. Vale anche come
  filtro: riduce le strategie trend quando sono affollate.
- **Chi perde.** L'ultimo entrato nel trend; i fondi che devono liquidare simultaneamente.
- **Segnale.** `cot_mm_net_brent_pctl` > 0,9 e `tsmom_21` in calo → short contrarian a 2 settimane; specchio sotto
  0,1. Filtro di affollamento `cot_crowding` applicato a S1–S3.
- **Regimi favorevoli.** Fine di trend lunghi; sfavorevole: "Shock/crash" (il posizionamento si azzera comunque).
- **Invalidazione.** Rendimento post-estremo non significativo su 40 episodi.

## Volatilità

### S16 · Regime di volatilità
- **Tesi.** Il premio per il rischio di volatilità (OVX − realizzata) è alto quando il mercato teme eventi: senza un
  evento in calendario i movimenti tendono a rientrare (mean reversion di 1–5 giorni). Quando la volatilità è compressa
  (OVX e realizzata entrambe basse) i breakout sono più affidabili: il regime decide la famiglia da favorire.
- **Chi perde.** Compratori di protezione sistematici (overpay); venditori di vol prima degli eventi.
- **Segnale.** `vrp` > 70° percentile e `hours_to_event` > 72 → mean reversion su `ret_1` oltre ±1,5σ; `vrp` <
  30° e `vol_pctl_1y` < 0,2 → abilita S2 con size piena. Opera direttamente sul future (nessuna opzione).
- **Regimi favorevoli.** "Backwardation + vol alta" per la mean reversion; "Range a bassa volatilità" per il breakout.
- **Invalidazione.** OVX non disponibile (prima del 2007 il conto ombra non opera).

### S17 · Reversal di breve
- **Tesi.** Nei regimi rumorosi ad alta volatilità (Hurst < 0,5) i movimenti di 1–5 giorni sono in parte liquidità e
  stop, e rientrano. Un processo OU stimato sui rendimenti a 5 giorni fornisce half-life e soglie; si opera solo in
  assenza di conferma fondamentale (nessuna sorpresa EIA, nessun picco di notizie).
- **Chi perde.** Chi insegue i movimenti intraday; stop forzati di posizioni a leva.
- **Segnale.** `hurst_100` < 0,5, `ret_5` oltre ±2σ (vol-scaled), `stock_surprise_z` neutro, `geo_spike` = 0 →
  posizione contraria con orizzonte = half-life OU (cap 5 giorni), stop 1,5σ.
- **Regimi favorevoli.** "Shock/crash" e geopolitico (con size ridotta). Sfavorevoli: trend ordinati.
- **Invalidazione.** Hurst > 0,55 persistente o half-life > 10 giorni.

### S18 · Opzioni sintetiche (approssimazione, facoltativa)
- **Tesi.** Prima di eventi binari (OPEC+, grandi report) la volatilità implicita è spesso inferiore a quella
  realizzata nel giorno dell'evento: un long straddle sintetico (Black-76 sul future con vol da OVX) guadagna se il
  movimento supera il premio. Con premio alto (OVX ≫ GARCH), strutture a rischio definito (iron condor sintetico).
- **Chi perde.** Chi vende volatilità a sconto prima degli eventi; chi la compra cara dopo.
- **Segnale.** `hours_to_event` < 48 e OVX < vol GARCH prevista → long straddle sintetico; OVX > GARCH·1,4 e nessun
  evento → short strangle a rischio definito. Prezzi dall'OVX con ipotesi di skew dichiarata; **etichettata approx**.
  Resta a peso zero nel master salvo validazione.
- **Regimi favorevoli.** Pre-evento; "Range a bassa volatilità" per la vendita di premio.
- **Invalidazione.** Senza dati di opzioni reali resta un'approssimazione: mai peso > 0 nel master.

## Machine learning e meta-livello

### S19 · Meta-labeling
- **Tesi.** Le strategie a regole hanno un buon richiamo ma precisione modesta. Un gradient boosting addestrato con
  triple-barrier labeling (stop, target, scadenza) sui segnali primari stima P(profitto) dalle feature di contesto
  (regime, vol, curva, COT, notizie) e la usa per la size. CV purged con embargo evita il leakage temporale; SHAP
  spiega la size scelta.
- **Chi perde.** Nessuno direttamente: migliora la precisione delle altre strategie.
- **Segnale.** Per ogni segnale primario: `prob` sostituita dalla probabilità calibrata del modello (isotonic);
  size = 0 se prob < 0,5. Retraining settimanale, walk-forward; modello versionato.
- **Regimi favorevoli.** Tutti; l'utilità dipende dalla quantità di segnali storici (richiede ≥ 300 segnali).
- **Invalidazione.** Brier score peggiore della calibrazione ingenua (0,5) su 6 mesi live.

### S20 · Allocatore regime-condizionato
- **Tesi.** La performance delle strategie dipende dal regime; i pesi devono adattarsi online. Hedge (pesi
  moltiplicativi) sulla performance out-of-sample nel regime corrente, con shrinkage verso il risk parity (umiltà) e
  penalità di correlazione (diversificazione reale). Decide anche l'ingresso nel gate "alpha forte".
- **Chi perde.** Nessuno direttamente: è il meta-livello del portafoglio.
- **Segnale.** Pesi w_i ∝ exp(η · Sharpe OOS nel regime) · shrink verso 1/vol_i, penalità per |ρ| > 0,5; aggiornamento
  settimanale; turnover limitato da isteresi.
- **Regimi favorevoli.** Tutti. In "Transizione" i pesi collassano verso il risk parity e la leva verso ≤ 1x.
- **Invalidazione.** Non applicabile: viene confrontato con l'equal weight e il risk parity nella dashboard.

---

## Famiglie (per il gate "accordo di almeno 3 famiglie poco correlate")

| Famiglia | Strategie |
|---|---|
| trend | S1, S2, S3 |
| carry | S4, S5, S6, S7 |
| relative_value | S8, S9, S10 |
| fundamental | S11, S14, S15 |
| event | S12, S13 |
| volatility | S16, S17, S18 |
| ml | S19 (sovrascrive la prob degli altri), S20 (allocatore) |

## S21 — Convinzione degli insider (SEC Form 4)

**Dato**: le dichiarazioni Form 4 che dirigenti, consiglieri e soci al 10% devono depositare quando trattano
azioni della propria società. Sono atti pubblici e dovuti per legge: qui non c'è nulla di non pubblico, e
nulla di tutto questo è un consiglio finanziario. Fonte: Alpha Vantage, che ripubblica i depositi SEC.

**Tesi economica**: chi dirige una società di esplorazione e produzione conosce il proprio costo marginale, i
tassi di declino dei giacimenti e il libro di copertura. Quando più dirigenti comprano azioni della propria
società sul mercato, con denaro proprio e già tassato, stanno esprimendo una view sull'economia del greggio a
termine che nessuna serie pubblicata contiene ancora. È una delle anomalie documentate più antiche
(Lakonishok e Lee 2001; Cohen, Malloy e Pomorski 2012 sulla differenza fra insider «di routine» e
«opportunisti»).

**Chi sta dall'altra parte**: chi legge gli stessi depositi come una nota di governance invece che come una
previsione sulla materia prima, e i flussi sistematici che prezzano il greggio solo su curva e scorte.

**Regimi favorevoli**: fasi laterali e punti di svolta, dove curva e momentum non dicono niente. È l'opposto
di una strategia da shock: un evento geopolitico muove il greggio molto prima di quanto chiunque possa
depositare un Form 4.

**Invalidazione**: il segnale salta quando il flusso è guidato da fatti societari — una fusione, una finestra
di riacquisto, un fondo che accumula una partecipazione — invece che dalla materia prima.

### Le tre trappole, misurate sui dati veri

| Trappola | Misura | Cosa fa il codice |
|---|---|---|
| La retribuzione non è convinzione | su ConocoPhillips solo **729 righe su 2.951 (25%)** sono operazioni di mercato in azioni ordinarie a un prezzo vero; il resto sono assegnazioni, maturazioni, esercizi di opzioni e ritenute fiscali, quasi tutte a prezzo 0 | solo le righe `open_market` entrano nel punteggio |
| Un socio al 10% non è un insider, ai fini di questa tesi | su Occidental **146 dei 258 acquisti di mercato** sono di un «10% Owner», mediana **17,2 M$**, massimo **564 M$**: è un fondo che accumula una partecipazione, cioè una view su un'azione, non sul greggio | i soci al 10% sono etichettati ed esclusi |
| Una sola operazione non può essere il punteggio | le dimensioni coprono quattro ordini di grandezza | ogni operazione è divisa per la mediana storica di quel ticker e troncata a ±3: un acquisto da 500 M$ e uno da 5 M$ dicono entrambi «ha comprato» |

### Il punto che decide tutto: la data di pubblicazione

Il feed porta **solo la data della transazione, mai quella di deposito**. Un Form 4 va depositato entro **due
giorni lavorativi** (17 CFR 240.16a-3), quindi trattare la data di transazione come il momento in cui il
mercato lo ha saputo sarebbe look-ahead — esattamente il difetto che `tests/test_no_lookahead.py` esiste per
catturare. Il motore usa quindi `published_at = data transazione + 2 giorni lavorativi`, che è il **più tardi**
in cui il deposito può legalmente comparire: i depositi reali sono spesso immediati, quindi la stima è
prudente e il motore non vede mai un'operazione prima di quando avrebbe potuto. È un'inferenza, non
un'osservazione, perciò la serie è sempre marcata `approx=true` e la dashboard mostra «≈».

### Segnale

* **Long** quando `insider_score` > 0,5 (quartile superiore del proprio storico a tre anni) **e** almeno
  **3 società distinte** hanno comprato nella finestra. La soglia di ampiezza è ciò che impedisce alla storia
  di una singola società di muovere il master.
* **Short** quando il punteggio < −0,7. L'asticella è più alta di proposito: un insider vende per comprare
  casa, per un divorzio o per una regola di diversificazione; per comprare c'è una ragione sola.
* **Nessun segnale** sotto 6 operazioni qualificanti nella finestra. Con circa nove acquisti di mercato per
  società all'anno, un trimestre silenzioso è normale, e il silenzio non va letto come una view piatta.
* Orizzonte **42 sedute (due mesi)**.

### Perché non è «tempo reale»

Non può esserlo, e vale la pena dirlo invece di lasciarlo intendere. La sola finestra di deposito è di due
giorni lavorativi; il flusso utile è di circa nove acquisti di mercato per società all'anno, quindi servono
settimane perché la finestra dica qualcosa. Il job che aggiorna la fonte è quello **settimanale**. Chi tratta
questo fattore in giornata sta trattando rumore.
