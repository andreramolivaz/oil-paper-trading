#!/usr/bin/env bash
# One-off: create the `data` branch with the current engine state and the dashboard JSON.
#
# The branch is an ORPHAN (no shared history with main) and carries only the state the engine produces, so the
# code history stays clean and the 48 daily state commits never touch main. The scheduled workflows then keep
# it up to date through scripts/data_branch.sh.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${DATA_BRANCH:-data}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cd "$REPO_ROOT"
test -d state || { echo "nessuna cartella state/: esegui prima 'engine.cli fetch' e 'engine.cli eod'" >&2; exit 1; }
test -d site-data || { echo "nessuna cartella site-data/: esegui prima 'engine.cli export-site'" >&2; exit 1; }

mkdir -p "$WORK/state" "$WORK/site-data"
# The state the dashboard and the next run need. Raw snapshots are copied too: they are the point-in-time
# archive (and the only record of the forward curve), already compacted to the last two plus one per ISO week.
cp -r state/. "$WORK/state/"
cp -r site-data/. "$WORK/site-data/"

cat > "$WORK/README.md" <<'TXT'
# Branch dati (non modificare a mano)

Questo branch contiene lo stato del paper trading scritto dai workflow schedulati:

- `state/` stato del motore: conto, posizioni, equity, operazioni, segnali, decisioni, previsioni, regimi,
  epoche, salute delle fonti e l'archivio point-in-time dei dati grezzi (`state/raw/`).
- `site-data/` i JSON compatti che la dashboard legge (contratto in `docs/SITE_DATA.md` sul branch `main`).

Il codice sta su `main`. Qui non c'è storia condivisa con `main`: è un branch orfano, così i commit di stato
(circa 48 al giorno) non toccano la storia del codice.
TXT

cd "$WORK"
git init --quiet -b "$BRANCH"
git remote add origin "$(cd "$REPO_ROOT" && git remote get-url origin)"
git add -A
git -c user.name="opt-bot" -c user.email="opt-bot@users.noreply.github.com" \
    commit --quiet -m "seed data branch: engine state and dashboard JSON"
git push --quiet --set-upstream origin "$BRANCH"
echo "branch $BRANCH pubblicato con $(find state site-data -type f | wc -l) file"
