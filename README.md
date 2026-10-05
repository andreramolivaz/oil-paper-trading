# Oil Paper Trading — Brent

Sistema di ricerca e **paper trading** sul petrolio Brent. Scarica dati reali, riconosce il regime di mercato,
fa girare una libreria di strategie con tesi economiche esplicite, produce previsioni con intervalli di
confidenza e gestisce un conto simulato da 10.000 $ pubblicando tutto su una dashboard.

> **Simulazione a scopo di studio su dati reali. Nessun consiglio finanziario, nessun ordine reale, nessun
> broker collegato.** Ogni numero mostrato porta la sua fonte e il suo orario; quando un dato manca il sistema
> scrive che manca e non apre nuovo rischio.

## Cosa fa, in breve

| | |
|---|---|
| **Dati** | Brent spot EIA dal 1987, futures BZ=F dal 2007, curva per scadenza, WTI, benzina e gasolio, OVX, VIX, dollaro, tassi, scorte EIA, COT di ICE e CFTC, rig count Baker Hughes dal 1987, indice geopolitico GPR dal 1985, eventi GDELT. Tutto con catena di fallback e stato di salute per fonte. |
| **Regimi** | HMM walk-forward (3-5 stati scelti per BIC) con filtro causale scritto a mano, più rilevamento online dei cambi strutturali (BOCPD). Etichette leggibili, probabilità e storico. Sotto la soglia di confidenza dichiara «Transizione» e riduce il rischio. |
| **Strategie** | 18 strategie implementate (S1-S18) più l'allocatore regime-condizionato (S20), ognuna con tesi economica, controparte che perde, regimi favorevoli e condizioni di invalidazione in `docs/STRATEGIES.md`. Ognuna ha un conto ombra da 10.000 $ per la classifica. |
| **Previsioni** | 1 giorno, 1 settimana, 1 mese, 3 mesi con mediana e quantili 5/25/75/95, sempre confrontate con random walk e curva dei futures. Random walk, curva, ARIMA/ETS, famiglia GARCH, HAR-RV, LightGBM quantilico, ensemble regime-condizionato. |
| **Rischio** | Default senza leva. Oltre 1x solo se passa un gate di sette condizioni; tetto assoluto 10x imposto dal codice e dimostrato da test di proprietà. Margine al 10%, stop-out, conto azzerato sotto il 5% del capitale. |
| **Dashboard** | Sito statico in italiano, tema scuro, mobile-first, con pulsante di reset a 10.000 $. |

## Come funziona

```
GitHub Actions (cron)              branch `data`                    GitHub Pages
┌──────────────────────┐          ┌─────────────────┐              ┌──────────────────┐
│ update  ogni 30 min  │ ──────►  │ state/*.json    │ ──────────►  │ dashboard legge  │
│ eod     dopo le 19:30│  scrive  │ site-data/*.json│   raw.github │ i JSON a runtime │
│ weekly  sabato       │          └─────────────────┘              └──────────────────┘
└──────────────────────┘
```

Il motore non gira nel browser: gira su GitHub Actions e scrive JSON versionati su un branch dedicato. Il sito
è statico e li legge quando lo apri, con una copia inclusa nel build come riserva. Backtest e paper trading
usano **lo stesso** loop a eventi (`engine/backtest/session.py`), quindi il track record live è confrontabile
con il backtest.

Il ciclo di una giornata: le barre del giorno arrivano prima e riempiono gli ordini decisi la sera precedente
(mai al prezzo che li ha generati), poi si rolla il contratto se il calendario ICE lo richiede, poi al
settlement si costruiscono le feature point-in-time, si inferisce il regime, le strategie emettono i segnali,
l'allocatore li combina, il gate decide se la leva può superare 1x e gli ordini vengono messi in coda per la
barra successiva.

## Cosa devi fare tu

Tre cose che il codice non può fare da sé:

1. **Attivare GitHub Pages**: Settings → Pages → Source: **GitHub Actions**. Il repository deve essere
   **pubblico** (Pages su repo privati richiede GitHub Pro, e `raw.githubusercontent.com` chiederebbe un token
   anche per leggere i JSON).
2. **Permessi dei workflow**: Settings → Actions → General → Workflow permissions → **Read and write**. Servono
   per scrivere il branch dati, aprire le issue di allerta e riattivare i cron.
3. **Chiavi API gratuite** (opzionali ma consigliate): Settings → Secrets and variables → Actions → New
   repository secret.

