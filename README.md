# Oil Paper Trading — Brent

Sistema di ricerca e **paper trading** sul petrolio. Scarica dati reali, calcola una previsione da trend e
curva dei future, e la fa comprare a quattro conti simulati da 10.000 $ che differiscono solo per quanta leva
usano. Tutto è pubblicato su un terminale di una pagina: <https://andreramolivaz.github.io/oil-paper-trading/>.

> **Simulazione a scopo di studio su dati reali. Nessun consiglio finanziario, nessun ordine reale, nessun
> broker collegato.** Ogni numero mostrato porta la sua fonte e il suo orario; quando un dato manca il sistema
> scrive che manca e non apre nuovo rischio.

## I quattro libri

| Libro | Strumento (com'è su Robinhood) | Rischio | Leva | Che cosa fa |
|---|---|---|---|---|
| **Prudente** | BNO, fondo sul Brent | un quarto di Kelly, 12% di volatilità | mai oltre 1x | compra il fondo quando trend e curva dicono long, altrimenti contanti |
| **Dinamico** | BNO a margine | mezzo Kelly, 25% | fino a 2x (1,5x nel fine settimana) | la stessa previsione, il doppio del rischio |
| **Spinto** | /MCL, future micro sul WTI (100 barili) | Kelly pieno, 50% | fino a 10x (3x nel fine settimana) | long e short; può azzerare il conto |
| **Opzioni** | spread di put su USO e BNO | 5% del conto per struttura, due strutture | rischio definito | vende una put sotto il mercato quando la previsione è lunga; sperimentale |

La previsione è una sola: trend (quattro incroci di medie mobili), carry (la curva è in backwardation o in
contango) e carry-momentum (la pendenza sta salendo o scendendo), a pesi uguali, su una scala da −20 a +20.
L'esposizione è `previsione / 10 × obiettivo di volatilità / volatilità di oggi`: **la leva non si sceglie, esce
da quel rapporto**. Con il greggio al 45% di volatilità anche il libro da 10x sta intorno a 1x.

Che cosa aspettarsi, dal backtest con costi reali (dettagli e limiti in `docs/VALIDATION.md`):

| | Rend. annuo | Volatilità | Sharpe | Perdita massima |
|---|---|---|---|---|
| Prudente (2011-2026) | +3,0% | 9% | 0,39 | −25% |
| Dinamico (2011-2026) | +5,1% | 17% | 0,37 | −47% |
| Spinto (1986-2026) | +10,6% | 49% | 0,45 | −89% |
| Brent comprato e tenuto (2011-2026) | +4,0% | 35% | 0,29 | −87% |

Sono numeri modesti e incerti: uno Sharpe di 0,4 su quindici anni non si distingue con sicurezza da zero. Il
momentum infragiornaliero, per cui il progetto era nato, **non ha mostrato margine dopo i costi** in nessuna
delle forme provate ed è stato lasciato fuori. Il perché, con i numeri e le fonti, è in `docs/RESEARCH.md`.

Il primo sistema (21 strategie su conti ombra, regimi, previsioni di prezzo) continua a girare ogni giorno e
resta consultabile dalla pagina **Archivio**: nessuna sua strategia ha superato la validazione, quindi il suo
conto è fermo per costruzione.

## Come funziona

```
GitHub Actions                        branch `data`                    GitHub Pages
┌───────────────────────────┐        ┌─────────────────┐              ┌──────────────────┐
│ runner   un giro ogni 30' │ ─────► │ state/…         │ ───────────► │ il terminale     │
│ watchdog lo riaccende     │ scrive │ site-data/*.json│  raw.github  │ legge desk.json  │
│ weekly   sabato           │        └─────────────────┘              └──────────────────┘
└───────────────────────────┘
```

- **`runner`** è un lavoro che resta acceso circa cinque ore, fa un giro ogni mezz'ora (ai minuti :18 e :48,
  quando l'ultima barra da 30 minuti è arrivata per intero anche su un flusso in ritardo) e alla fine avvia il
  proprio successore. Da domenica 17:30 a venerdì 18:30, ora di New York.
- **`watchdog`** è l'unico lavoro pianificato della settimana: controlla che un runner sia vivo e, se no, lo
  avvia. Le pianificazioni di GitHub partono con ore di ritardo: per questo non fanno più il lavoro, lo
  sorvegliano soltanto.
- **Un giro** (`python -m engine.cli tick`) scarica ciò che serve, esegue sugli ultimi prezzi gli ordini in
  coda, controlla margini e stop, rolla il future se è ora, prende la decisione del giorno se non è già stata
  presa, marca i conti e pubblica `desk.json`. Rifà anche la fine giornata del primo sistema quando una data di
  Londra è chiusa e non l'ha ancora. È idempotente: ripeterlo non duplica nulla.
- **`weekly`** (sabato) aggiorna le fonti lente, rifà backtest e validazione e comprime la storia del branch
  dati.

Un ordine deciso alle 15:18 di New York si esegue all'apertura della barra da 30 minuti successiva (le
15:30), con lo spread e le commissioni dello strumento: mai al prezzo che ha generato il segnale. L'eseguito
compare sul terminale un'ora dopo, quando quella barra è chiusa e il flusso l'ha consegnata tutta. Se il motore resta fermo,
alla ripartenza le barre arrivano nell'ordine e ai prezzi in cui sono avvenute: un ritardo sposta *quando* lo
vedi, non *che cosa* è successo.

