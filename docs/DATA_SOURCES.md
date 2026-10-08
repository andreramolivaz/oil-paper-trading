# Fonti dati — verifica del 5 ottobre 2026

Ogni fonte è stata interrogata dal container di sviluppo (proxy egress condiviso). Le note "da runner" indicano
comportamenti che potrebbero differire sui runner GitHub Actions (IP diversi, nessun proxy).

| Fonte | Endpoint | Esito | Storico | Note |
|---|---|---|---|---|
| **EIA API v2** | `api.eia.gov/v2/petroleum/pri/spt` (RBRTE, RWTC) | ✅ 200 | Brent spot dal **1987-05-20** (9 988 oss.), WTI dal 1986 | `DEMO_KEY` funziona ma va in 429 dopo ~8 chiamate: serve `EIA_API_KEY` gratuita. Max 5 000 righe/chiamata → paginazione. Ultimo dato 2026-09-29: Brent 113,96 $, WTI 96,16 $. |
| EIA API v2 | `petroleum/pri/fut` (RCLC1..RCLC4, RBOB, HO) | ✅ 200 | 1983 → **2024-04-05** | La serie dei futures NYMEX è **cessata ad aprile 2024**: utile solo come proxy storico della pendenza di curva (WTI C1–C4). |
| EIA API v2 | `petroleum/stoc/wstk` (WCESTUS1, Cushing `W_EPC0_SAX_YCUOK_MBBL`, WGTSTUS1, WDISTUS1) | ✅ 200 | Scorte crude dal **1982-08-20** | Ultimo 2026-09-25: crude 427,3 Mbbl, Cushing 24,3 Mbbl. Pubblicazione mercoledì 10:30 ET. |
| EIA API v2 | `petroleum/sum/sndw` (lavorazioni, produzione, import/export, domanda implicita) | ⏳ 429 su DEMO_KEY | 1990+ | Da verificare con chiave reale; stessi codici WPSR (WCRFPUS2, WCRRIUS2, WCRIMUS2, WCREXUS2, WGFUPUS2, WDIUPUS2). |
| EIA WPSR CSV/XLS | `ir.eia.gov/wpsr/table1.csv`, `psw01.xls` | ✅ 200 | Settimana corrente | Fallback senza chiave per il dato più recente. **Non contiene la colonna Cushing**: senza `EIA_API_KEY` le scorte di Cushing restano NaN dichiarato (mai stimato), e con esse le feature `cushing_stocks`, `cushing_vs_5y`, `cushing_surprise_z` e la strategia S7 nella parte che le usa. |
| EIA XLS storico | `eia.gov/dnav/pet/hist_xls/RBRTEd.xls` | ✅ 200 (lento, ~9 s) | 1987+ | Fallback senza chiave per lo storico spot. |
| EIA STEO | `steo/data` (BREPUUS) | ⏳ 429 su DEMO_KEY | mensile | Previsione EIA del Brent: benchmark aggiuntivo. |
| **FRED API** | `api.stlouisfed.org/fred/series/observations` | ✅ raggiungibile (400 = chiave non valida) | DCOILBRENTEU 1987+, DCOILWTICO 1986+, OVXCLS 2007+, VIXCLS 1990+, DTWEXBGS 2006+, DGS10, T10YIE, DFF | Serve `FRED_API_KEY` gratuita. |
| FRED CSV senza chiave | `fred.stlouisfed.org/graph/fredgraph.csv?id=…` | ⚠️ intermittente dal container (HTTP/2 INTERNAL_ERROR / timeout), ok una volta | idem | Dato Brent 2026-09-29 = 113,96 $ coincide con EIA. Da runner probabilmente ok: usato come fallback. |
| **Yahoo Finance** chart API | `query1.finance.yahoo.com/v8/finance/chart/BZ=F` | ✅ 200 | BZ=F dal 2007-08; CL=F, RB=F, HO=F, HG=F dal 2000; ^OVX 2007; ^VIX 1990; DX-Y.NYB 1985; ^GSPC | Non ufficiale, ritardato ~10-15 min. Rate limit: ~1 richiesta/s va bene; raffiche → risposte vuote; circa duecento richieste in un'ora → HTTP 429 su tutto per minuti (misurato l'8 ottobre 2026: 17 contratti di fila, tutti rifiutati). Dopo un 429 che resiste ai tentativi l'adattatore non chiede più nulla per due minuti. Intraday: 5m×7g, 30m×60g, **1h×730g** (HAR-RV). |
| Yahoo singole scadenze Brent | `BZZ26.NYM … BZZ29.NYM` | ✅ 200 | Dal 2018-11 per i contratti ancora quotati | **Le scadenze passate scompaiono** (BZX26 → 404 dopo il 30/9). La curva storica M1–M12 non è ricostruibile a ritroso: la archiviamo noi ogni giorno da oggi. Curva al 2026-10-05: Dec-26 101,41 · Jan-27 97,95 · Jun-27 88,46 · Dec-27 81,92 · Dec-28 73,71 · Dec-29 71,52 (backwardation ripida). |
| Yahoo singole scadenze WTI | `CLZ26.NYM, CLF27.NYM …` | ✅ 200 | dal 2017-11 | Stessi limiti. |
| **CFTC** Socrata | `publicreporting.cftc.gov/resource/72hh-3qpy.json` (disaggregated futures-only) | ✅ 200, nessuna chiave | 2006+ | WTI = codice `067651` ("WTI-PHYSICAL"), managed money long/short. Ultimo 2026-09-29. Anche `jun7-fc8e` (futures+options). |
| CFTC file | `cftc.gov/dea/newcot/f_disagg.txt`, `files/dea/history/fut_disagg_txt_YYYY.zip` | ✅ 200 | 2006+ | Fallback. |
| **ICE COT** | `ice.com/publicdocs/futures/COTHistYYYY.csv` | ✅ 200, nessuna chiave | **2011+** (un CSV per anno) | "ICE Brent Crude Futures - ICE Futures Europe", colonne `M_Money_Positions_Long_All/Short_All`, dato del martedì. Ultimo 2026-09-29: OI 2,58 M, MM long 311,7k / short 116,3k. Anche Gasoil (crack europeo) e WTI ICE. |
| **GDELT** DOC API | `api.gdeltproject.org/api/v2/doc/doc` | ❌ 429 persistente dal container (IP condiviso) | 2017+ | Limite 1 richiesta/5 s per IP. Da runner probabilmente ok ma non garantito: usato come fonte secondaria con backoff. |
| GDELT file grezzi v2 | `data.gdeltproject.org/gdeltv2/lastupdate.txt` → `*.export.CSV.zip` (≈50 KB ogni 15 min) | ✅ 200 | 2015-02+ | Fonte primaria per intensità/tono. Posizioni di colonna **verificate su un file reale** (963 righe, 61 campi separati da tabulazione): Actor1CountryCode 7, Actor2CountryCode 17, EventRootCode 28, GoldsteinScale 30, NumArticles 33, AvgTone 34, ActionGeo_CountryCode 53, SOURCEURL 60. **Attenzione ai due sistemi di codici**: i codici degli attori sono CAMEO a 3 lettere (`IRN`, `SAU`, `USA`) mentre `ActionGeo_CountryCode` è **FIPS 10-4 a 2 lettere** (`IR` Iran, `SA` Arabia Saudita, `IZ` Iraq, `YM` Yemen, `AE` Emirati, `KU` Kuwait, `QA` Qatar, `MU` Oman, `BA` Bahrein); usare i codici CAMEO sulla geografia non filtra nulla. Backfill limitato (96 file/giorno): si accumula dal go-live; lo storico per i backtest viene dal GPR. |
| **GPR** (Caldara-Iacoviello) | `matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls` | ✅ 200 | **1985+** giornaliero (15 253 righe) | 11 colonne: DAY, N10D, GPRD, GPRD_ACT, GPRD_THREAT, date, GPRD_MA30, GPRD_MA7 più tre colonne di legenda. **L'indice viene ricalcolato quando il file cresce**: il valore del 2026-10-01 letto il 5 ottobre era 181,74 con l'ultimo giorno al 1° ottobre, e 176,28 dopo l'aggiunta dei giorni fino al 5. Non è un errore: è una revisione, e il motore la tratta come tale (ogni scaricamento è archiviato come vintage con il suo `observed_at`, niente viene sovrascritto). |
| **Baker Hughes** rig count | `rigcount.bakerhughes.com/na-rig-count` | ✅ 200 (verificato in esecuzione reale, 0,75 s) | **1987-07-17+**, 2 047 settimane | Due layout da unire: il workbook settimanale corrente (`MM-DD-YYYY North_America Rig_Count Report.xlsx`, ~7 MB) ha una tabella lunga `NAM Weekly` dal 2024-01-05; la serie storica `Date/Oil/Gas/Misc/Total` sta nell'archivio `.xlsb` (foglio `US Oil & Gas Split`) dal 1987 al 2024-03-28. Il nome del file cambia ogni settimana: lo scraper cerca gli anchor per testo. Venerdì 13:00 ET. Ultimo dato 2026-10-02: 456 impianti a olio, 133 a gas, 598 totali. |
| ICE specifiche Brent | `ice.com/products/219/Brent-Crude-Futures` | ✅ | — | 1 000 bbl, tick 0,01 $, orari 01:00–23:00 Londra, settlement media pesata 19:28–19:30 Londra, scadenza **ultimo giorno lavorativo del secondo mese precedente il mese di consegna** (eccezione Natale/Capodanno: un giorno prima). |
| Stooq | `stooq.com/q/d/l/?s=sc.f` | ❌ sfida JavaScript | — | Scartato. |
| Nasdaq Data Link CHRIS | — | ❌ 403 / deprecato | — | Scartato. |
| OPEC basket | `opec.org/basket/basketDayArchives.xml` | ❌ 403 | — | Scartato (calendario riunioni mantenuto a mano in `config/events.yaml`). |

