# Catene di opzioni Cboe (quotazioni ritardate, dati reali)

| File | Che cos'è | Scaricato |
|---|---|---|
| `uso_20261008.json` | catena USO, scadenze 6 e 20 novembre 2026, strike fra 80% e 112% del prezzo | 2026-10-08 18:11 UTC |
| `bno_20261008.json` | catena BNO, stesse scadenze, strike fra 75% e 125% del prezzo | 2026-10-08 16:34 UTC |
| `uso_chain_2026-11-13.parquet` | la catena USO come il motore l'archivia, ridotta alle dieci scadenze fra 4 e 75 giorni (2 008 righe) | 2026-11-13 21:17 UTC |

Fonte: `https://cdn.cboe.com/api/global/delayed_quotes/options/{SIMBOLO}.json` (quotazioni in ritardo di 15
minuti, nessuna chiave). I file sono la risposta originale ridotta alle scadenze e agli strike indicati e ai soli
campi che l'adapter legge (`option`, `bid`, `ask`, dimensioni, `iv`, greche, `open_interest`, `volume`); nessun
valore è stato modificato.

Servono a due cose che un dato sintetico non può dare: mostrare che la catena USO è negoziabile (denaro-lettera
intorno al 5-10% del prezzo) e che quella BNO, lo stesso giorno, non lo era (40-160%). I test del libro delle
opzioni verificano entrambe le cose su questi file.

Il file `uso_chain_2026-11-13.parquet` è una copia della tabella `uso_options/cboe` del ramo `data` (stesse
colonne, nessun valore modificato), tagliata alle scadenze che la lettura a due stati guarda. Serve all'unica
cosa che una catena vera può dimostrare (`tests/test_desk_twostate.py`, fase 22): che la lettura gira da capo a
fondo su quotazioni vere e che quel giorno ha **rifiutato** di chiamarli due stati, pur avendo prezzato ogni
quotazione dentro il suo denaro-lettera.