## Configurazione (fatta il 5 ottobre 2026)

Le quattro cose che il codice non poteva fare da sé sono state completate e verificate su GitHub:

| Cosa | Dove | Stato |
|---|---|---|
| Repository pubblico | Settings → General | ✅ `raw.githubusercontent.com` risponde 200: la dashboard legge il branch `data` dal vivo |
| GitHub Pages | Settings → Pages → Source: **GitHub Actions** | ✅ online su <https://andreramolivaz.github.io/oil-paper-trading/> |
| Permessi dei workflow | Settings → Actions → General → Workflow permissions → **Read and write** | ✅ i job scrivono il branch dati e aprono le issue di allerta |
| Chiavi API gratuite | Settings → Secrets and variables → Actions | ✅ `EIA_API_KEY` e `FRED_API_KEY` presenti; `ALPHAVANTAGE_API_KEY` aggiunta l'8 ottobre 2026 (la conferma è la fonte `insider_form4` verde nel riquadro «Stato» del terminale) |

Le chiavi restano **opzionali**: senza di esse il sistema gira lo stesso, con più fonti sui fallback.

| Secret | Dove si ottiene | Cosa sblocca | Senza la chiave |
|---|---|---|---|
| `EIA_API_KEY` | <https://www.eia.gov/opendata/register.php> (istantaneo) | Storico completo delle scorte settimanali, scorte di **Cushing**, previsione STEO, proxy storico della curva (WTI C1-C4) | Solo l'ultima settimana di scorte, nessun Cushing, nessun proxy di curva: le feature relative restano NaN dichiarato e le strategie che le usano non operano |
| `FRED_API_KEY` | <https://fredaccount.stlouisfed.org/apikeys> | OVX, VIX, dollaro, tassi e breakeven con storico pieno e fonte ufficiale | Fallback su Yahoo e sui CSV di FRED: funziona, stato giallo |
| `ALPHAVANTAGE_API_KEY` | <https://www.alphavantage.co/support/#api-key> (istantaneo) | Le dichiarazioni Form 4 degli insider del settore, per la strategia S21 del primo sistema | La tabella resta vuota e S21 non opera. I quattro libri non la usano |

Senza chiavi il sistema gira comunque: in un'esecuzione reale del 5 ottobre 2026 ha recuperato 23 voci su 25,
stato complessivo giallo, senza alcun crash (dettagli in `docs/DATA_SOURCES.md`).

## Il pulsante Reset

