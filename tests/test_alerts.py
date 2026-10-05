from datetime import UTC, datetime, timedelta

from engine.monitoring.alerts import MARKER, Alert, Alerter, GhClient, cron_lag_minutes, detect_alerts


class FakeGh(GhClient):
    def __init__(self):
        self.repo = "x/y"
        self.available = True
        self.issues: list[dict] = []
        self.comments: list[tuple[int, str]] = []
        self.closed: list[int] = []
        self._n = 0

    def list_open(self):
        return [i for i in self.issues if i["number"] not in self.closed]

    def create(self, title, body, labels):
        self._n += 1
        self.issues.append({"number": self._n, "title": title})
        return f"https://github.com/x/y/issues/{self._n}"

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number, body):
        self.closed.append(number)


def test_alert_lifecycle(tmp_store):
    gh = FakeGh()
    al = Alerter(gh, tmp_store)
    a = Alert("stale-data", "Dati stantii", "body")
    assert al.raise_alerts([a]) == {"stale-data": "created"}
    assert gh.issues[0]["title"] == f"{MARKER} Dati stantii (stale-data)"
    # same alert within 24h -> unchanged, no comment
    assert al.raise_alerts([a]) == {"stale-data": "unchanged"}
    assert gh.comments == []
    # resolved -> closed
    assert al.raise_alerts([]) == {"stale-data": "closed"}
    assert gh.closed == [1]


def test_detect_alerts_from_state(tmp_store):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    tmp_store.write_json(
        "health.json",
        {
            "checked_at": (now - timedelta(hours=40)).isoformat(),
            "sources": [{"source": "yahoo", "key": "brent_front", "status": "red"}],
        },
    )
    tmp_store.write_json("account.json", {"status": "dead", "equity": 400.0})
    tmp_store.write_json(
        "strategies.json",
        {"strategies": [{"id": "S5", "name": "Spread", "lifecycle": "retired", "retired_at": "2026-10-01"}]},
    )
    for i in range(3):
        tmp_store.append_jsonl(
            "runs",
            {"job": "update", "status": "failed", "finished": (now - timedelta(minutes=30 * i)).isoformat()},
            ts=now,
        )
    keys = {a.key for a in detect_alerts(tmp_store, now)}
    assert keys == {"stale-data", "source-red", "account-dead", "retired-S5", "job-failures"}


def test_cron_lag(tmp_store):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    assert cron_lag_minutes(tmp_store, "update", 30, now) is None
    tmp_store.append_jsonl(
        "runs", {"job": "update", "status": "ok", "finished": (now - timedelta(minutes=95)).isoformat()}, ts=now
    )
    assert abs(cron_lag_minutes(tmp_store, "update", 30, now) - 65.0) < 1e-6
    tmp_store.append_jsonl(
        "runs", {"job": "update", "status": "ok", "finished": (now - timedelta(minutes=10)).isoformat()}, ts=now
    )
    assert cron_lag_minutes(tmp_store, "update", 30, now) == 0.0
