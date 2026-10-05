# Fixture EIA (snapshot reali, catturate il 2026-10-05)

Tutti i file sono risposte reali di fonti EIA scaricate il **5 ottobre 2026** dal container di sviluppo e usate
dai test offline di `tests/test_eia.py`. Nessun valore è stato modificato; i file JSON sono salvati così come
restituiti dall'API (solo troncati tramite i parametri `length`/`offset` della richiesta).

| File | Fonte (URL + parametri) | Contenuto |
|---|---|---|
| `rbrte_spot_page1_100rows.json` | `https://api.eia.gov/v2/petroleum/pri/spt/data/?frequency=daily&data[0]=value&facets[series][]=RBRTE&sort[0][column]=period&sort[0][direction]=asc&length=100&offset=0` | Prima pagina del Brent spot (RBRTE): 100 righe dal 1987-05-20 (18,63 $) al 1987-10-07 (18,58 $); `total=9988`. |
| `wpsr_stoc_wstk_20weeks.json` | `https://api.eia.gov/v2/petroleum/stoc/wstk/data/?frequency=weekly&data[0]=value&facets[series][]=WCESTUS1&facets[series][]=W_EPC0_SAX_YCUOK_MBBL&facets[series][]=WGTSTUS1&facets[series][]=WDISTUS1&sort[0][column]=period&sort[0][direction]=desc&length=80` | Scorte settimanali (MBBL = migliaia di barili), 20 settimane 2026-05-15 → 2026-09-25. Ultimo: crude 427 320, Cushing 24 301, benzina 204 362, distillati 105 180. |
| `wpsr_sum_sndw_20weeks.json` | `https://api.eia.gov/v2/petroleum/sum/sndw/data/?frequency=weekly&data[0]=value&facets[series][]=WCRRIUS2&facets[series][]=WCRFPUS2&facets[series][]=WCRIMUS2&facets[series][]=WCREXUS2&facets[series][]=WGFUPUS2&facets[series][]=WDIUPUS2&sort[0][column]=period&sort[0][direction]=desc&length=120` | Flussi settimanali (MBBL/D), 20 settimane 2026-05-15 → 2026-09-25. Ultimo: lavorazioni 16 257, produzione 13 955, import 5 698, export 3 570, benzina fornita 8 689, distillati forniti 3 948. |
| `fut_rclc1_4_last3days.json` | `https://api.eia.gov/v2/petroleum/pri/fut/data/?frequency=daily&data[0]=value&facets[series][]=RCLC1..RCLC4&sort[0][column]=period&sort[0][direction]=desc&length=12` | Ultimi 3 giorni (2024-04-03 → 2024-04-05) dei futures NYMEX WTI C1–C4; la serie EIA è cessata il 2024-04-05 (C1 86,91 $). |
| `steo_brepuus_full.json` | `https://api.eia.gov/v2/steo/data/?frequency=monthly&data[0]=value&facets[seriesId][]=BREPUUS&sort[0][column]=period&sort[0][direction]=asc&length=5000&offset=0` | Serie STEO completa del Brent (456 mesi, 1990-01 → 2027-12), vintage settembre 2026: 2026-08 = 91,08 (storico), 2026-09 = 93 (prima previsione), 2027-12 = 62. |
| `wpsr_table1_2026-09-25.csv` | `https://ir.eia.gov/wpsr/table1.csv` (302 → URL firmato su `ir.eia.gov/secure/wpsr/`) | Tabella 1 del WPSR, settimana al 2026-09-25. Codifica cp1252 (trattini `0x96` nelle celle n/d). |
| `RBRTEd_trimmed.xlsx` | `https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls` (452 KB, foglio `Data 1`, 9 097 osservazioni) | Copia **ridotta** del workbook reale: fogli `Contents` e `Data 1` con le 3 righe di intestazione, le prime 60 e le ultime 60 osservazioni (1987-05-20 18,63 $ … 2026-09-29 113,96 $). Risalvata in xlsx perché il file originale supera il limite di 200 KB e `xlwt` non è disponibile; il parser legge lo stesso layout (il test `network` usa il file .xls originale). |

Chiave API: le catture sono state fatte con `DEMO_KEY` (limite ~8 chiamate/minuto); il motore richiede
`EIA_API_KEY` e non incorpora mai `DEMO_KEY`.
