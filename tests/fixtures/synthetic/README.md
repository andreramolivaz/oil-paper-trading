# Dati sintetici per i test

I file e gli helper di questa cartella NON sono dati di mercato: sono serie generate con un seme fisso per
testare la logica del motore (sessione, broker, feature, strategie). Non vengono mai caricati dal motore in
produzione e non compaiono nella dashboard.

Gli helper sono in `tests/synthetic.py` (`make_market_data`, `make_feature_frame`): random walk con OHLC
coerenti, una curva in backwardation costruita a mano e tabelle WPSR/COT/news minime con `published_at`
realistici (mercoledì 10:30 ET per l'EIA, venerdì 15:30 ET per il COT).
