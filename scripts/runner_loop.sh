#!/usr/bin/env bash
# The runner: one long job that ticks every 30 minutes, instead of 48 scheduled jobs a day.
#
# Why: GitHub runs schedules "best effort". On this repository the */30 schedule fired about four times a day
# and the end-of-day one four to six hours late (docs/PLAN.md), which made an intraday loop impossible. A job
# that is already running sleeps to the next slot with the precision of `sleep`. It runs for a bit more than
# five hours (the hosted-runner limit is six), then the workflow starts its own successor; a scheduled watchdog
# restarts the chain if it ever breaks.
#
# Slots are minutes :18 and :48: the half-hour bars close at :00 and :30 and the feed is 10-15 minutes late, so
# three minutes later the last bar is complete and can be shown to the brokers (engine/desk/live.py,
# FEED_DELAY). Each tick is independent: a failed tick is logged and the loop carries on, and every tick starts
# from whatever is on the data branch at that moment, so a reset pushed from the site is picked up at once.
set -uo pipefail

DIR="${1:-state-repo}"
SECONDS_BUDGET="${RUNNER_SECONDS:-19200}"   # 5 h 20 min
SLOT_MINUTES=(18 48)
PY=".venv/bin/python"
END=$(( $(date +%s) + SECONDS_BUDGET ))

market_window() {
  # Globex crude trades from Sunday 18:00 to Friday 17:00 New York. The loop also covers the hour after the
  # Friday close so the last marks and the weekend caps are on the page. Exit code 0 = keep ticking.
  "$PY" - <<'PYCODE'
import sys
from datetime import datetime, UTC
from zoneinfo import ZoneInfo
ny = datetime.now(UTC).astimezone(ZoneInfo("America/New_York"))
wd, hm = ny.weekday(), ny.hour * 60 + ny.minute
open_ = (wd < 4) or (wd == 4 and hm < 18 * 60 + 30) or (wd == 6 and hm >= 17 * 60 + 30)
sys.exit(0 if open_ else 1)
PYCODE
}

sleep_to_next_slot() {
  local now minute second target wait best=3600
  now=$(date +%s); minute=$(( 10#$(date -u +%M) )); second=$(( 10#$(date -u +%S) ))
  for target in "${SLOT_MINUTES[@]}"; do
    wait=$(( (target - minute + 60) % 60 * 60 - second ))
    [ "$wait" -le 20 ] && wait=$(( wait + 3600 ))
    [ "$wait" -lt "$best" ] && best=$wait
  done
  [ $(( now + best )) -gt "$END" ] && best=$(( END - now ))
  [ "$best" -gt 0 ] && sleep "$best"
}

n=0
while [ "$(date +%s)" -lt "$END" ]; do
  if ! market_window; then
    echo "$(date -u +%FT%TZ) market closed for the weekend: runner stops, the watchdog restarts it on Sunday"
    break
  fi
  n=$(( n + 1 ))
  stamp="$(date -u +%Y%m%dT%H%M)"
  echo "::group::tick $n at $(date -u +%FT%TZ)"
  scripts/data_branch.sh sync "$DIR" || echo "sync failed: ticking on the local copy"
  if "$PY" -m engine.cli tick --job-id "${GITHUB_RUN_ID:-local}-$stamp"; then
    "$PY" -m engine.cli export-site --out "$DIR/site-data" || echo "export failed"
    scripts/data_branch.sh push "$DIR" "tick $stamp run ${GITHUB_RUN_ID:-local}" || echo "push failed: retried on the next tick"
  else
    echo "tick failed: state not pushed, the next tick starts again from the branch"
    git -C "$DIR" checkout --quiet -- . 2>/dev/null || true
    git -C "$DIR" clean --quiet -fd 2>/dev/null || true
  fi
  if [ $(( n % 4 )) -eq 1 ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
    "$PY" -m engine.cli alerts --repo "$GITHUB_REPOSITORY" || true
  fi
  echo "::endgroup::"
  sleep_to_next_slot
done
echo "runner finished after $n ticks"
