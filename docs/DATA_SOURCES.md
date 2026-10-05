# Fonti dati — verifica del 5 ottobre 2026

Ogni fonte è stata interrogata dal container di sviluppo (proxy egress condiviso). Le note "da runner" indicano
comportamenti che potrebbero differire sui runner GitHub Actions (IP diversi, nessun proxy).

| Fonte | Endpoint | Esito | Storico | Note |
|---|---|---|---|---|
| **EIA API v2** | `api.eia.gov/v2/petroleum/pri/spt` (RBRTE, RWTC) | ✅ 200 | Brent spot dal **1987-05-20** (9 988 oss.), WTI dal 1986 | `DEMO_KEY` funziona ma va in 429 dopo ~8 chiamate: serve `EIA_API_KEY` gratuita. Max 5 000 righe/chiamata → paginazione. Ultimo dato 2026-09-29: Brent 113,96 $, WTI 96,16 $. |
| EIA API v2 | `petroleum/pri/fut` (RCLC1..RCLC4, RBOB, HO) | ✅ 200 | 1983 → **2024-04-05** | La serie dei futures NYMEX è **cessata ad aprile 2024**: utile solo come proxy storico della pendenza di curva (WTI C1–C4). |
| EIA API v2 | `petroleum/stoc/wstk` (WCESTUS1, Cushing `W_EPC0_SAX_YCUOK_MBBL`, WGTSTUS1, WDISTUS1) | ✅ 200 | Scorte crude dal **1982-08-20** | Ultimo 2026-09-25: crude 427,3 Mbbl, Cushing 24,3 Mbbl. Pubblicazione mercoledì 10:30 ET. |
| EIA API v2 | `petroleum/sum/sndw` (lavorazioni, produzione, import/export, domanda implicita) | ⏳ 429 su DEMO_KEY | 1990+ | Da verificare con chiave reale; stessi codici WPSR (WCRFPUS2, WCRRIUS2, WCRIMUS2, WCREXUS2, WGFUPUS2, WDIUPUS2). |
| EIA WPSR CSV/XLS | `ir.eia.gov/wpsr/table1.csv`, `psw01.xls` | ✅ 200 | Settimana corrente | Fallback senza chiave per il dato più recente. |
| EIA XLS storico | `eia.gov/dnav/pet/hist_xls/RBRTEd.xls` | ✅ 200 (lento, ~9 s) | 1987+ | Fallback senza chiave per lo storico spot. |
| EIA STEO | `steo/data` (BREPUUS) | ⏳ 429 su DEMO_KEY | mensile | Previsione EIA del Brent: benchmark aggiuntivo. |
| **FRED API** | `api.stlouisfed.org/fred/series/observations` | ✅ raggiungibile (400 = chiave non valida) | DCOILBRENTEU 1987+, DCOILWTICO 1986+, OVXCLS 2007+, VIXCLS 1990+, DTWEXBGS 2006+, DGS10, T10YIE, DFF | Serve `FRED_API_KEY` gratuita. |
| FRED CSV senza chiave | `fred.stlouisfed.org/graph/fredgraph.csv?id=…` | ⚠️ intermittente dal container (HTTP/2 INTERNAL_ERROR / timeout), ok una volta | idem | Dato Brent 2026-09-29 = 113,96 $ coincide con EIA. Da runner probabilmente ok: usato come fallback. |
| **Yahoo Finance** chart API | `query1.finance.yahoo.com/v8/finance/chart/BZ=F` | ✅ 200 | BZ=F dal 2007-08; CL=F, RB=F, HO=F, HG=F dal 2000; ^OVX 2007; ^VIX 1990; DX-Y.NYB 1985; ^GSPC | Non ufficiale, ritardato ~10-15 min. Rate limit: ~1 richiesta/s va bene; raffiche → risposte vuote. Intraday: 5m×7g, 30m×60g, **1h×730g** (HAR-RV). |
| Yahoo singole scadenze Brent | `BZZ26.NYM … BZZ29.NYM` | ✅ 200 | Dal 2018-11 per i contratti ancora quotati | **Le scadenze passate scompaiono** (BZX26 → 404 dopo il 30/9). La curva storica M1–M12 non è ricostruibile a ritroso: la archiviamo noi ogni giorno da oggi. Curva al 2026-10-05: Dec-26 101,41 · Jan-27 97,95 · Jun-27 88,46 · Dec-27 81,92 · Dec-28 73,71 · Dec-29 71,52 (backwardation ripida). |
| Yahoo singole scadenze WTI | `CLZ26.NYM, CLF27.NYM …` | ✅ 200 | dal 2017-11 | Stessi limiti. |
| **CFTC** Socrata | `publicreporting.cftc.gov/resource/72hh-3qpy.json` (disaggregated futures-only) | ✅ 200, nessuna chiave | 2006+ | WTI = codice `067651` ("WTI-PHYSICAL"), managed money long/short. Ultimo 2026-09-29. Anche `jun7-fc8e` (futures+options). |
| CFTC file | `cftc.gov/dea/newcot/f_disagg.txt`, `files/dea/history/fut_disagg_txt_YYYY.zip` | ✅ 200 | 2006+ | Fallback. |
| **ICE COT** | `ice.com/publicdocs/futures/COTHistYYYY.csv` | ✅ 200, nessuna chiave | **2011+** (un CSV per anno) | "ICE Brent Crude Futures - ICE Futures Europe", colonne `M_Money_Positions_Long_All/Short_All`, dato del martedì. Ultimo 2026-09-29: OI 2,58 M, MM long 311,7k / short 116,3k. Anche Gasoil (crack europeo) e WTI ICE. |
| **GDELT** DOC API | `api.gdeltproject.org/api/v2/doc/doc` | ❌ 429 persistente dal container (IP condiviso) | 2017+ | Limite 1 richiesta/5 s per IP. Da runner probabilmente ok ma non garantito: usato come fonte secondaria con backoff. |
| GDELT file grezzi v2 | `data.gdeltproject.org/gdeltv2/lastupdate.txt` → `*.export.CSV.zip` (≈50 KB ogni 15 min) | ✅ 200 | 2015-02+ | Fonte primaria per intensità/tono: aggregazione giornaliera di eventi con attori IRN/SAU/USA e geografia Hormuz/Golfo, Goldstein e AvgTone. Backfill limitato (96 file/giorno): si accumula dal go-live; lo storico per i backtest viene dal GPR. |
| **GPR** (Caldara-Iacoviello) | `matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls` | ✅ 200 | **1985+** giornaliero (15 249 righe) | Colonne GPRD, GPRD_ACT, GPRD_THREAT, N10D. Ultimo 2026-10-01: GPRD 181,7 (media 30g 167). Proxy storico del premio geopolitico per backtest. |
| **Baker Hughes** rig count | `rigcount.bakerhughes.com/na-rig-count` | ⚠️ 503/timeout dal container, 200 una volta | 1987+ (xlsb) | Pagina Drupal che linka un xlsb settimanale con nome variabile: scraper con degradazione (stato giallo). Venerdì 13:00 ET. |
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

## Limiti dichiarati in dashboard
1. **Curva forward**: dal 2026-10-05 archiviamo ogni giorno le scadenze Yahoo (M1–M36). Prima di quella data la
   pendenza M1–M2/M1–M6 storica è un **proxy** costruito da WTI C1–C4 EIA (1983–2024-04) e dalle scadenze WTI/Brent
   Yahoo ancora vive (2018+ per i mesi differiti). Etichettato `approx`.
2. **Dated Brent** (fisico): nessuna fonte gratuita. Proxy: Brent spot EIA (FOB, Dated) meno il front future
   Yahoo. Fonti a pagamento opzionali: S&P Global Platts, Argus, ICE Data Services.
3. **Open interest/volumi per scadenza**: solo Yahoo (parziali). Fonti a pagamento: ICE Data, CME DataMine.
4. **Notizie**: GDELT dal go-live; storico via GPR (indice di notizie di 10 quotidiani, non specifico petrolio).
5. **Intraday**: Yahoo ritardato ~15 min; il job ogni 30 min marca il conto su quel prezzo e lo dichiara.
