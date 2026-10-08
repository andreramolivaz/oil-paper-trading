#!/usr/bin/env bash
# Helpers for the persistent `data` branch (state JSON, raw snapshots, site-data).
# Usage:
#   scripts/data_branch.sh checkout <dir>   # fetch the data branch into <dir> (creates an orphan branch if absent)
#   scripts/data_branch.sh push <dir> "<message>"   # commit and push <dir> back to the data branch (rebase-retry on conflict)
#   scripts/data_branch.sh sync <dir>     # bring an existing checkout up to the remote branch (the runner loop calls it before every tick)
#   scripts/data_branch.sh squash <dir>   # replace the branch history with ONE commit of the current tree (weekly)
#
# Why squash exists: every tick rewrites parquet and JSON files, and git keeps every old version for ever. At 48
# ticks a day that is tens of megabytes of history a day for files nobody will ever read again; the audit trail
# that matters (orders, fills, equity, decisions, runs) lives INSIDE the files as append-only logs. Once a week
# the history is collapsed to the current tree and force-pushed with a lease, so a concurrent push is never lost.
#
# The clone is a SEPARATE repository from the workspace, so it does not inherit the credentials
# actions/checkout installed there: on a private repo an unauthenticated clone stops at a username prompt.
# The token (GH_TOKEN, or GITHUB_TOKEN) is therefore woven into the URL for the network calls only, and the
# stored remote is left clean so it never lands in .git/config or in a log.
set -euo pipefail
BRANCH="${DATA_BRANCH:-data}"
REMOTE="${DATA_REMOTE:-origin}"
cmd="${1:-}"; dir="${2:-state-repo}"

plain_url() { git remote get-url "$REMOTE"; }

auth_url() {
  local url token
  url="$(plain_url)"
  token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  if [ -n "$token" ] && [ "${url#https://github.com/}" != "$url" ]; then
    printf 'https://x-access-token:%s@github.com/%s' "$token" "${url#https://github.com/}"
  else
    printf '%s' "$url"
  fi
}

case "$cmd" in
  checkout)
    url="$(auth_url)"; clean="$(plain_url)"
    if git ls-remote --exit-code --heads "$url" "$BRANCH" >/dev/null 2>&1; then
      git clone --quiet --depth 1 --branch "$BRANCH" "$url" "$dir"
      git -C "$dir" remote set-url "$REMOTE" "$clean"
    else
      mkdir -p "$dir" && cd "$dir" && git init --quiet && git checkout --quiet --orphan "$BRANCH"
      git remote add "$REMOTE" "$clean"
      echo "# Stato persistente (branch dati)" > README.md
      echo "Questo branch contiene lo stato del paper trading: non modificare a mano." >> README.md
      git add README.md && git -c user.name="opt-bot" -c user.email="opt-bot@users.noreply.github.com" commit --quiet -m "init data branch"
      git push --quiet "$url" "HEAD:$BRANCH"
      git branch --quiet --set-upstream-to="$REMOTE/$BRANCH" "$BRANCH" 2>/dev/null || true
    fi
    ;;
  push)
    msg="${3:-update state}"
    cd "$dir"
    url="$(auth_url)"
    git config user.name "opt-bot"; git config user.email "opt-bot@users.noreply.github.com"
    git add -A
    if git diff --cached --quiet; then echo "no state changes"; exit 0; fi
    git commit --quiet -m "$msg"
    for i in 1 2 3 4; do
      if git push --quiet "$url" "HEAD:$BRANCH"; then exit 0; fi
      echo "push failed, rebase and retry ($i)"
      git fetch --quiet "$url" "$BRANCH" && git rebase --quiet FETCH_HEAD || { git rebase --abort || true; }
      sleep $((2 ** i))
    done
    echo "could not push state" >&2; exit 1
    ;;
  sync)
    cd "$dir"
    url="$(auth_url)"
    git fetch --quiet --depth 1 "$url" "$BRANCH"
    if git diff --quiet && git diff --cached --quiet && [ -z "$(git status --porcelain)" ]; then
      # nothing local: just take whatever is on the branch now (a reset job may have pushed in the meantime)
      git reset --quiet --hard FETCH_HEAD
    else
      echo "local state changes present: keeping them, the next push will rebase"
    fi
    ;;
  squash)
    cd "$dir"
    url="$(auth_url)"
    git config user.name "opt-bot"; git config user.email "opt-bot@users.noreply.github.com"
    git fetch --quiet --depth 1 "$url" "$BRANCH"
    remote_sha="$(git rev-parse FETCH_HEAD)"
    if [ "$(git rev-parse HEAD)" != "$remote_sha" ]; then
      echo "local checkout is not at the remote head: sync first, nothing squashed" >&2; exit 0
    fi
    new="$(git commit-tree "HEAD^{tree}" -m "state snapshot $(date -u +%Y-%m-%dT%H:%MZ) (history squashed)")"
    if git push --quiet --force-with-lease="$BRANCH:$remote_sha" "$url" "$new:refs/heads/$BRANCH"; then
      git reset --quiet --hard "$new"
      echo "history squashed to one commit"
    else
      echo "branch moved while squashing: left as it is" >&2
    fi
    ;;
  *) echo "usage: $0 checkout|push|sync|squash <dir> [message]" >&2; exit 2;;
esac
