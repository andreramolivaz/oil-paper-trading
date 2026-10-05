# Fixture FRED (snapshot reali, catturate il 2026-10-05)

File usati dai test offline di `tests/test_fred.py`. Catturati il **5 ottobre 2026** (~07:00 UTC) dal container di
sviluppo (proxy egress condiviso). Nessun valore è stato modificato: i CSV sono il download reale troncato alle
righe dal 2026-08-03 in avanti (intestazione originale inclusa).

| File | Fonte (URL + parametri) | Contenuto |
|---|---|---|
| `dcoilbrenteu_fredgraph_2026-08-03.csv` | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU` (`curl --http1.1`, 200 in 0,23 s, 169 913 byte, 10 270 righe dal 1987-05-20) | Brent spot FOB ($/bbl, fonte EIA) dal 2026-08-03 (88,90) al **2026-09-29 (113,96 $, coincide con EIA RBRTE)**: 42 osservazioni, di cui **1 mancante** (`2026-08-31,` — campo vuoto, bank holiday UK). Nel file completo le righe vuote sono 1 173. |
| `ovxcls_fredgraph_2026-08-03.csv` | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=OVXCLS` (200 in 0,44 s, 85 214 byte, 5 061 righe dal 2007-05-10) | OVX (CBOE Crude Oil Volatility Index) dal 2026-08-03 (57,20) al **2026-10-01 (51,69)**: 44 osservazioni, **1 mancante** (`2026-09-07,` — Labor Day). Nel file completo le righe vuote sono 179. |
| `dcoilbrenteu_observations_2026-08-03.json` | Formato di `https://api.stlouisfed.org/fred/series/observations?series_id=DCOILBRENTEU&file_type=json&observation_start=2026-08-03` | **Non è una risposta catturata dall'API** (nel container non è disponibile una `FRED_API_KEY`): è la busta JSON documentata da FRED (`realtime_start/end`, `observation_start/end`, `units`, `output_type`, `file_type`, `order_by`, `sort_order`, `count`, `offset`, `limit`, `observations[] = {realtime_start, realtime_end, date, value}`) riempita con **gli stessi 42 valori reali** del CSV qui sopra; l'osservazione mancante del 2026-08-31 è codificata `"value": "."` come fa l'API. Il test di rete `test_network_fred_api_brent_and_ovx` verifica il formato contro l'API vera quando la chiave è presente. |
| `api_error_400_no_key.json` | `https://api.stlouisfed.org/fred/series/observations?series_id=DCOILBRENTEU&file_type=json` senza `api_key` → **HTTP 400** | Busta d'errore reale dell'API: `{"error_code":400,"error_message":"Bad Request.  Variable api_key is not set. ..."}`. |
| `api_error_400_unregistered_key.json` | Stessa richiesta con una chiave di 32 caratteri non registrata → **HTTP 400** | `{"error_code":400,"error_message":"Bad Request.  The value for variable api_key is not registered. ..."}`. L'adapter non ritenta i 400 e non scrive mai la chiave nei messaggi. |

## Note di verifica (2026-10-05)

* `fredgraph.csv` **ha funzionato** dal container con HTTP/1.1 (`curl --http1.1` e `requests`, che parla solo
  HTTP/1.1): DCOILBRENTEU 0,23 s, OVXCLS 0,44 s, DFF 0,88 s (ultimo DFF 2026-10-01 = 3,88 %). Il 2026-10-05 con
  HTTP/2 (default di curl) erano stati osservati `INTERNAL_ERROR`/timeout intermittenti: il test di rete sul CSV è
  marcato `xfail(strict=False)` per i soli errori di trasporto (`DataUnavailable`).
* Un id sconosciuto (`?id=NOPE123XYZ`) risponde **HTTP 404 con una pagina HTML** ("Error - St. Louis Fed"): il parser
  rifiuta l'intestazione e l'adapter solleva `DataUnavailable`.
* I valori mancanti nel CSV odierno sono **campi vuoti** (es. `2026-08-31,`), non più `.`: l'adapter accetta entrambi
  i marcatori (e l'intestazione legacy `DATE,<ID>`) e li scarta senza mai riempire.
* `published_at` = giorno lavorativo USA successivo alla data, 12:00 UTC (`engine.core.calendar.add_business_days`,
  calendario `US`): 2026-09-04 (venerdì prima del Labor Day) → 2026-09-08 12:00 UTC; 2026-09-29 → 2026-09-30 12:00 UTC.
  Regola di progetto, ottimistica per le serie spot di origine EIA (il 2026-10-05 FRED aveva DCOILBRENTEU solo fino
  al 2026-09-29 mentre OVXCLS/DFF arrivavano al 2026-10-01).
