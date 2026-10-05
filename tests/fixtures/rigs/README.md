# Fixture rig count (Baker Hughes)

| File | Origine | Catturato | Dimensione | Note |
|---|---|---|---|---|
| `na-rig-count_2026-10-05.html` | `https://rigcount.bakerhughes.com/na-rig-count` | 2026-10-05 15:57 UTC (HTTP 200 in 0,75 s) | 26,4 KB | **Pagina reale, byte per byte.** Contiene gli anchor `/static-files/<uuid>` con il nome vero nell'attributo `title`: `10-02-2026 North_America Rig_Count Report.xlsx` (report corrente), `North America Rotary Rig Count (Jan 2000 - Mar 2024).xlsb` (archivio), più tabelle pivot/per stato che lo scraper scarta. |
| `nam_weekly_long_SYNTHETIC.xlsx` | — | 2026-10-05 | 5,9 KB | **Sintetico, creato per il test del parser: mai caricato dal motore.** Riproduce il layout LUNGO reale del foglio `NAM Weekly` (intestazione sulla riga 11, colonne `Country … US_PublishDate, Rig Count Value`) con i valori reali delle settimane 2026-09-18/09-25/10-02 (oil 452/455/456, gas 134/135/133, misc 9) più una riga Canada da escludere. Il workbook reale pesa 7,3 MB. |
| `us_oil_gas_split_wide_SYNTHETIC.xlsx` | — | 2026-10-05 | 5,1 KB | **Sintetico, creato per il test del parser: mai caricato dal motore.** Riproduce il layout LARGO reale del foglio `US Oil & Gas Split` dell'archivio `.xlsb` (intestazione sulla riga 7, colonne `Date | Oil | Gas | Misc | Total | % Oil | % Gas`) con i valori reali delle ultime settimane dell'archivio (2024-03-28: oil 506, gas 112, totale 621). Il `.xlsb` reale (0,7 MB, date come seriali Excel) non è incluso. |

## Valori reali verificati il 2026-10-05

* Report corrente (`10-02-2026 North_America Rig_Count Report.xlsx`, 7,3 MB): foglio `NAM Weekly`, 27 189 righe,
  **2024-01-05 → 2026-10-02**. Stati Uniti al **2026-10-02: oil 456, gas 133, misc 9, totale 598**
  (coincide con il foglio `NAM Summary`: United States Total 598, -1 sulla settimana, 549 un anno prima).
* Archivio (`North America Rotary Rig Count (Jan 2000 - Mar 2024).xlsb`, 0,7 MB): foglio `US Oil & Gas Split`,
  1 916 settimane, **1987-07-17 → 2024-03-28** (date come seriali Excel, origine 1899-12-30).
* Unione delle due fonti (quello che fa `BakerHughesAdapter.fetch`): 2 047 settimane dal 1987-07-17 al 2026-10-02.
