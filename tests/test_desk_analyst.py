"""The Claude analyst (phase 17, ``engine/desk/analyst.py``, docs/RESEARCH.md chapter 12): two calls a day to the
Messages API, archived with their sources, usage and cost; a budget; the reading the books on it read; and the books
that buy it, without touching the desk's forecast, the replay or the fingerprint.

No reading exists yet (the key is not among the repository's secrets), so the API's answers here are SYNTHETIC: the
shape of the documented responses (``server_tool_use``, ``web_search_tool_result``, text blocks with
``web_search_result_location`` citations, a JSON text block for the structured output), with made-up news.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from engine.core.config import RiskConfig
from engine.core.store import StateStore
from engine.desk import analyst
from engine.desk.book import Book, BookConfig, load_books
from engine.desk.live import TickReport, analyst_forecasts, build_desk, desk_store
from engine.desk.report import replayed_books

RISK = RiskConfig.load()
CFG = analyst.AnalystConfig()
FRIDAY = datetime(2026, 10, 16, 13, 20, tzinfo=UTC)  # a US business day, after 13:00 UTC
KEY = "sk-ant-test-0000"  # a fake key: it must appear nowhere the desk writes
OIL = BookConfig(
    id="claude_oil", name="Claude oil", vehicle="MCL", vol_target=0.50, max_leverage=10.0, weekend_max_leverage=3.0,
    role="candidate", forecast="claude", sleeves={"oil": 1.0}, daily_loss_breaker=0.15,
)  # fmt: skip
EQUITIES = BookConfig(
    id="claude_equities", name="Claude equities", vehicle="SPY", vol_target=0.50, max_leverage=2.0,
    weekend_max_leverage=1.0, long_only=True, role="candidate", forecast="claude", sleeves={"equities": 1.0},
    daily_loss_breaker=0.15,
)  # fmt: skip


def result(url: str, title: str) -> dict:
    return {"type": "web_search_result", "url": url, "title": title, "encrypted_content": "x", "page_age": None}


def cited(text: str, url: str, title: str) -> dict:
    """A passage with its citation, as the API splits it."""
    return {
        "type": "text",
        "text": text,
        "citations": [{"type": "web_search_result_location", "url": url, "title": title, "cited_text": "..."}],
    }


def brief_response(
    stop: str = "end_turn",
    text: str = "Oil: Brent rose 2% on 15 October after ...",
    results: tuple[tuple[str, str], ...] = (("https://example.com/a", "A"), ("https://example.com/b", "B")),
) -> dict:
    """SYNTHETIC: a search turn as the API documents it - what the model says while searching, a search and its
    results, then the passage it wrote, citing the second result."""
    return {
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will search for the day's news."},
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "Brent news"}},
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": [result(u, t) for u, t in results],
            },
            cited(text, results[-1][0], results[-1][1]),
        ],
        "stop_reason": stop,
        "usage": {"input_tokens": 12_000, "output_tokens": 800, "server_tool_use": {"web_search_requests": 3}},
    }


FORECAST = {
    "oil": {"forecast": 8, "confidence": "medium", "reasons": ["a", "b"], "risk": "a ceasefire"},
    "gold": {"forecast": -3, "confidence": "low", "reasons": ["c"], "risk": "the dollar"},
    "equities": {"forecast": 0, "confidence": "low", "reasons": [], "risk": "none"},
    "bonds": {"forecast": 35, "confidence": "high", "reasons": ["d"], "risk": "inflation"},
}


def forecast_response(payload: dict | None = None, stop: str = "end_turn") -> dict:
    """SYNTHETIC: a structured-output answer, the JSON in the text block."""
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": json.dumps(payload or FORECAST)}],
        "stop_reason": stop,
        "usage": {"input_tokens": 1_500, "output_tokens": 400},
    }


class Fake:
    """A transport that answers from a list and keeps every request it was sent."""

    def __init__(self, *answers: dict | Exception):
        self.answers = list(answers)
        self.sent: list[dict] = []
        self.seconds: list[float] = []

    def __call__(self, payload: dict, seconds: float) -> dict:
        self.sent.append(json.loads(json.dumps(payload)))
        self.seconds.append(seconds)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


# ------------------------------------------------------------------------------------------- the reading
def test_a_reading_is_two_calls_archived_with_its_sources_usage_and_cost(tmp_path):
    store = StateStore(tmp_path)
    send = Fake(brief_response(), forecast_response())
    out = analyst.maybe_read(store, CFG, FRIDAY, send, readers=True)
    assert out == {
        "status": "ok",
        "error": None,
        "markets": {"oil": 8.0, "gold": -3.0, "equities": 0.0, "bonds": 20.0},
        "cost_usd": 0.069,
    }
    # 12 000 x 2 + 800 x 10 + 1 500 x 2 + 400 x 10 per million tokens, and 3 searches at a cent: 0.039 + 0.03
    brief, forecast = send.sent
    assert brief["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
    assert "output_config" not in brief and brief["model"] == "claude-sonnet-5-5"
    assert "2026-10-16 (Friday), 13:20 UTC" in brief["system"] and "no advice" in brief["system"]
    # the news since the previous business day's morning, and at least 48 hours back
    assert "the news since Wednesday 14 October, 13:20 UTC that matter" in brief["system"]
    assert brief["max_tokens"] == 16000 and forecast["max_tokens"] == 16000  # room for the thinking
    assert all(0 < x <= CFG.timeout_s for x in send.seconds)  # each request waits only for what is left
    # the forecast call reads the brief alone, with no tool, and asks for the schema
    assert "tools" not in forecast and forecast["output_config"]["format"]["type"] == "json_schema"
    assert forecast["output_config"]["format"]["schema"] == analyst.SCHEMA
    assert "Oil: Brent rose 2%" in forecast["messages"][0]["content"]
    (record,) = analyst.records(store)
    assert record["status"] == "ok" and record["day"] == "2026-10-16" and record["searches"] == 3
    assert record["brief"] == "Oil: Brent rose 2% on 15 October after ..."  # what it said while searching is not
    assert record["prompt_version"] == analyst.prompt_version(CFG) and len(record["prompt_version"]) == 12
    # the cited page first, then the others the searches returned, each once
    assert [s["url"] for s in record["sources"]] == ["https://example.com/b", "https://example.com/a"]
    assert record["markets"]["bonds"] == {
        "forecast": 20.0,
        "confidence": "high",
        "reasons": ["d"],
        "risk": "inflation",
        "clamped": True,  # 35 is off the scale: cut to 20, and said
    }
    assert len(record["usage"]) == 2 and record["cost_usd"] == 0.069
    # one reading a day: the next tick of the day calls nothing
    again = Fake()
    assert analyst.maybe_read(store, CFG, FRIDAY + timedelta(hours=1), again, readers=True) == {
        "status": "not due",
        "why": "today's reading is on file",
    }
    assert again.sent == []


def test_a_paused_search_turn_is_sent_back_as_it_is_and_goes_on(tmp_path):
    first = brief_response(stop="pause_turn", text="Oil: ...")
    send = Fake(first, brief_response(text="Gold: ...", results=(("https://example.com/c", "C"),)), forecast_response())
    analyst.maybe_read(StateStore(tmp_path), CFG, FRIDAY, send, readers=True)
    resumed = send.sent[1]
    assert [m["role"] for m in resumed["messages"]] == ["user", "assistant"]
    assert resumed["messages"][1]["content"] == first["content"]  # the paused turn, unchanged
    # six searches for the whole reading: the first request used three, the continuation may use what is left
    assert [r["tools"][0]["max_uses"] for r in send.sent[:2]] == [6, 3]
    (record,) = analyst.records(StateStore(tmp_path))
    # the passage written between two searches cites a source: it is part of the brief; the narration is not
    assert record["brief"] == "Oil: ...\n\nGold: ..." and record["searches"] == 6
    # the pages cited, in order, then the others found
    assert [s["url"][-1] for s in record["sources"]] == ["b", "c", "a"]
    assert len(record["usage"]) == 3  # two search requests and the forecast, each billed


def test_the_brief_is_what_the_model_wrote_from_its_searches():
    narration = {"type": "text", "text": "Let me look for the gold news."}
    search = {"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}}
    found = {"type": "web_search_tool_result", "tool_use_id": "s", "content": []}
    # the API splits a passage around its citations: the blocks are joined back as they are
    final = [{"type": "text", "text": "Brent rose "}, cited("2%", "https://x.org", "X"), {"type": "text", "text": "."}]
    assert analyst._brief_text([[narration, search, found, *final]]) == "Brent rose 2%."
    # nothing written after the last search: all of the text, rather than no brief
    assert analyst._brief_text([[narration, search, found]]) == "Let me look for the gold news."
    assert analyst._brief_text([[search, found]]) == ""


def test_a_reading_never_takes_longer_than_its_time_or_searches_more_than_allowed(tmp_path, monkeypatch):
    # the deadline is set when the reading starts; a request is not started with less than 20 seconds left
    clock = iter([0.0, 1.0, CFG.timeout_s - 15.0])
    monkeypatch.setattr(analyst, "monotonic", lambda: next(clock))
    send = Fake(brief_response(stop="pause_turn"))
    out = analyst.maybe_read(StateStore(tmp_path), CFG, FRIDAY, send, readers=True)
    assert out is not None and out["status"] == "failed" and "no time left for the search" in out["error"]
    assert len(send.sent) == 1 and send.seconds == [CFG.timeout_s - 1.0]  # the continuation was not sent
    # a brief that took most of the time leaves no room for the forecast: the brief was billed, nothing else
    clock = iter([0.0, 1.0, CFG.timeout_s - 10.0])
    slow = Fake(brief_response())
    out = analyst.maybe_read(StateStore(tmp_path / "slow"), CFG, FRIDAY, slow, readers=True)
    assert out is not None and "no time left for the forecast" in out["error"] and out["cost_usd"] == 0.062
    monkeypatch.setattr(analyst, "monotonic", lambda: 0.0)
    # a turn paused after the six searches of the reading is not continued: no brief
    spent = brief_response(stop="pause_turn")
    spent["usage"]["server_tool_use"]["web_search_requests"] = 6
    out = analyst.maybe_read(StateStore(tmp_path / "six"), CFG, FRIDAY, Fake(spent), readers=True)
    assert out is not None and "paused after all 6 searches of the reading" in out["error"]
    # and a turn still paused after the continuations allowed is no brief
    paused = []
    for _ in range(1 + analyst.MAX_CONTINUATIONS):
        r = brief_response(stop="pause_turn")
        r["usage"]["server_tool_use"]["web_search_requests"] = 1
        paused.append(r)
    out = analyst.maybe_read(StateStore(tmp_path / "b"), CFG, FRIDAY, Fake(*paused), readers=True)
    assert out is not None and "still paused after 3 continuations" in out["error"]


def test_a_brief_cut_short_is_no_brief(tmp_path):
    """The registered rule: a truncated answer gives no forecast that day - the brief's as well as the forecast's."""
    for stop in ("max_tokens", "model_context_window_exceeded"):
        send = Fake(brief_response(stop=stop))
        out = analyst.maybe_read(StateStore(tmp_path / stop), CFG, FRIDAY, send, readers=True)
        assert out is not None and out["status"] == "failed" and f"cut short ({stop})" in out["error"]
        assert len(send.sent) == 1  # no forecast is asked of a brief cut short


def test_the_news_start_at_the_previous_business_day_s_morning_and_at_least_two_days_back():
    at = lambda *a: datetime(*a, tzinfo=UTC)  # noqa: E731
    # a Monday: Friday morning, not Saturday - the week's last session is in the brief
    assert analyst.news_since(at(2026, 10, 19, 13, 18)) == at(2026, 10, 16, 13, 0)
    # a Tuesday: 48 hours back (Sunday), which is before Monday's morning
    assert analyst.news_since(at(2026, 10, 20, 13, 18)) == at(2026, 10, 18, 13, 18)
    # the day after a holiday (Thanksgiving, Thursday 26 November): Wednesday's morning
    assert analyst.news_since(at(2026, 11, 27, 13, 18)) == at(2026, 11, 25, 13, 0)
    assert analyst._when_words(at(2026, 10, 16, 13, 0)) == "Friday 16 October, 13:00 UTC"


def test_an_attempt_a_failed_tick_discarded_is_put_back_and_never_paid_twice(tmp_path):
    """A failed tick resets the data branch's working tree, and a push that does not land is reset over by the next
    sync: the archive's line goes with them. The ledger, outside that tree, puts it back (found in review: eight
    ticks, sixteen paid requests, nothing on file, the budget at zero)."""
    ledger = tmp_path / "workspace" / "analyst-ledger.jsonl"
    store = StateStore(tmp_path / "state")
    out = analyst.maybe_read(store, CFG, FRIDAY, Fake(brief_response(), forecast_response()), True, ledger=ledger)
    assert out is not None and out["status"] == "ok" and len(analyst.ledger_rows(ledger)) == 1
    for f in (tmp_path / "state").glob("claude-*.jsonl"):
        f.unlink()  # what `git checkout -- . && git clean -fd` does to it
    again = Fake()
    out = analyst.maybe_read(store, CFG, FRIDAY + timedelta(minutes=30), again, True, ledger=ledger)
    assert out == {"status": "not due", "why": "today's reading is on file", "restored": 1} and again.sent == []
    (back,) = analyst.records(store)
    assert back["status"] == "ok" and back["markets"]["oil"]["forecast"] == 8.0 and back["cost_usd"] == 0.069
    # put back once: the next tick finds it in the archive
    assert analyst.maybe_read(store, CFG, FRIDAY + timedelta(hours=1), Fake(), True, ledger=ledger) == {
        "status": "not due",
        "why": "today's reading is on file",
    }
    # failures too: three paid attempts, then none, whatever happened to the archive in between
    boom = analyst.AnalystError("HTTP 529 overloaded_error: Overloaded")
    other = StateStore(tmp_path / "other")
    led2 = tmp_path / "workspace" / "other.jsonl"
    calls = 0
    for minutes in (0, 30, 60, 90, 120):
        f = Fake(brief_response(), boom)
        analyst.maybe_read(other, CFG, FRIDAY + timedelta(minutes=minutes), f, True, ledger=led2)
        calls += len(f.sent)
        for g in (tmp_path / "other").glob("claude-*.jsonl"):
            g.unlink()
    assert calls == 2 * 3 and len(analyst.ledger_rows(led2)) == 3


def test_a_message_is_scrubbed_of_the_key_before_it_is_cut(tmp_path):
    key = "sk-ant-" + "x" * 300
    assert analyst.scrub(f"bad header: {key}", key) == "bad header: ***"
    assert key[:40] not in analyst.scrub("a" * 280 + key, key)  # cut first, 20 characters of the key would stay

    def leaky(payload, seconds):
        raise RuntimeError(f"connection reset while sending {key}")

    out = analyst.maybe_read(StateStore(tmp_path), CFG, FRIDAY, leaky, True, secret=key)
    assert out is not None and key[:12] not in json.dumps(out) and "***" in out["error"]
    assert key[:12] not in "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())


def test_a_source_that_is_not_a_web_page_is_left_out():
    blocks = [
        {
            "type": "web_search_tool_result",
            "content": [result("javascript:alert(1)", "bad"), result("https://ok.org/a", "<b>ok</b>"), {"url": 3}],
        },
        cited("x", "data:text/html,hi", "bad"),
    ]
    assert analyst._sources(blocks) == [{"url": "https://ok.org/a", "title": "<b>ok</b>"}]  # the page escapes it


def test_a_failed_attempt_is_archived_with_what_was_billed_and_retried_at_most_three_times(tmp_path):
    store = StateStore(tmp_path)
    boom = analyst.AnalystError("HTTP 529 overloaded_error: Overloaded")
    out = analyst.maybe_read(store, CFG, FRIDAY, Fake(brief_response(), boom), readers=True)
    assert out is not None and out["status"] == "failed" and "529" in out["error"]
    (record,) = analyst.records(store)
    assert record["cost_usd"] == pytest.approx(0.024 + 0.008 + 0.03)  # the brief was billed, the failure was not
    # the next attempt waits half an hour, and there are three at most in a day
    assert analyst.due(analyst.records(store), CFG, FRIDAY + timedelta(minutes=29), True) == (
        False,
        "the last attempt was less than 30 minutes ago",
    )
    for minutes in (30, 60):
        analyst.maybe_read(store, CFG, FRIDAY + timedelta(minutes=minutes), Fake(boom), readers=True)
    assert len(analyst.records(store)) == 3
    assert analyst.due(analyst.records(store), CFG, FRIDAY + timedelta(hours=3), True) == (
        False,
        "3 attempts today, the most allowed",
    )
    # a refusal or a truncated answer is no forecast either
    for stop in ("refusal", "max_tokens"):
        other = StateStore(tmp_path / stop)
        analyst.maybe_read(other, CFG, FRIDAY, Fake(brief_response(), forecast_response(stop=stop)), readers=True)
        (r,) = analyst.records(other)
        assert r["status"] == "failed" and stop in r["error"] and r["markets"] == {}


def test_when_a_reading_is_due(tmp_path):
    rows: list[dict] = []
    assert analyst.due(rows, CFG, FRIDAY, True) == (True, "due")
    assert analyst.due(rows, CFG, datetime(2026, 10, 17, 14, tzinfo=UTC), True) == (False, "not a US business day")
    assert analyst.due(rows, CFG, datetime(2026, 10, 16, 12, 59, tzinfo=UTC), True) == (False, "before 13:00 UTC")
    assert analyst.due(rows, CFG, FRIDAY, False) == (False, "no book reads it")
    off = analyst.AnalystConfig(enabled=False)
    assert analyst.due(rows, off, FRIDAY, True) == (False, "disabled in config/analyst.yaml")
    # the month's budget: the spend of every attempt of the month, failures included
    spent = [{"day": "2026-10-01", "ts": "2026-10-01T13:18:00Z", "status": "ok", "cost_usd": 19.99}]
    assert analyst.due(spent, CFG, FRIDAY, True) == (True, "due")
    spent.append({"day": "2026-10-02", "ts": "2026-10-02T13:18:00Z", "status": "failed", "cost_usd": 0.02})
    assert analyst.due(spent, CFG, FRIDAY, True) == (False, "the month's budget is spent (20.01 of 20.00 $)")
    assert analyst.due(spent, CFG, datetime(2026, 11, 2, 13, 30, tzinfo=UTC), True) == (True, "due")  # a new month


def test_without_the_key_nothing_runs_and_nothing_is_written(tmp_path):
    store = StateStore(tmp_path)
    assert analyst.maybe_read(store, CFG, FRIDAY, None, readers=True) is None
    assert analyst.records(store) == [] and not list(tmp_path.glob("claude-*.jsonl"))


def test_the_forecast_is_cut_to_the_scale_and_what_is_not_a_market_is_left_out():
    text = json.dumps(
        {
            "oil": {"forecast": -27, "confidence": "high", "reasons": ["a", "b", "c", "d"], "risk": "r"},
            "copper": {"forecast": 5, "confidence": "low", "reasons": [], "risk": ""},  # not a market of the four
            "gold": {"forecast": True, "confidence": "low", "reasons": [], "risk": ""},  # not a number
            "equities": "flat",  # not an entry
            "bonds": {"forecast": 4.0, "confidence": "certain", "reasons": [" ", "x"], "risk": "y"},
        }
    )
    out = analyst.parse_forecast(text)
    assert set(out) == {"oil", "bonds"}
    assert out["oil"]["forecast"] == -20.0 and out["oil"]["clamped"] and out["oil"]["reasons"] == ["a", "b", "c"]
    assert out["bonds"] == {"forecast": 4.0, "confidence": None, "reasons": ["x"], "risk": "y", "clamped": False}
    with pytest.raises(analyst.AnalystError):
        analyst.parse_forecast("not json")
    with pytest.raises(analyst.AnalystError):
        analyst.parse_forecast(json.dumps(["buy"]))
    assert analyst.parse_forecast(json.dumps({"verdict": "buy"})) == {}  # no market: the reading says so and fails
    # the schema the API is asked for names every market, all required: none can be left out of a reading
    assert analyst.SCHEMA["required"] == list(analyst.MARKETS) and analyst.SCHEMA["additionalProperties"] is False
    assert set(analyst.SCHEMA["properties"]) == set(analyst.MARKETS)


def test_the_http_transport_says_what_the_api_said_and_never_the_key(monkeypatch):
    class Response:
        status_code = 401

        def json(self) -> dict:
            return {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}

    sent: dict = {}

    def post(self, url, json, timeout, headers):  # noqa: A002 - the name requests uses
        sent.update(url=url, headers=headers, body=json, timeout=timeout)
        return Response()

    monkeypatch.setattr("requests.Session.post", post)
    send = analyst.http_transport(KEY, timeout=420.0)
    with pytest.raises(analyst.AnalystError) as err:
        send({"model": "m"}, 30.0)
    assert str(err.value) == "HTTP 401 authentication_error: invalid x-api-key" and KEY not in str(err.value)
    assert sent["url"] == analyst.API_URL and sent["headers"]["x-api-key"] == KEY
    assert sent["timeout"] == 30.0  # what the reading has left, never more than the transport's own limit
    assert sent["headers"]["anthropic-version"] == "2023-06-01"


# ------------------------------------------------------------------------------------------- what the books read
def _ok(day: str, oil: float, ts: str | None = None) -> dict:
    return {
        "day": day,
        "ts": ts or f"{day}T13:18:00Z",
        "status": "ok",
        "model": "claude-sonnet-5-5",
        "markets": {
            "oil": {"forecast": oil, "confidence": "medium"},
            "equities": {"forecast": -6.0, "confidence": "low"},
        },
    }


def test_a_decision_reads_the_newest_reading_of_its_day_never_a_later_one_and_closes_on_an_old_one():
    rows = [_ok("2026-10-14", 4.0), _ok("2026-10-15", 8.0), _ok("2026-10-16", -5.0)]
    on15 = analyst.forecast_on(rows, date(2026, 10, 15))
    assert on15 is not None and on15.values == {"oil": 8.0, "equities": -6.0} and on15.age_days == 0
    assert on15.as_forecast() == {
        "oil": 8.0,
        "equities": -6.0,
        "oil:confidence": 2.0,
        "equities:confidence": 1.0,
        "claude:age_days": 0.0,
    }
    # a failed attempt is never read, and the weekend reads Friday's
    rows.append({"day": "2026-10-16", "ts": "2026-10-16T14:00:00Z", "status": "failed", "markets": {}})
    sunday = analyst.forecast_on(rows, date(2026, 10, 18))
    assert sunday is not None and sunday.values["oil"] == -5.0 and sunday.age_days == 2
    # more than three days old: no view, every market at zero, the books close
    old = analyst.forecast_on(rows, date(2026, 10, 20))
    assert old is not None and old.stale and old.values == dict.fromkeys(analyst.MARKETS, 0.0)
    assert "4 days old" in old.note
    # nothing on or before the day: no forecast at all
    assert analyst.forecast_on(rows, date(2026, 10, 13)) is None and analyst.forecast_on([], date(2026, 10, 16)) is None


def test_a_claude_book_buys_the_reading_of_its_market_and_says_where_it_comes_from():
    ts = datetime(2026, 10, 16, 18, 35, tzinfo=UTC)
    oil = Book(OIL, RISK)
    forecast = analyst.forecast_on([_ok("2026-10-16", 16.0)], date(2026, 10, 16))
    assert forecast is not None
    dec, order = oil.decide(ts, date(2026, 10, 16), "MCLZ26", 95.7, forecast.as_forecast(), 0.30)
    assert dec.forecast["combined"] == 16.0 and dec.exposure > 0 and order is not None and order.qty_bbl > 0
    assert order.strategies_for == ["oil"]
    assert dec.rationale.startswith(
        "The Claude analyst's reading of the news (today): crude oil +16 out of 20, confidence medium. "
    )
    # a long-only book on a negative reading stays in cash, and says why
    eq = Book(EQUITIES, RISK)
    dec2, order2 = eq.decide(ts, date(2026, 10, 16), "SPY", 670.0, forecast.as_forecast(), 0.15)
    assert dec2.exposure == 0.0 and order2 is None and "does not go short" in dec2.rationale
    # an old reading: no view, and the words say so
    stale = analyst.forecast_on([_ok("2026-10-10", 16.0)], date(2026, 10, 16))
    assert stale is not None
    dec3, _ = oil.decide(ts, date(2026, 10, 16), "MCLZ26", 95.7, stale.as_forecast(), 0.30)
    assert dec3.exposure == 0.0 and dec3.rationale.startswith("The Claude analyst's last reading is 6 days old")


def test_on_the_live_path_the_books_on_the_analyst_get_its_reading_and_nothing_else(tmp_path):
    desk = build_desk(tmp_path, RISK, [OIL, EQUITIES])
    report = TickReport(datetime(2026, 10, 16, 18, 48, tzinfo=UTC))
    books = list(desk.books.values())
    assert analyst_forecasts(desk, books, date(2026, 10, 16), report) == {}  # no reading yet: no forecast
    assert any("no reading of the Claude analyst yet" in n for n in report.notes)
    desk_store(tmp_path).append_jsonl(analyst.LOG, _ok("2026-10-16", 8.0), ts=datetime(2026, 10, 16, tzinfo=UTC))
    own = analyst_forecasts(desk, books, date(2026, 10, 16), report)
    assert set(own) == {"claude_oil", "claude_equities"} and own["claude_oil"]["oil"] == 8.0
    assert own["claude_equities"]["equities"] == -6.0 and own["claude_oil"]["claude:age_days"] == 0.0


def test_the_books_on_file_are_the_registered_ones_and_stay_out_of_the_replay():
    books = {b.id: b for b in load_books()}
    claude = [b for b in books.values() if b.forecast == analyst.FAMILY]
    assert [b.id for b in claude] == ["claude_oil", "claude_gold", "claude_equities", "claude_bonds"]
    assert {b.id for b in replayed_books(list(books.values()))} & {b.id for b in claude} == set()
    assert analyst.SLEEVES == ("oil", "gold", "equities", "bonds")
    cfg = analyst.AnalystConfig.load()
    assert (cfg.model, cfg.max_searches, cfg.monthly_budget_usd, cfg.start_utc.hour) == (
        "claude-sonnet-5-5",
        6,
        20.0,
        13,
    )
    assert (cfg.price_input_per_mtok, cfg.price_output_per_mtok, cfg.price_per_search) == (2.0, 10.0, 0.01)


def test_the_tick_runs_the_analyst_only_with_a_key_and_the_key_is_written_nowhere(tmp_path, monkeypatch):
    import dataclasses

    from engine.core.config import Settings
    from engine.live import jobs

    settings = dataclasses.replace(
        Settings.from_env(), state_dir=tmp_path, offline=False, anthropic_api_key=None, analyst_ledger=None
    )
    runner = type("Runner", (), {"settings": settings})()
    desk = build_desk(tmp_path, RISK, [OIL])
    store = desk_store(tmp_path)
    assert jobs._analyst_step(runner, desk, FRIDAY) == {}  # no key: no call, nothing archived
    assert analyst.records(store) == []
    # the tick's word for the page: it looked, it had no key, a book reads the analyst
    status = analyst.read_status(store)
    assert status is not None and status["key_set"] is False and status["readers"] is True and status["step"] is None
    keyed = type("Runner", (), {"settings": dataclasses.replace(settings, anthropic_api_key=KEY)})()
    calls = Fake(brief_response(), forecast_response())
    monkeypatch.setattr(analyst, "http_transport", lambda key, timeout=420.0: calls)
    out = jobs._analyst_step(keyed, desk, FRIDAY)
    assert out["analyst"]["status"] == "ok" and len(calls.sent) == 2
    # with a ledger in the settings (the runner's workspace), the attempt is written there too
    ledger = tmp_path.parent / f"{tmp_path.name}-ledger.jsonl"
    led = type("Runner", (), {"settings": dataclasses.replace(keyed.settings, analyst_ledger=ledger)})()
    monkeypatch.setattr(
        analyst, "http_transport", lambda key, timeout=420.0: Fake(brief_response(), forecast_response())
    )
    assert jobs._analyst_step(led, desk, FRIDAY + timedelta(days=3))["analyst"]["status"] == "ok"
    assert [r["day"] for r in analyst.ledger_rows(ledger)] == ["2026-10-19"] and KEY not in ledger.read_text()
    assert analyst.read_status(store)["key_set"] is True and analyst.read_status(store)["step"] == {"status": "ok"}
    offline = type("Runner", (), {"settings": dataclasses.replace(keyed.settings, offline=True)})()
    assert jobs._analyst_step(offline, desk, FRIDAY + timedelta(days=3)) == {}  # tests and CI never call out
    # a desk without a book on the analyst never calls it
    plain = build_desk(tmp_path / "plain", RISK, [b for b in load_books() if b.id == "prudente"])
    assert jobs._analyst_step(keyed, plain, FRIDAY + timedelta(days=3)) == {
        "analyst": {"status": "not due", "why": "no book reads it"}
    }

    # a step that breaks says so without the key, even when the error carries it
    def broken(*_a, **_k):
        raise RuntimeError(f"bad header x-api-key: {KEY}")

    monkeypatch.setattr(analyst, "maybe_read", broken)
    failed = jobs._analyst_step(keyed, desk, FRIDAY + timedelta(days=3))
    assert failed["analyst"]["status"].startswith("failed: RuntimeError") and KEY not in str(failed)
    written = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    assert KEY not in written and KEY not in repr(keyed.settings)


def test_the_page_block_says_whether_it_runs_what_it_read_and_what_it_cost(tmp_path):
    store = StateStore(tmp_path)
    books = [{"id": "claude_oil", "name": "Claude oil", "vehicle": "MCL", "sleeves": {"oil": 1}}]
    empty = analyst.block(store, CFG, date(2026, 10, 16), books, key_present=False)
    assert empty["configured"] is False and empty["reading"] is None and empty["spent_month_usd"] == 0.0
    assert (
        empty["state"] == "off"
        and empty["today"] is None
        and empty["state_text"]
        == ("The runner has no ANTHROPIC_API_KEY secret, so no reading is taken; the books stay in cash.")
    )
    # the runner's own word wins over the exporting process's: a job without the key does not say "off"
    analyst.write_status(store, FRIDAY, key_set=True, readers=True, outcome=None)
    waiting = analyst.block(store, CFG, date(2026, 10, 16), books, key_present=False)
    assert (
        waiting["state"] == "waiting" and waiting["key_set"] is True and waiting["checked_at"] == "2026-10-16T13:20:00Z"
    )
    assert waiting["state_text"].startswith("The first reading comes at the first tick after 13:00 UTC")
    analyst.maybe_read(store, CFG, FRIDAY, Fake(brief_response(), forecast_response()), readers=True)
    on = analyst.block(store, CFG, date(2026, 10, 16), books, key_present=False)
    assert on["state"] == "on" and on["state_text"] == "The books read the reading of 16 October (today)."
    assert on["today"] == {
        "day": "2026-10-16",
        "reading_day": "2026-10-16",
        "age_days": 0,
        "stale": False,
        "values": {"oil": 8.0, "gold": -3.0, "equities": 0.0, "bonds": 20.0},
    }
    analyst.maybe_read(
        store, CFG, FRIDAY + timedelta(days=3), Fake(analyst.AnalystError("HTTP 500 api_error: x")), readers=True
    )
    b = analyst.block(store, CFG, date(2026, 10, 19), books, key_present=False)
    assert b["configured"] is True
    assert b["reading"]["day"] == "2026-10-16" and b["reading"]["markets"]["oil"]["forecast"] == 8.0
    assert [s["url"] for s in b["reading"]["sources"]] == ["https://example.com/b", "https://example.com/a"]
    assert b["reading"]["n_sources"] == 2 and b["reading"]["searches"] == 3
    assert b["last_attempt"]["status"] == "failed" and "HTTP 500" in b["last_attempt"]["error"]
    assert b["spent_month_usd"] == 0.069 and b["readings"] == 1 and b["budget_usd"] == 20.0
    assert b["state"] == "on" and b["today"]["age_days"] == 3  # Monday reads Friday's
    assert "No backtest can exist" in b["rule"]["text"]
    # older than three days: the books close, and the block says it
    late = analyst.block(store, CFG, date(2026, 10, 21), books, key_present=False)
    assert late["state"] == "stale" and late["state_text"] == (
        "The analyst's last reading is 5 days old (more than 3): no view, the books close."
    )
    # the budget, spent
    analyst.write_status(store, FRIDAY, key_set=True, readers=True, outcome=None)
    rich = analyst.AnalystConfig(monthly_budget_usd=0.05)
    spent = analyst.block(store, rich, date(2026, 10, 19), books, key_present=True)
    assert spent["state"] == "budget" and spent["state_text"] == (
        "This month's budget is spent (0.07 of 0.05 $): no reading until next month; the books read the reading of "
        "16 October (3 days old)."
    )
    assert analyst.block(store, analyst.AnalystConfig(enabled=False), date(2026, 10, 19), books, True)["state"] == (
        "disabled"
    )


def test_a_reading_without_the_book_s_market_gives_that_book_no_forecast():
    oil_missing = {
        "day": "2026-10-16",
        "ts": "2026-10-16T13:18:00Z",
        "status": "ok",
        "markets": {"gold": {"forecast": 4}},
    }
    forecast = analyst.forecast_on([oil_missing], date(2026, 10, 16))
    assert forecast is not None
    book = Book(OIL, RISK)
    assert book.combined_forecast(forecast.as_forecast()) is None  # no value: the book holds what it has
    assert analyst.lead_text(forecast.as_forecast(), {"oil": 1.0}) == (
        "The Claude analyst's reading of the news (today): no forecast for crude oil in it. "
    )
    # and the decision says it in those words, not "not enough data" (found in review)
    ts = datetime(2026, 10, 16, 18, 35, tzinfo=UTC)
    dec, order = book.decide(ts, date(2026, 10, 16), "MCLZ26", 95.7, forecast.as_forecast(), 0.30)
    assert (
        order is None
        and dec.limited_by == "missing data"
        and dec.rationale
        == ("The Claude analyst's reading of the news (today): no forecast for crude oil in it. Position unchanged.")
    )
    # no reading at all: the book's entry of the forecasts is empty
    dec2, _ = book.decide(ts, date(2026, 10, 16), "MCLZ26", 95.7, {}, 0.30)
    assert dec2.rationale == "No reading of the Claude analyst: no forecast. Position unchanged."