## Chiavi necessarie (tutte gratuite)

| Secret | Dove si ottiene | Cosa abilita | Senza chiave |
|---|---|---|---|
| `EIA_API_KEY` | https://www.eia.gov/opendata/register.php (istantaneo) | Storico spot 1987+, WPSR completo, STEO, futures storici | Fallback a XLS/CSV EIA senza chiave (solo spot e tabella WPSR corrente); stato giallo |
| `FRED_API_KEY` | https://fredaccount.stlouisfed.org/apikeys | OVX, VIX, dollaro, tassi, breakeven con storico pieno | Fallback a `fredgraph.csv` (intermittente) e a Yahoo ^OVX/^VIX/DX-Y.NYB; stato giallo |

Inserimento: GitHub → Settings → Secrets and variables → Actions → New repository secret.

## Verifica in esecuzione reale (5 ottobre 2026)

Un ciclo completo `fetch --snapshot` → `assemble` → `eod` → `export-site` senza alcuna chiave API: nessun crash,
23 voci su 25 recuperate, stato complessivo **giallo**. Valori confrontati con riferimenti noti:

| Atteso | Risultato |
|---|---|
| Brent spot EIA 2026-09-29 = 113,96 $ | ✅ esatto (XLS EIA; il CSV FRED concorda al centesimo, il controllo di divergenza tra fonti non segnala nulla) |
| WTI spot 2026-09-29 = 96,16 $ | ✅ esatto |
| Dec-26 Brent ≈ 101,4 $ il 2026-10-05 | ✅ 101,07–101,41 nell'arco della sessione (Yahoo è ritardato di ~15 min: è deriva intraday, non un errore) |
| WPSR 2026-09-25 scorte crude 427 320 kbbl | ✅ esatto |
| WPSR 2026-09-25 Cushing 24 301 kbbl | ❌ non disponibile senza chiave EIA: NaN dichiarato (vedi sopra) |
| COT ICE Brent 2026-09-29 MM long/short 311 738 / 116 275 | ✅ esatto (net +195 463, OI 2 578 636) |
| GPR 2026-10-01 = 181,74 | ⚠️ 176,28: revisione dell'indice, vedi la riga GPR |

