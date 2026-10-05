#!/usr/bin/env bash
# Helpers for the persistent `data` branch (state JSON, raw snapshots, site-data).
# Usage:
#   scripts/data_branch.sh checkout <dir>   # fetch the data branch into <dir> (creates an orphan branch if absent)
#   scripts/data_branch.sh push <dir> "<message>"   # commit and push <dir> back to the data branch (rebase-retry on conflict)
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
  *) echo "usage: $0 checkout|push <dir> [message]" >&2; exit 2;;
esac