Il reset è un workflow (`.github/workflows/reset.yml`) con input di conferma `RESET` e la scelta di che cosa
azzerare: un libro (`prudente`, `dinamico`, `spinto`, `opzioni`) oppure tutto. Archivia la vita corrente,
chiude le posizioni all'ultimo prezzo e riparte da 10.000 $.

Dal sito, pagina **Reset**: se salvi nel tuo browser un token fine-grained con il solo permesso
*Actions: write* su questo repository, il pulsante avvia il workflow direttamente; altrimenti apre la pagina del
workflow su GitHub, dove parte con un clic. **Il token resta nel tuo browser e non finisce mai nel repository.**

## Se il motore si ferma

Il terminale lo dice in alto («motore in ritardo» o «motore fermo»). Ogni runner avvia il proprio successore;
se la catena si spezza la riaccende il watchdog, che però è su una pianificazione di GitHub e può tardare anche
di ore. Per farlo subito: **Actions → runner → Run workflow**. Non c'è nulla da recuperare a mano: al primo
giro il motore riprende da dove era rimasto, e le barre arrivate nel frattempo vengono lette nell'ordine e ai
prezzi in cui sono avvenute.

Nell'elenco delle esecuzioni un runner «cancelled» è normale: ogni modifica al motore su `main` ferma quello
in corso e ne avvia uno sul codice nuovo.

## Limiti, dichiarati

- **Un solo mercato, poca storia.** Quindici anni per il fondo Brent, quaranta per il WTI: gli errori standard
  sono larghi quanto i risultati.
- **Su Robinhood non c'è un future sul Brent.** La leva passa dal WTI: il libro spinto porta anche il rischio
  che i due greggi si muovano diversamente.
- **Un contratto è quasi tutto il conto.** 100 barili a 90 $ sono 0,9 volte 10.000 $: il libro spinto tiene
  zero o un contratto finché la previsione non è forte, e lo scrive in ogni decisione.
- **Prezzi in ritardo.** Yahoo (non ufficiale) è in ritardo di 10-15 minuti, le opzioni Cboe di 15. Gli ordini
  simulati si eseguono su quei prezzi, con uno spread prudente.
- **Curva del Brent.** Yahoo non conserva le scadenze passate: la pendenza reale del Brent esiste da quando il
  motore la archivia. Prima, per il fondo si usa quella del WTI, marcata `approx` (≈).
- **Opzioni.** Il backtest del libro delle opzioni è un modello, non uno storico di quotazioni: gratis non
  esiste (quello di Alpha Vantage è riservato ai piani a pagamento, verificato l'8 ottobre 2026). Assegnazione
  anticipata e rischio a scadenza non sono simulati.
- **Il fine settimana.** Uno stop non ferma un'apertura in gap, e nel 2026 ce ne sono state fino a +16%.

## Sviluppo

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.lock
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy engine
.venv/bin/pytest -q                  # test offline e deterministici
.venv/bin/pytest -q -m network       # test che toccano le fonti reali (opzionali)

OPT_STATE_DIR=./state .venv/bin/python -m engine.cli tick            # un giro completo: scarica, esegue, decide, marca
OPT_STATE_DIR=./state .venv/bin/python -m engine.cli desk-backtest   # backtest dei libri sullo storico archiviato
OPT_STATE_DIR=./state .venv/bin/python -m engine.cli export-site --out site-data
cd site && npm ci && npm run dev
```

Ricerca, numeri e fonti: `docs/RESEARCH.md`. Regole dei libri e schede delle strategie: `docs/STRATEGIES.md`.
Backtest e che cosa garantiscono i test: `docs/VALIDATION.md`. Fonti dati verificate: `docs/DATA_SOURCES.md`.
Contratto JSON del sito: `docs/SITE_DATA.md`. Piano e decisioni: `docs/PLAN.md`. Architettura e regole non
negoziabili: `CLAUDE.md`. Requisiti originali: `docs/BRIEF.md`.

## Licenza e responsabilità

Codice a scopo didattico. I dati appartengono alle rispettive fonti e sono usati secondo i loro termini.
Nessuna garanzia sui risultati: è una simulazione, e una simulazione non è un rendimento.
