# Fixture notizie (GPR, GDELT)

| File | Origine | Catturato | Dimensione | Note |
|---|---|---|---|---|
| `20261005160000.export.CSV.zip` | `https://data.gdeltproject.org/gdeltv2/20261005160000.export.CSV.zip` | 2026-10-05 16:05 UTC | 64,7 KB | **File reale, byte per byte.** 963 eventi, 61 campi separati da tab, nessuna intestazione. Usato per verificare le posizioni dei campi e l'aggregazione dello slot. |
| `gdelt_lastupdate_20261005T1600.txt` | `https://data.gdeltproject.org/gdeltv2/lastupdate.txt` | 2026-10-05 16:05 UTC | 0,3 KB | **Reale.** Tre righe `size md5 url` (export, mentions, gkg) con URL in HTTP semplice: l'adapter li riscrive in HTTPS. |
| `gdelt_doc_api_429.txt` | `https://api.gdeltproject.org/api/v2/doc/doc?query=oil%20hormuz&mode=timelinevol&format=json&timespan=1week` | 2026-10-05 15:58 UTC | 0,4 KB | **Risposta reale HTTP 429** (corpo in testo semplice, non JSON): "Please limit requests to one every 5 seconds…". Dimostra perché la DOC API è una fonte secondaria da questo IP. |
| `gdelt_doc_timelinevol_HANDWRITTEN.json` | — | 2026-10-05 | 1 KB | **Forma JSON scritta a mano, NON dati reali** (la DOC API risponde 429 da questo container). Serve solo a testare il parser `parse_doc_timeline`: due serie, un valore `null`, timestamp `yyyymmddTHHMMSSZ`. |
| `gpr_daily_recent_trimmed.xlsx` | `https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls` | 2026-10-05 15:57 UTC | 10,8 KB | **Valori reali**, ridotti alle prime 5 e alle ultime 60 righe del file (15 253 righe, 3,3 MB) e **ri-codificati in `.xlsx`** perché il file originale `.xls` supera il limite di 200 KB per fixture; le colonne sono quelle originali (`DAY, N10D, GPRD, GPRD_ACT, GPRD_THREAT, date, GPRD_MA30, GPRD_MA7, event, var_name, var_label`). `pandas.read_excel` apre entrambi i formati, quindi il parser è lo stesso. |

## Valori reali verificati il 2026-10-05

* GPR: 15 253 righe dal 1985-01-01 al **2026-10-05**; GPRD 2026-10-01 = **176,278549**, 2026-10-02 = 264,232056,
  2026-10-05 = 141,491577 (media 30 giorni 167,31).
  **Discrepanza rispetto alla verifica precedente** (file con 15 249 righe, ultimo giorno 2026-10-01, GPRD
  181,736877): l'indice giornaliero viene **ricalcolato** quando vengono aggiunti nuovi giorni, quindi ogni
  snapshot è una versione (vintage) a sé. Lo store archivia `observed_at` e non sovrascrive nulla.
* GDELT: posizioni dei campi confermate sul file reale (Actor1CountryCode 7, Actor2CountryCode 17,
  EventRootCode 28, GoldsteinScale 30, NumArticles 33, AvgTone 34, ActionGeo_CountryCode 53, SOURCEURL 60).
  Attenzione: i codici attore sono CAMEO a 3 lettere (`IRN`, `SAU`), `ActionGeo_CountryCode` usa **FIPS 10-4 a
  2 lettere** (`IR`, `SA`, `IS`).
