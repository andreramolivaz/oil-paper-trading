"""Automatic GitHub Issues for operational alerts (brief §15): stale data, liquidation/dead account, strategy
retirement, repeated job failures. Deduplicated by a stable marker in the issue title; uses the `gh` CLI with
GH_TOKEN (available in Actions). Offline-safe: without `gh` or a token it only logs.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from engine.core.store import StateStore

log = logging.getLogger(__name__)

MARKER = "[opt-alert]"


@dataclass(frozen=True)
class Alert:
    key: str  # stable id, e.g. "stale-data", "account-dead", "retired-S5"
    title: str
    body: str
    labels: tuple[str, ...] = ("automated",)

    @property
    def full_title(self) -> str:
        return f"{MARKER} {self.title} ({self.key})"


class GhClient:
    """Minimal wrapper over the gh CLI so tests can substitute a fake."""

    def __init__(self, repo: str):
        self.repo = repo
        self.available = shutil.which("gh") is not None

    def _run(self, args: list[str]) -> str:
        res = subprocess.run(["gh", *args, "--repo", self.repo], check=True, capture_output=True, text=True)
        return res.stdout

    def list_open(self) -> list[dict[str, Any]]:
        out = self._run(
            ["issue", "list", "--state", "open", "--search", MARKER, "--json", "number,title", "--limit", "100"]
        )
        return list(json.loads(out or "[]"))

    def create(self, title: str, body: str, labels: tuple[str, ...]) -> str:
        args = ["issue", "create", "--title", title, "--body", body]
        for lab in labels:
            args += ["--label", lab]
        try:
            return self._run(args).strip()
        except subprocess.CalledProcessError:
            # labels may not exist in the repo: retry without them
            return self._run(["issue", "create", "--title", title, "--body", body]).strip()

    def comment(self, number: int, body: str) -> None:
        self._run(["issue", "comment", str(number), "--body", body])

    def close(self, number: int, body: str) -> None:
        self._run(["issue", "close", str(number), "--comment", body])


class Alerter:
    def __init__(self, client: GhClient | None, store: StateStore):
        self.client = client
        self.store = store

    def raise_alerts(self, alerts: list[Alert], resolve_missing: bool = True) -> dict[str, str]:
        """Open one issue per new alert key, comment on existing ones at most once a day, close resolved ones."""
        actions: dict[str, str] = {}
        if self.client is None or not self.client.available:
            for a in alerts:
                log.warning("ALERT (no gh): %s — %s", a.full_title, a.body[:200])
                actions[a.key] = "logged"
            return actions
        open_issues = self.client.list_open()
        by_key: dict[str, dict[str, Any]] = {}
        for issue in open_issues:
            k = self._key_from_title(str(issue["title"]))
            if k:
                by_key[k] = issue
        last_comment = self.store.read_json("alerts_state.json", {}) or {}
        now = datetime.now(tz=UTC)
        for a in alerts:
            if a.key in by_key:
                last = last_comment.get(a.key)
                if last is None or now - datetime.fromisoformat(last) > timedelta(hours=24):
                    self.client.comment(
                        int(by_key[a.key]["number"]), f"Ancora attivo alle {now.isoformat()}\n\n{a.body}"
                    )
                    last_comment[a.key] = now.isoformat()
                    actions[a.key] = "commented"
                else:
                    actions[a.key] = "unchanged"
            else:
                self.client.create(a.full_title, a.body, a.labels)
                last_comment[a.key] = now.isoformat()
                actions[a.key] = "created"
        if resolve_missing:
            active = {a.key for a in alerts}
            for key, issue in by_key.items():
                if key not in active:
                    self.client.close(int(issue["number"]), f"Risolto automaticamente alle {now.isoformat()}.")
                    actions[key] = "closed"
                    last_comment.pop(key, None)
        self.store.write_json("alerts_state.json", last_comment)
        return actions

    @staticmethod
    def _key_from_title(title: str) -> str | None:
        if MARKER not in title or "(" not in title or not title.endswith(")"):
            return None
        return title[title.rfind("(") + 1 : -1]


# ----------------------------------------------------------------------------
# Alert detection from state


def detect_alerts(store: StateStore, now: datetime | None = None, stale_hours: float = 36.0) -> list[Alert]:
    now = now or datetime.now(tz=UTC)
    alerts: list[Alert] = []
    health = store.read_json("health.json", {}) or {}
    red = [s for s in health.get("sources", []) if s.get("status") == "red"]
    checked = health.get("checked_at")
    if checked:
        age_h = (now - datetime.fromisoformat(str(checked).replace("Z", "+00:00"))).total_seconds() / 3600
        if age_h > stale_hours:
            alerts.append(
                Alert(
                    "stale-data",
                    "Dati stantii: nessun aggiornamento riuscito",
                    f"L'ultimo controllo delle fonti risale a {checked} ({age_h:.0f} h fa). "
                    "Il trading non apre nuovo rischio.",
                )
            )
    # The same list the fetcher uses to decide whether new risk is allowed (it writes it into health.json): an
    # alert about a "critical" source that cannot actually stop anything would be noise.
    critical = set(health.get("critical") or ["brent_front", "wti_front", "bno_daily"])
    red_critical = [s for s in red if s.get("source") in critical or s.get("key") in critical]
    if red_critical:
        names = ", ".join(sorted({str(s.get("key") or s.get("source")) for s in red_critical}))
        alerts.append(
            Alert(
                "source-red",
                f"Fonti critiche in rosso: {names}",
                json.dumps(red_critical, ensure_ascii=False, indent=2),
            )
        )
    account = store.read_json("account.json", {}) or {}
    if account.get("status") == "dead":
        alerts.append(
            Alert(
                "account-dead",
                "Conto azzerato: trading fermo",
                f"Equity {account.get('equity')} $ sotto la soglia minima. "
                "Usare il pulsante Reset (workflow reset.yml) per ripartire da 10.000 $.",
            )
        )
    for book_dir in sorted((store.root / "desk").glob("*/")) if (store.root / "desk").is_dir() else []:
        book = StateStore(book_dir)
        state = book.read_json("account.json") or book.read_json("book.json") or {}
        if state.get("status") == "dead":
            alerts.append(
                Alert(
                    f"book-dead-{book_dir.name}",
                    f"Libro «{book_dir.name}» azzerato: fermo fino al reset",
                    f"Equity {state.get('last_equity', state.get('equity'))} $ sotto il 5% del capitale iniziale. "
                    f"Il libro riparte solo con il workflow reset.yml (libro: {book_dir.name}).",
                )
            )
    for s in (store.read_json("strategies.json", {}) or {}).get("strategies", []):
        if s.get("lifecycle") == "retired" and s.get("retired_at"):
            alerts.append(
                Alert(
                    f"retired-{s['id']}",
                    f"Strategia {s['id']} ritirata",
                    f"{s.get('name', '')}: decadimento rilevato ({s.get('retired_reason', 'CUSUM')}) "
                    f"il {s['retired_at']}.",
                )
            )
    runs = store.tail_jsonl("runs", 10)
    failures = [r for r in runs if r.get("status") == "failed"]
    if len(failures) >= 3 and all(r.get("status") == "failed" for r in runs[-3:]):
        alerts.append(
            Alert(
                "job-failures",
                "Tre esecuzioni consecutive fallite",
                json.dumps(runs[-3:], ensure_ascii=False, indent=2),
            )
        )
    return alerts


def cron_lag_minutes(
    store: StateStore, job: str, expected_interval_min: int, now: datetime | None = None
) -> float | None:
    """Minutes since the last successful run of `job` beyond the expected interval (0 if on time); None if never ran."""
    now = now or datetime.now(tz=UTC)
    runs = [r for r in store.iter_jsonl("runs") if r.get("job") == job and r.get("status") == "ok"]
    if not runs:
        return None
    last = datetime.fromisoformat(str(runs[-1]["finished"]).replace("Z", "+00:00"))
    lag = (now - last).total_seconds() / 60 - expected_interval_min
    return max(0.0, lag)
