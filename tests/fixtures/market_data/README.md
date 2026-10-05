# Fixture MarketData (snapshot reale, catturato il 2026-10-05 ~16:30 UTC)

Catturata eseguendo sul container di sviluppo, **senza alcuna API key**:

```bash
python -m engine.cli fetch --snapshot          # tutte le fonti, snapshot point-in-time in state/raw/
python -m engine.cli assemble                  # build_market_data(RawStore, Settings)
# engine.data.fixtures.save_fixture_market_data(md, start="1987-05-20", meta=...)
```

Nessun valore è stato modificato: le tabelle sono l'output di `build_market_data` troncato all'inizio della
serie Brent (`prices` dal 1987-05-20, prima di quella data esistono solo le serie macro FRED). `intraday` è
escluso di proposito. Totale **1,8 MB** (limite di progetto: 8 MB).

| File | Righe x colonne | Periodo | Fonte reale |
|---|---|---|---|
| `prices.parquet` | 10 155 x 23 | 1987-05-20 → 2026-10-05 | `brent_spot` EIA `RBRTEd.xls` (fallback senza chiave), `wti_spot` `fredgraph.csv` (DCOILWTICO), OHLCV Brent/WTI/RBOB/HO e OVX·VIX·DXY·SPX·Copper da Yahoo, `us10y`/`breakeven10y` da `fredgraph.csv`. `brent_cont`/`wti_cont` = serie continue back-adjusted (`engine.data.continuous`). Le 3 colonne extra sono `published_at`, `published_at_brent_spot`, `published_at_wti_spot`. |
| `curve.parquet` | 1 x 38 | 2026-10-05 | Curva Brent archiviata da Yahoo (M1..M15, M19, M25, M31 reali; i ranghi senza contratto quotato restano NaN) + `M1_code` + `published_at`. **La curva storica non esiste: si accumula uno snapshot al giorno dal 2026-10-05.** |
| `wpsr.parquet` | 1 x 11 | settimana 2026-09-25 (pubblicata 2026-09-30 14:30 UTC) | `ir.eia.gov/wpsr/table1.csv` (fallback senza chiave: `cushing_stocks` NaN, serve `EIA_API_KEY`). |
| `cot.parquet` | 2 704 x 10 | 2006-06-16 → 2026-10-02 (data di pubblicazione) | WTI = CFTC Socrata `067651`; Brent e Gasoil = ICE `COTHist{anno}.csv` 2011+. |
| `news.parquet` | 15 253 x 8 | 1985-01-01 → 2026-10-05 | GPR giornaliero (Caldara-Iacoviello) + aggregato giornaliero GDELT (solo dal go-live: una riga, 2026-10-05). |
| `rigs.parquet` | 2 047 x 2 | 1987-07-17 → 2026-10-02 | Baker Hughes (conteggio trivelle petrolio USA) + `published_at`. |
| `meta.json` | — | — | Fonte per colonna, adapter usati, fallback, tabelle assenti, flag di approssimazione. |

## Valori reali (verifica incrociata del 2026-10-05)

| Grandezza | Valore nella fixture | Riscontro |
|---|---|---|
| Brent spot EIA 2026-09-29 | **113,96 $** | coincide con `docs/DATA_SOURCES.md` |
| WTI spot 2026-09-29 | **96,16 $** | idem |
| Brent front (BZ=F) 2026-10-05 | 101,07 $ | mercato ancora aperto: quotazione Yahoo ritardata ~15 min |
| Curva 2026-10-05 | M1 101,07 · M2 98,07 · M3 96,13 · M4 94,23 · M5 91,93 · M6 90,24 (`M1_code` BZZ26) | backwardation ripida, coerente con Dec-26 ≈101,4 di inizio giornata |
| WPSR settimana 2026-09-25 | crude 427 320 kbbl, Cushing **NaN** | 427 320 coincide; Cushing richiede `EIA_API_KEY` |
| COT ICE Brent 2026-09-29 | OI 2 578 636, MM long **311 738** / short **116 275** | coincide |
| COT WTI 2026-09-29 | OI 1 878 576, MM long 209 028 / short 129 436 | coincide |
| GPR 2026-10-01 | **176,278549** | il file verificato in precedenza dava 181,736877: l'indice giornaliero viene **ricalcolato** quando arrivano nuovi giorni (vedi `tests/fixtures/news/README.md`) |
| Rig count USA petrolio 2026-10-02 | **456** (gas 133, totale 598) | coincide con il foglio `NAM Summary` di Baker Hughes |

## Limiti dichiarati nella fixture

* `cushing_stocks`, `steo_brent` e il proxy storico della curva WTI (`WTI_C1..WTI_C4`) sono assenti: richiedono
  `EIA_API_KEY`. Di conseguenza `curve_approx` è tutto `False` e non esiste alcuna riga di curva proxy.
* `gdelt_*` è popolato solo dal 2026-10-05 (i file grezzi GDELT non sono ricostruibili a ritroso oltre 96 slot/giorno).
* `prices` è indicizzata sull'**unione** delle date delle fonti: le celle senza osservazione restano NaN e non
  vengono mai riempite (nessun forward-fill).