Altri numeri reali della stessa esecuzione: 4 775 barre giornaliere BZ=F dal 2007, 13 608 barre a 1 ora dal
2024-05-13, curva Brent con 18 scadenze (M1 BZZ26 101,07 · M2 98,07 · M3 96,13 · M12 BZX27 82,78: backwardation
ripida), rig count 2 047 settimane dal 1987, GPR 15 253 giorni dal 1985, COT Brent e gasolio 822 settimane dal 2011.
La serie continua roll-adjusted copre 222 roll dal 2007: in assenza di curva storica tutti sono corretti con il
rendimento dello spot EIA, e la verifica del 2026-09-28 dà uno spread stimato di −3,00 $ contro un M1−M2 reale di
−2,97 $. Ogni riga porta `roll_adj_source` con il metodo usato.

## Limiti dichiarati in dashboard
1. **Curva forward**: dal 2026-10-05 archiviamo ogni giorno le scadenze Yahoo (M1–M36). Prima di quella data la
   pendenza M1–M2/M1–M6 storica è un **proxy** costruito da WTI C1–C4 EIA (1983–2024-04) e dalle scadenze WTI/Brent
   Yahoo ancora vive (2018+ per i mesi differiti). Etichettato `approx`.
2. **Dated Brent** (fisico): nessuna fonte gratuita. Proxy: Brent spot EIA (FOB, Dated) meno il front future
   Yahoo. Fonti a pagamento opzionali: S&P Global Platts, Argus, ICE Data Services.
