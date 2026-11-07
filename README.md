# Branch dati (non modificare a mano)

Questo branch contiene lo stato del paper trading scritto dai workflow schedulati:

- `state/` stato del motore: conto, posizioni, equity, operazioni, segnali, decisioni, previsioni, regimi,
  epoche, salute delle fonti e l'archivio point-in-time dei dati grezzi (`state/raw/`).
- `site-data/` i JSON compatti che la dashboard legge (contratto in `docs/SITE_DATA.md` sul branch `main`).

Il codice sta su `main`. Qui non c'è storia condivisa con `main`: è un branch orfano, così i commit di stato
(circa 48 al giorno) non toccano la storia del codice.
