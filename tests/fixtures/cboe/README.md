# Catene di opzioni Cboe (quotazioni ritardate, dati reali)

| File | Che cos'è | Scaricato |
|---|---|---|
| `uso_20261008.json` | catena USO, scadenze 6 e 20 novembre 2026, strike fra 80% e 112% del prezzo | 2026-10-08 18:11 UTC |
| `bno_20261008.json` | catena BNO, stesse scadenze, strike fra 75% e 125% del prezzo | 2026-10-08 16:34 UTC |

Fonte: `https://cdn.cboe.com/api/global/delayed_quotes/options/{SIMBOLO}.json` (quotazioni in ritardo di 15
minuti, nessuna chiave). I file sono la risposta originale ridotta alle scadenze e agli strike indicati e ai soli
campi che l'adapter legge (`option`, `bid`, `ask`, dimensioni, `iv`, greche, `open_interest`, `volume`); nessun
valore è stato modificato.

Servono a due cose che un dato sintetico non può dare: mostrare che la catena USO è negoziabile (denaro-lettera
intorno al 5-10% del prezzo) e che quella BNO, lo stesso giorno, non lo era (40-160%). I test del libro delle
opzioni verificano entrambe le cose su questi file.
