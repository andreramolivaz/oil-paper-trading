# Fixture Yahoo Finance (chart API v8)

Snapshot reali catturate il **2026-10-05 ~06:45 UTC** dal container di sviluppo con
`GET https://query1.finance.yahoo.com/v8/finance/chart/{symbol}` (User-Agent del progetto, nessuna chiave).
`manifest.json` riporta per ogni file URL completo, parametri, status HTTP, byte, numero di timestamp e orario di cattura.
I corpi sono salvati così come ricevuti (nessuna modifica).

| File | Simbolo | Richiesta | HTTP | Contenuto |
|---|---|---|---|---|
| `bz_f_1d_5d.json` | `BZ=F` (Brent front, continuo) | `period1=now-7d, period2=now, interval=1d` | 200 | 6 barre 2026-09-28 → 2026-10-05 (l'ultima è la sessione in corso); timestamp 04:00 UTC = 00:00 New York |
| `bzz26_nym_1d_5d.json` | `BZZ26.NYM` (Dec-26) | idem | 200 | 6 barre; stessi valori di BZ=F dal 1/10 (BZZ26 è il front dopo la scadenza di BZX26 il 30/9) |
| `bzf27_nym_1d_5d.json` | `BZF27.NYM` (Jan-27) | idem | 200 | 6 barre |
| `bzg27_nym_1d_5d.json` | `BZG27.NYM` (Feb-27) | idem | 200 | 6 barre |
| `ovx_1d_5d.json` | `^OVX` (CBOE Crude Oil Volatility) | idem | 200 | 5 barre 2026-09-28 → 2026-10-02; timestamp 13:30 UTC = 09:30 New York; volume 0 |
| `bz_f_1h_7d.json` | `BZ=F` | `range=7d, interval=1h` | 200 | 148 punti dal 2026-09-28 04:00 UTC: 29 righe con quote `null` (pausa 17:00-18:00 ET, weekend) e una riga finale fuori griglia (`regularMarketTime` 06:34:52 UTC) |
| `bz_f_1d_2009-04_nulls.json` | `BZ=F` | `period1=2009-04-13, period2=2009-04-25, interval=1d` | 200 | 10 barre di cui 8 con close `null` (reali, non trattate) |
| `bzx26_nym_404.json` | `BZX26.NYM` (Nov-26, scaduto il 2026-09-30) | `period1/period2/interval=1d` | 404 | `chart.error = {"code": "Not Found", ...}` |
| `bz_f_1d_pre_history_400.json` | `BZ=F` | `period1=1980-01-01, period2=1990-01-01, interval=1d` | 400 | `chart.error = {"code": "Bad Request", "description": "Data doesn't exist ..."}` |

Valori di riferimento usati nei test: BZ=F close 2026-10-02 = 102.25, BZZ26 close 2026-10-02 = 102.25,
BZF27 = 98.71, BZG27 = 95.93, ^OVX close 2026-10-02 = 51.00.

Fonte: Yahoo Finance (non ufficiale, quote ritardate ~10-15 min). Uso esclusivamente per test offline.
