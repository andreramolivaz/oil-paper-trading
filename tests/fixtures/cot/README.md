# Fixture COT (snapshot reali, catturate il 2026-10-05)

Tutti i file sono risposte reali di CFTC e ICE scaricate il **5 ottobre 2026** dal container di sviluppo e usate
dai test offline di `tests/test_cot.py`. Nessun valore è stato modificato: i file sono stati solo **ridotti**
(parametri della richiesta per l'API Socrata; selezione di righe intere, byte per byte, per i CSV/TXT, con la riga
di intestazione originale conservata dove esiste).

| File | Fonte (URL + parametri) | Contenuto |
|---|---|---|
| `socrata_72hh-3qpy_067651_since_2026-06-09.json` | `https://publicreporting.cftc.gov/resource/72hh-3qpy.json?$select=report_date_as_yyyy_mm_dd,cftc_contract_market_code,market_and_exchange_names,contract_market_name,open_interest_all,m_money_positions_long_all,m_money_positions_short_all,prod_merc_positions_long,prod_merc_positions_short,swap_positions_long_all,swap__positions_short_all&$where=cftc_contract_market_code='067651' AND report_date_as_yyyy_mm_dd >= '2026-06-09'&$order=report_date_as_yyyy_mm_dd&$limit=50000&$offset=0` | 17 settimane WTI (NYMEX, codice `067651`, "WTI-PHYSICAL") dal 2026-06-09 al 2026-09-29, futures-only disaggregato. Ultima riga: OI 1 878 576, managed money long 209 028 / short 129 436 (netto +79 592), produttori long 615 887 / short 296 348, swap dealer long 113 558 / short 575 715. Comprende le settimane con festività federale: 2026-06-16 (Juneteenth venerdì 19/6 → pubblicazione lunedì 22/6) e 2026-06-30 (4 luglio osservato venerdì 3/7 → lunedì 6/7). Il dataset completo conta 1 060 righe dal 2006-06-13; `prod_merc_positions_long/short` sono i nomi reali dei campi (senza suffisso `_all`), `swap__positions_short_all` ha il doppio underscore. |
| `COTHist2026_trimmed.csv` | `https://www.ice.com/publicdocs/futures/COTHist2026.csv` (297 KB, 507 righe, 191 colonne, BOM UTF-8) | Intestazione originale (con BOM) + 20 righe: `ICE Brent Crude Futures - ICE Futures Europe` e `ICE Gasoil Futures - ICE Futures Europe`, 10 settimane dal 2026-07-28 al 2026-09-29, solo `FutOnly`. Brent 2026-09-29: OI 2 578 636, MM long 311 738 / short 116 275 (netto +195 463), produttori 902 012 / 1 242 289, swap 386 885 / 85 509. Gasoil 2026-09-29: OI 749 721, MM long 93 584 / short 24 239. |
| `COTHist2011_trimmed.csv` | `https://www.ice.com/publicdocs/futures/COTHist2011.csv` (151 KB, 208 righe; `COTHist2010.csv` → 404: il 2011 è il primo anno disponibile) | Intestazione originale (senza BOM, colonna scritta `Swap__Positions_Short_All` con doppio underscore come in tutti i file 2011-2020) + 8 righe: Brent e Gasoil, settimane 2011-01-04 e 2011-01-11, **sia** `FutOnly` **sia** `Combined`. Fino al 2013-03-05 ICE pubblicava il rapporto futures+opzioni con lo stesso nome del futures-only: l'adapter deve tenere solo `FutOnly` (Brent 2011-01-04: OI 870 690 FutOnly contro 878 891 Combined; MM long 133 347 / short 11 814; swap short 56 589). |
| `f_disagg_2026-09-29_trimmed.txt` | `https://www.cftc.gov/dea/newcot/f_disagg.txt` (457 KB, 283 righe, senza intestazione, 191 campi, CRLF) | 5 righe intere: WHEAT-SRW (prima riga del file), `USGC HSFO-PLATTS/BRENT 1ST LN`, `WTI-PHYSICAL` (codice `067651`), `BRENT LAST DAY` (codice `06765T`), ultima riga del file. Tutte al 2026-09-29. I campi 7..14 della riga WTI coincidono con l'API Socrata della stessa data (verifica delle posizioni: 7 OI, 8/9 produttori, 10/11 swap, 13/14 managed money, 190 `FutOnly`). |

Posizioni dei campi del file senza intestazione verificate anche con la riga di intestazione di
`https://www.cftc.gov/files/dea/history/fut_disagg_txt_2026.zip` (`f_year.txt`, 191 colonne).

Calendario di pubblicazione: `https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm`
(2026: 05/1*, 09/1, 16/1, 23/1, 30/1, 06/2, 13/2, 20/2, 27/2, 06/3, 13/3, 20/3, 27/3, 03/4, 10/4, 17/4, 24/4, 01/5,
08/5, 15/5, 22/5, 29/5, 05/6, 12/6, 22/6*, 26/6, 06/7*, 10/7, 17/7, 24/7, 31/7, 07/8, 14/8, 21/8, 28/8, 04/9, 11/9,
18/9, 25/9, 02/10, 09/10, 16/10, 23/10, 30/10, 06/11, 16/11*, 20/11, 30/11*, 04/12, 11/12, 18/12, 28/12*;
`*` = rinviato per festività federale). Il test `test_release_days_match_official_2026_schedule` confronta la
regola dell'adapter con questo elenco.