3. **Open interest/volumi per scadenza**: solo Yahoo (parziali). Fonti a pagamento: ICE Data, CME DataMine.
4. **Notizie**: GDELT dal go-live; storico via GPR (indice di notizie di 10 quotidiani, non specifico petrolio).
5. **Intraday**: Yahoo ritardato ~15 min; il job ogni 30 min marca il conto su quel prezzo e lo dichiara.

## Insider SEC Form 4 (Alpha Vantage) — verificata il 5 ottobre 2026

| | |
|---|---|
| Endpoint | `https://www.alphavantage.co/query?function=INSIDER_TRANSACTIONS&symbol=<ticker>` |
| Chiave | `ALPHAVANTAGE_API_KEY`, **opzionale** (come EIA e FRED): senza, la fonte è gialla e `insider_score` resta NaN |
| Cadenza | settimanale (job `weekly`) |
| Universo | 12 nomi **long sul greggio**: XOM, CVX, COP, EOG, OXY, DVN, FANG, APA, HES, SLB, HAL, BKR |
| Approssimata | **sì, sempre** — `published_at` è dedotta, non osservata |

**Verifica eseguita da questo container.** HTTP 200. ConocoPhillips ha restituito **2.951 righe dal 2008 al
2026**, Occidental **1.637**. Campi: `transaction_date`, `ticker`, `executive`, `executive_title`,
`security_type`, `acquisition_or_disposal`, `shares`, `share_price`.

**Manca la data di deposito.** È l'unico limite serio del feed e condiziona tutto il resto: senza di essa,
usare la data di transazione sarebbe look-ahead. Regola adottata: `published_at = transazione + 2 giorni
lavorativi alle 22:00 UTC`, la scadenza di legge del Form 4 (17 CFR 240.16a-3), cioè il momento **più tardo**
in cui il deposito può comparire. Chi volesse la data vera deve passare da EDGAR, che la pubblica ma richiede
di interpretare l'XML dei singoli depositi.

**Qualità del dato misurata, non supposta:**

* solo il **25%** delle righe di ConocoPhillips sono operazioni di mercato in azioni ordinarie a prezzo reale;
  il resto sono assegnazioni e meccanica retributiva, quasi tutte a `share_price = 0`;
* su Occidental **146 acquisti di mercato su 258** sono di un socio al 10% (mediana 17,2 M$): un fondo che
  accumula, non un dirigente che si espone. Etichettati ed esclusi dal punteggio;
* gli acquisti di mercato sono **circa nove per società all'anno**. È un fattore lento e rado: qualunque
  lettura infragiornaliera sarebbe rumore.