| Secret | Dove si ottiene | Cosa sblocca | Senza la chiave |
|---|---|---|---|
| `EIA_API_KEY` | <https://www.eia.gov/opendata/register.php> (istantaneo) | Storico completo delle scorte settimanali, scorte di **Cushing**, previsione STEO, proxy storico della curva (WTI C1-C4) | Solo l'ultima settimana di scorte, nessun Cushing, nessun proxy di curva: le feature relative restano NaN dichiarato e le strategie che le usano non operano |
| `FRED_API_KEY` | <https://fredaccount.stlouisfed.org/apikeys> | OVX, VIX, dollaro, tassi e breakeven con storico pieno e fonte ufficiale | Fallback su Yahoo e sui CSV di FRED: funziona, stato giallo |

Senza chiavi il sistema gira comunque: in un'esecuzione reale del 5 ottobre 2026 ha recuperato 23 voci su 25,
stato complessivo giallo, senza alcun crash (dettagli in `docs/DATA_SOURCES.md`).

## Il pulsante Reset

Il reset è un workflow (`.github/workflows/reset.yml`) con input di conferma `RESET`: archivia l'epoca corrente
in `epochs.json` con durata, equity massima e causa della fine, chiude le posizioni e riparte da 10.000 $.

Dalla dashboard, pagina **Reset**: se salvi nel tuo browser un token fine-grained con il solo permesso
*Actions: write* su questo repository, il pulsante avvia il workflow direttamente; altrimenti apre la pagina del
workflow su GitHub, dove parte con un clic. **Il token resta nel tuo browser e non finisce mai nel repository.**
Quando il conto è azzerato il pulsante diventa l'elemento principale della Home.

## Limiti, dichiarati

Il brief chiede onestà intellettuale, quindi:

- **Curva forward storica.** Yahoo non conserva le scadenze passate: la curva Brent reale viene archiviata da
  noi ogni giorno a partire dal go-live. Prima di allora la pendenza è un proxy (WTI C1-C4 dell'EIA, che richiede
  la chiave) ed è etichettata `approx` con il simbolo ≈ nella dashboard. Le strategie che vivono di curva (S5,
  S6, e il ramo fisico di S7) restano in incubazione finché non ci sono 120 giorni di curva reale.
- **Dated Brent.** Nessuna fonte gratuita: usiamo lo spot EIA come proxy, dichiarato.
- **Serie continua.** I 222 roll dal 2007 sono corretti con il rendimento dello spot in assenza di curva
  storica; ogni riga registra il metodo usato. Verifica del 2026-09-28: spread stimato −3,00 $ contro un M1−M2
  reale di −2,97 $.
- **Opzioni (S18).** Non ci sono dati di opzioni gratuiti: le strutture sono approssimazioni Black-76 con
  volatilità dall'OVX, hanno sempre peso zero nel portafoglio e sono etichettate come tali.
- **Intraday.** Yahoo è non ufficiale e ritardato di circa 15 minuti: il job da 30 minuti marca il conto su quel
  prezzo e lo dichiara.
- **Strategie non promosse.** Una strategia entra nel portafoglio solo se supera Deflated Sharpe, PBO, costi
  raddoppiati e numero minimo di operazioni. Quelle che non li superano restano a peso zero e la dashboard
  spiega perché.

## Sviluppo

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.lock
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy engine
.venv/bin/pytest -q                  # test offline e deterministici
.venv/bin/pytest -q -m network       # test che toccano le fonti reali (opzionali)

OPT_STATE_DIR=./state .venv/bin/python -m engine.cli fetch --snapshot
OPT_STATE_DIR=./state .venv/bin/python -m engine.cli eod --force
OPT_STATE_DIR=./state .venv/bin/python -m engine.cli export-site --out site-data
cd site && npm ci && npm run dev
```

Architettura, convenzioni e regole non negoziabili: `CLAUDE.md`. Requisiti originali: `docs/BRIEF.md`.
Piano e decisioni: `docs/PLAN.md`. Fonti verificate: `docs/DATA_SOURCES.md`. Strategie: `docs/STRATEGIES.md`.
Contratto JSON della dashboard: `docs/SITE_DATA.md`.

## Licenza e responsabilità

Codice a scopo didattico. I dati appartengono alle rispettive fonti e sono usati secondo i loro termini.
Nessuna garanzia sui risultati: è una simulazione, e una simulazione non è un rendimento.
