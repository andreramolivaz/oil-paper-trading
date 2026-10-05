#!/usr/bin/env bash
# Helpers for the persistent `data` branch (state JSON, raw snapshots, site-data).
# Usage:
#   scripts/data_branch.sh checkout <dir>   # fetch the data branch into <dir> (creates an orphan branch if absent)
#   scripts/data_branch.sh push <dir> "<message>"   # commit and push <dir> back to the data branch (rebase-retry on conflict)
set -euo pipefail
BRANCH="${DATA_BRANCH:-data}"
REMOTE="${DATA_REMOTE:-origin}"
cmd="${1:-}"; dir="${2:-state-repo}"
case "$cmd" in
  checkout)
    if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
      git clone --quiet --depth 1 --branch "$BRANCH" "$(git remote get-url "$REMOTE")" "$dir"
    else
      mkdir -p "$dir" && cd "$dir" && git init --quiet && git checkout --quiet --orphan "$BRANCH"
      git remote add "$REMOTE" "$(cd .. && git remote get-url "$REMOTE")"
      echo "# Stato persistente (branch dati)" > README.md
      echo "Questo branch contiene lo stato del paper trading: non modificare a mano." >> README.md
      git add README.md && git -c user.name="opt-bot" -c user.email="opt-bot@users.noreply.github.com" commit --quiet -m "init data branch"
      git push --quiet -u "$REMOTE" "$BRANCH"
    fi
    ;;
  push)
    msg="${3:-update state}"
    cd "$dir"
    git config user.name "opt-bot"; git config user.email "opt-bot@users.noreply.github.com"
    git add -A
    if git diff --cached --quiet; then echo "no state changes"; exit 0; fi
    git commit --quiet -m "$msg"
    for i in 1 2 3 4; do
      if git push --quiet "$REMOTE" "HEAD:$BRANCH"; then exit 0; fi
      echo "push failed, rebase and retry ($i)"; git fetch --quiet "$REMOTE" "$BRANCH" && git rebase --quiet "$REMOTE/$BRANCH" || { git rebase --abort || true; }
      sleep $((2 ** i))
    done
    echo "could not push state" >&2; exit 1
    ;;
  *) echo "usage: $0 checkout|push <dir> [message]" >&2; exit 2;;
esac