**Limiti del piano gratuito**: 25 chiamate al giorno. Con 12 nomi aggiornati una volta a settimana il margine è
ampio. Il piano gratuito e quello a pagamento restituiscono lo stesso schema; il codice tratta `Note`,
`Information` e `Error Message` come indisponibilità e degrada a giallo invece di inventare.

Fixture reale per i test offline: `tests/fixtures/insider/alphavantage_form4_COP_OXY.json` (COP e OXY,
catturata il 2026-10-05, tutte le righe di mercato più un campione di ogni classe di rumore).

## Fonti aggiunte per il desk — verifica dell'8 ottobre 2026

Verificate da un runner GitHub (`.github/workflows/research-data.yml`) e dal container di sviluppo.

| Fonte | Endpoint | Esito | Storico | Note |
|---|---|---|---|---|
| **Yahoo, fondi** | `BNO`, `USO` (grafico giornaliero e a 30 minuti) | ✅ 200 | BNO dal 2010-06, USO dal 2006 | BNO è la serie del Brent che un conto può davvero detenere: nessun salto di roll, aperture e chiusure reali. Le barre a 30 minuti coprono 60 giorni. |
| **Yahoo, per contratto** | `CLX26.NYM`, `BZZ26.NYM`, … (14 scadenze più tre dicembre lontani) | ✅ 200 | finché il contratto è quotato | Tabella lunga `date, code, OHLCV`. **Cumulativa**: Yahoo dimentica una scadenza il giorno in cui muore, quindi ogni scaricamento viene fuso con l'archivio. Da qui vengono il prezzo del contratto detenuto e la pendenza fra il vicino e un dicembre lontano. |
| Yahoo, `BZ=F` / `CL=F` infragiornaliero | — | ⚠️ inaffidabile vicino alla scadenza | — | Le barre alternano il contratto in scadenza e il successivo: finti movimenti di 5-7 $ nella stessa ora. Il motore chiede il simbolo del contratto (`BZZ26.NYM`), mai il continuo. |
| **IMF PortWatch** | servizio ArcGIS `Daily_Chokepoints_Data` | ✅ 200, nessuna chiave | dal 2019 | Transiti giornalieri per stretto e tipo di nave, da segnali AIS. Pubblicato il martedì per la settimana chiusa la domenica: `published_at` è quel martedì alle 15:00 UTC, mai la data del transito. Hormuz: circa 50 petroliere al giorno fino a febbraio 2026, circa una da marzo. |
| **Cboe, quotazioni ritardate** | `cdn.cboe.com/api/global/delayed_quotes/options/{SIMBOLO}.json` | ✅ 200, nessuna chiave (serve un User-Agent da browser) | solo l'istantanea | Catena completa con denaro, lettera, volatilità implicita, greche. Ritardo 15 minuti; il timbro del file è in UTC. BNO può restare ferma per ore: il libro controlla l'età delle quotazioni. Il motore conserva un'istantanea a settimana: è l'unico storico di quotazioni reali disponibile. |
| **Alpha Vantage** | `INSIDER_TRANSACTIONS` | ✅ con chiave gratuita | 2008+ | 25 chiamate al giorno, 12 titoli: lo scarica il lavoro settimanale. Se una chiamata viene rifiutata per limite, il titolo conserva i dati già archiviati. |
| Alpha Vantage | `HISTORICAL_OPTIONS` (catene storiche con denaro e lettera, dal 2008) | ❌ solo piani a pagamento (risposta «premium endpoint», 8 ottobre 2026) | — | Sarebbe lo storico che manca al libro delle opzioni. Finché non c'è, il suo backtest resta un modello e il libro archivia una catena reale a settimana. |
| Kalshi | `api.elections.kalshi.com/trade-api/v2` (serie `KXBRENTD`, `KXBRENTW`, `KXBRENTMON`) | ✅ 200, nessuna chiave | da luglio 2026 | Contratti a evento sul Brent. Scaricati e descritti in `docs/RESEARCH.md`; nessun adattatore nel motore. |

Solo `bno_daily`, `wti_front` e `brent_front` possono portare lo stato complessivo in rosso
(`critical_sources` in `config/data_sources.yaml`). Ogni altra fonte, se manca, spegne ciò che la legge.

