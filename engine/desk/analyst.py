"""The Claude analyst (phase 17): a language model reads the day's news and gives four markets a direction.

The owner asked how to use his Claude API credits "to open and close positions". Everything here was written down in
``docs/RESEARCH.md`` (chapter 12) before the first reading. Once a day, on a US business day, at the first tick after
``start_utc``, two calls to the Messages API:

1. the BRIEF: the model searches the web (``web_search_20250305``, at most ``max_searches`` searches for the whole
   reading) and writes the facts since the previous US business day's morning - at least 48 hours - that matter for
   crude oil, gold, US equities and US Treasuries, each with its date and source; no forecast;
2. the FORECAST: the model reads that brief alone and answers with a JSON object (``output_config.format``): for
   each market (``MARKETS``, all four required) an integer from -20 to +20 for the next five sessions (+10 a normal
   conviction that it rises), a confidence, up to three reasons and the main risk.

The model thinks before it answers (adaptive thinking is the default of the model, at its default effort): the
thinking counts toward ``max_tokens``, so the limits leave room for it; an answer cut short is no reading.

Two calls, not one, because structured output does not combine with the citations a search produces. Every attempt
is one line of ``state/desk/claude-YYYY-MM.jsonl`` - the brief with its sources, the forecast, the usage and the cost
of each call - and the key never is. Without ``ANTHROPIC_API_KEY`` nothing runs and the books stay in cash; a monthly
budget stops the calls when it is spent. The same line also goes to a ledger outside the data branch's working tree
(``OPT_ANALYST_LEDGER``, the runner's workspace): a tick that fails, or a push that does not land, discards the tree,
and an attempt lost with it would be paid again; the next tick puts it back (``restore_from_ledger``).

The four candidate books with ``forecast: claude`` read one market each (``sleeves`` in ``config/books.yaml``) and
size it like every book: ``forecast / 10 x target x m / vol``. The decision of day D reads the newest reading taken on
day D or up to ``STALE_DAYS`` days before, never a later one; older than that, the forecast is 0 and the books close.

No backtest exists or can exist: a model trained on text up to a date knows the prices a replay of the past would ask
it to forecast (look-ahead bias, Glasserman and Lin 2023). The books are out of the replay (``report.replayed_books``)
and judged on live fills alone; the lifecycle never promotes them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

import requests

from engine.core.calendar import is_business_day
from engine.core.config import CONFIG_DIR, load_config
from engine.core.store import StateStore, dumps
from engine.core.timeutil import ensure_utc, iso, parse_iso

log = logging.getLogger(__name__)

FAMILY = "claude"
MARKETS: tuple[str, ...] = ("oil", "gold", "equities", "bonds")
SLEEVES = MARKETS  # one sleeve per market: a book names the market it trades in ``sleeves``
DEFAULT_WEIGHTS: dict[str, float] = {"oil": 1.0}
LOG = "claude"  # state/desk/claude-YYYY-MM.jsonl
# state/desk/claude_status.json: what the runner's last tick knew - whether it HAD a key (yes or no, never the key)
STATUS_FILE = "claude_status.json"
CONFIG = "analyst"  # config/analyst.yaml
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
SEARCH_TOOL = "web_search_20250305"  # the web search that runs without code execution
FORECAST_CAP = 20.0
STALE_DAYS = 3
MAX_ATTEMPTS = 3  # per UTC day
RETRY_AFTER = timedelta(minutes=30)
MAX_CONTINUATIONS = 3  # a long search turn the API pauses ("pause_turn") is sent back at most this many times
MIN_SECONDS_LEFT = 20.0  # a request is not started with less than this left of the reading's time
MAX_SOURCES = 20
MAX_TEXT = 300  # characters of one reason or risk kept
KEY_AGE = "claude:age_days"  # the forecast's extra keys, beside the markets' values
CONFIDENCE_LEVELS = ("low", "medium", "high")

MARKET_WORDS = {
    "oil": "crude oil",
    "gold": "gold",
    "equities": "the S&P 500",
    "bonds": "long Treasuries",
}

BRIEF_SYSTEM = (
    "You are the news researcher of a paper-trading study: simulated accounts, no real money, no advice to anyone. "
    "Today is {today} ({weekday}), {clock} UTC. Search the web for the news since {since} that matter for four "
    "markets: crude oil (Brent and WTI: OPEC+, the Strait of Hormuz and the conflict with Iran, US inventories, "
    "demand), gold, US equities (the S&P 500), and US Treasuries with the Federal Reserve. Use at most {searches} "
    "searches. Then write a factual brief: one short section per market, the most important facts first, each with "
    "its date and its source; say plainly when you found nothing new for a market. Facts only: no forecast, no "
    "opinion, no advice."
)
BRIEF_USER = "Write today's brief."
FORECAST_SYSTEM = (
    "You are the analyst of a paper-trading study: simulated accounts, no real money, no advice to anyone. Below is "
    "today's news brief. From the brief alone - not from anything you may remember about prices after it - give each "
    "of four markets a directional forecast for the next five trading sessions, an integer from -20 to +20: +10 is a "
    "normal conviction that the market rises, -10 that it falls, 0 is no view, +20 or -20 the maximum and rare. The "
    "number says how much the news tilts the odds, not how much you like the market: on most days the honest answer "
    "is small. The markets: oil (Brent and WTI crude), gold, equities (the S&P 500), bonds (long US Treasuries: "
    "positive means prices up, yields down). For each: the forecast, your confidence (low, medium or high), up to "
    "three reasons taken from the brief, and the main risk to the view."
)
FORECAST_USER = "Today's brief ({today}):\n\n{brief}"
MARKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "forecast": {
            "type": "integer",
            "description": "from -20 to +20 for the next five sessions; +10 a normal conviction that it rises",
        },
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "reasons": {"type": "array", "items": {"type": "string"}, "description": "up to three, from the brief"},
        "risk": {"type": "string", "description": "the main risk to the view"},
    },
    "required": ["forecast", "confidence", "reasons", "risk"],
    "additionalProperties": False,
}
# one entry per market, all four required: a market can never be left out of a reading (a book whose market went
# missing day after day would hold its position for ever - found in review)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": dict.fromkeys(MARKETS, MARKET_SCHEMA),
    "required": list(MARKETS),
    "additionalProperties": False,
}

# one request to the Messages API: the payload and the seconds it may take; the JSON answer, or AnalystError
Transport = Callable[[dict[str, Any], float], dict[str, Any]]


class AnalystError(RuntimeError):
    """A call that did not give a usable answer: an HTTP error, a refusal, a truncated or malformed answer."""


@dataclass(frozen=True)
class AnalystConfig:
    enabled: bool = True
    model: str = "claude-sonnet-5-5"
    start_utc: time = time(13, 0)
    max_searches: int = 6
    # the model thinks before it answers and the thinking counts toward max_tokens: room for both (found in review)
    max_tokens_brief: int = 16000
    max_tokens_forecast: int = 16000
    monthly_budget_usd: float = 20.0
    price_input_per_mtok: float = 2.0
    price_output_per_mtok: float = 10.0
    price_cache_write_per_mtok: float = 2.5
    price_cache_read_per_mtok: float = 0.10
    price_per_search: float = 0.01
    timeout_s: float = 420.0  # the whole reading, both calls: the tick waits for it

    @staticmethod
    def load(config_dir: Path | None = None) -> AnalystConfig:
        """``config/analyst.yaml``; the defaults above when the file is absent."""
        if not ((config_dir or CONFIG_DIR) / f"{CONFIG}.yaml").exists():
            return AnalystConfig()
        raw = load_config(CONFIG, config_dir)
        hh, mm = str(raw.get("start_utc", "13:00")).split(":")
        return AnalystConfig(
            enabled=bool(raw.get("enabled", True)),
            model=str(raw.get("model", "claude-sonnet-5-5")),
            start_utc=time(int(hh), int(mm)),
            max_searches=int(raw.get("max_searches", 6)),
            max_tokens_brief=int(raw.get("max_tokens_brief", 16000)),
            max_tokens_forecast=int(raw.get("max_tokens_forecast", 16000)),
            monthly_budget_usd=float(raw.get("monthly_budget_usd", 20.0)),
            price_input_per_mtok=float(raw.get("price_input_per_mtok", 2.0)),
            price_output_per_mtok=float(raw.get("price_output_per_mtok", 10.0)),
            price_cache_write_per_mtok=float(raw.get("price_cache_write_per_mtok", 2.5)),
            price_cache_read_per_mtok=float(raw.get("price_cache_read_per_mtok", 0.10)),
            price_per_search=float(raw.get("price_per_search", 0.01)),
            timeout_s=float(raw.get("timeout_s", 420)),
        )


def prompt_version(cfg: AnalystConfig) -> str:
    """The fingerprint of the rule: the model, the texts, the schema, the number of searches and the token limits. A
    reading carries it, so a change of any of them is visible in the archive - and is a new rule, registered first."""
    payload = {
        "model": cfg.model,
        "brief": [BRIEF_SYSTEM, BRIEF_USER],
        "forecast": [FORECAST_SYSTEM, FORECAST_USER],
        "schema": SCHEMA,
        "tool": SEARCH_TOOL,
        "searches": cfg.max_searches,
        "max_tokens": [cfg.max_tokens_brief, cfg.max_tokens_forecast],
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def http_transport(api_key: str, timeout: float = 420.0) -> Transport:
    """A function that sends one request to the Messages API and returns its JSON, waiting at most the seconds it is
    given (never more than ``timeout``). Errors say what the API said and never repeat a header: the key stays out
    of every log line and of the archive."""
    session = requests.Session()

    def send(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        try:
            response = session.post(
                API_URL,
                json=payload,
                timeout=max(1.0, min(timeout, seconds)),
                headers={"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"},
            )
        except requests.RequestException as exc:
            raise AnalystError(f"the request failed: {type(exc).__name__}") from None
        if response.status_code >= 400:
            try:
                err = (response.json() or {}).get("error") or {}
                what = f"{err.get('type', '')}: {err.get('message', '')}"
            except ValueError:
                what = response.text[:200]
            raise AnalystError(f"HTTP {response.status_code} {what}"[:300])
        try:
            body = response.json()
        except ValueError:
            raise AnalystError("the API answered something that is not JSON") from None
        if not isinstance(body, dict):
            raise AnalystError("the API answered something that is not a JSON object")
        return body

    return send


# ---------------------------------------------------------------------------------------------- the two calls
def call_cost(usage: Mapping[str, Any], cfg: AnalystConfig) -> float:
    """What one call cost, in dollars, from the usage the API reported and the model's published prices."""

    def n(key: str) -> float:
        v = usage.get(key)
        return float(v) if isinstance(v, int | float) and math.isfinite(v) else 0.0

    server = usage.get("server_tool_use") or {}
    searches = server.get("web_search_requests") if isinstance(server, dict) else 0
    tokens = (
        n("input_tokens") * cfg.price_input_per_mtok
        + n("output_tokens") * cfg.price_output_per_mtok
        + n("cache_creation_input_tokens") * cfg.price_cache_write_per_mtok
        + n("cache_read_input_tokens") * cfg.price_cache_read_per_mtok
    ) / 1e6
    return tokens + float(searches or 0) * cfg.price_per_search


def _web_url(value: Any) -> str | None:
    """A source's address when it is a web page (http or https), else None: the page links to it."""
    url = str(value or "").strip()
    return url if url.lower().startswith(("https://", "http://")) and len(url) <= 2000 else None


def _sources(blocks: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The pages the brief rests on: the ones its text cites first, then the others the searches returned; web
    addresses only, each once."""
    cited: list[dict[str, str]] = []
    found: list[dict[str, str]] = []
    for block in blocks:
        if block.get("type") == "text":
            for c in block.get("citations") or []:
                url = _web_url(c.get("url")) if isinstance(c, dict) else None
                if url:
                    cited.append({"url": url, "title": str(c.get("title") or "")[:MAX_TEXT]})
        elif block.get("type") == "web_search_tool_result":
            content = block.get("content")
            for r in content if isinstance(content, list) else []:
                url = _web_url(r.get("url")) if isinstance(r, dict) else None
                if url:
                    found.append({"url": url, "title": str(r.get("title") or "")[:MAX_TEXT]})
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for s in [*cited, *found]:
        if s["url"] not in seen:
            seen.add(s["url"])
            out.append(s)
    return out[:MAX_SOURCES]


def _brief_text(responses: list[list[dict[str, Any]]]) -> str:
    """The brief, from the content of the search turn's responses: the text the model wrote after its last search,
    and before it only the passages that cite a source - what it says while searching ("let me look for the gold
    news") cites nothing and is not part of the brief. A passage is a run of text blocks with no search between
    them (the API splits a passage into blocks around its citations: they are joined back as they are); passages
    are separated by a blank line. When nothing is left, all of the text."""
    passages: list[list[dict[str, Any]]] = []
    for content in responses:
        passages.append([])  # a new response starts a new passage
        for b in content:
            if b.get("type") == "text":
                passages[-1].append(b)
            elif passages[-1]:
                passages.append([])
    last = passages[-1] if passages else []
    kept = [p for p in passages[:-1] if any(b.get("citations") for b in p)] + [last]

    def joined(blocks: list[list[dict[str, Any]]]) -> str:
        texts = ("".join(str(b.get("text", "")) for b in p).strip() for p in blocks)
        return "\n\n".join(t for t in texts if t)

    return joined(kept) or joined(passages)


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def news_since(now: datetime) -> datetime:
    """Where the brief's news start: the morning (``13:00 UTC``) of the previous US business day, and at least 48
    hours back - on a Monday, Friday morning, so the week's last session is in the brief (found in review: "the last
    48 hours" on a Monday began on Saturday)."""
    now = ensure_utc(now)
    prev = now.date() - timedelta(days=1)
    while not is_business_day(prev, "US"):
        prev -= timedelta(days=1)
    return min(now - timedelta(hours=48), datetime.combine(prev, time(13, 0), tzinfo=UTC))


def _when_words(t: datetime) -> str:
    """'Friday 16 October, 13:00 UTC', whatever the locale of the runner."""
    return f"{WEEKDAYS[t.weekday()]} {_long_day(t.date())}, {t:%H:%M} UTC"


class Deadline:
    """The reading's time: every request gets what is left of ``seconds``, and none starts with less than
    ``MIN_SECONDS_LEFT`` - the tick waits for the reading, and its bars, fills and decisions must not wait long."""

    def __init__(self, seconds: float):
        self.end = monotonic() + seconds
        self.seconds = seconds

    def left(self, what: str) -> float:
        left = self.end - monotonic()
        if left < MIN_SECONDS_LEFT:
            raise AnalystError(f"no time left for {what}: the reading has {self.seconds:.0f} seconds in all")
        return left


def _searches(usages: Iterable[Mapping[str, Any]]) -> int:
    return sum(int(((u.get("server_tool_use") or {}).get("web_search_requests")) or 0) for u in usages)


def brief_call(
    send: Transport, cfg: AnalystConfig, now: datetime, usages: list[dict[str, Any]], deadline: Deadline | None = None
) -> dict[str, Any]:
    """The first call: search and write the facts. ``usages`` receives each request's usage as it comes, so that a
    failure later still leaves the cost of what was billed. A turn the API pauses is sent back as it is, at most
    ``MAX_CONTINUATIONS`` times, with the searches the reading has left (``max_searches`` is for the whole reading,
    not for each request); a brief cut short (``max_tokens``) or refused is no brief."""
    now = ensure_utc(now)
    deadline = deadline or Deadline(cfg.timeout_s)
    system = BRIEF_SYSTEM.format(
        today=now.date().isoformat(),
        weekday=WEEKDAYS[now.weekday()],
        clock=now.strftime("%H:%M"),
        since=_when_words(news_since(now)),
        searches=cfg.max_searches,
    )
    first = len(usages)
    messages: list[dict[str, Any]] = [{"role": "user", "content": BRIEF_USER}]
    content: list[dict[str, Any]] = []
    responses: list[list[dict[str, Any]]] = []
    stop = None
    for _ in range(1 + MAX_CONTINUATIONS):
        left = cfg.max_searches - _searches(usages[first:])
        if left < 1:
            raise AnalystError(f"the search was paused after all {cfg.max_searches} searches of the reading")
        response = send(
            {
                "model": cfg.model,
                "max_tokens": cfg.max_tokens_brief,
                "system": system,
                "messages": messages,
                "tools": [{"type": SEARCH_TOOL, "name": "web_search", "max_uses": left}],
            },
            deadline.left("the search"),
        )
        usages.append(dict(response.get("usage") or {}))
        responses.append([b for b in response.get("content") or [] if isinstance(b, dict)])
        content += responses[-1]
        stop = response.get("stop_reason")
        if stop != "pause_turn":
            break
        # a long search turn paused by the API: sent back as it is, it goes on where it stopped
        messages = [messages[0], {"role": "assistant", "content": content}]
    if stop == "refusal":
        raise AnalystError("the brief was refused")
    if stop in ("max_tokens", "model_context_window_exceeded"):
        raise AnalystError(f"the brief was cut short ({stop})")
    if stop == "pause_turn":
        raise AnalystError(f"the search was still paused after {MAX_CONTINUATIONS} continuations")
    text = _brief_text(responses)
    if not text:
        raise AnalystError(f"the brief has no text (stop: {stop})")
    return {"text": text, "sources": _sources(content), "stop": stop, "searches": _searches(usages[first:])}


def parse_forecast(text: str) -> dict[str, dict[str, Any]]:
    """The forecast call's JSON (``SCHEMA``: one entry per market) -> {market: {forecast, confidence, reasons, risk,
    clamped}}. A market missing or without a finite number is left out: no forecast for it that day (the schema
    requires all four, so this is the answer of a model that broke it). A number off the scale is cut to +-20 and
    says so."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AnalystError(f"the forecast is not JSON ({exc})") from None
    if not isinstance(data, dict):
        raise AnalystError("the forecast is not a JSON object")
    out: dict[str, dict[str, Any]] = {}
    for market in MARKETS:
        item = data.get(market)
        if not isinstance(item, dict):
            continue
        value = item.get("forecast")
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            continue
        cut = max(-FORECAST_CAP, min(FORECAST_CAP, float(value)))
        confidence = item.get("confidence")
        reasons = [str(r)[:MAX_TEXT] for r in item.get("reasons") or [] if isinstance(r, str) and r.strip()][:3]
        out[market] = {
            "forecast": cut,
            "confidence": confidence if confidence in CONFIDENCE_LEVELS else None,
            "reasons": reasons,
            "risk": str(item.get("risk") or "")[:MAX_TEXT],
            "clamped": cut != float(value),
        }
    return out


def forecast_call(
    send: Transport,
    cfg: AnalystConfig,
    brief: str,
    now: datetime,
    usages: list[dict[str, Any]],
    deadline: Deadline | None = None,
) -> dict[str, dict[str, Any]]:
    """The second call: the brief alone in, one JSON object out (structured output: no tools, no citations). An
    answer cut short may not match the schema: no forecast."""
    deadline = deadline or Deadline(cfg.timeout_s)
    response = send(
        {
            "model": cfg.model,
            "max_tokens": cfg.max_tokens_forecast,
            "system": FORECAST_SYSTEM,
            "messages": [
                {"role": "user", "content": FORECAST_USER.format(today=ensure_utc(now).date().isoformat(), brief=brief)}
            ],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
        },
        deadline.left("the forecast"),
    )
    usages.append(dict(response.get("usage") or {}))
    stop = response.get("stop_reason")
    if stop in ("refusal", "max_tokens", "model_context_window_exceeded"):
        raise AnalystError(f"the forecast stopped early ({stop}): its JSON may not match the schema")
    text = next((str(b.get("text", "")) for b in response.get("content") or [] if b.get("type") == "text"), "")
    return parse_forecast(text)


# ---------------------------------------------------------------------------------------------- the daily reading
def records(store: StateStore) -> list[dict[str, Any]]:
    """Every attempt on file, oldest first (a line cut short is skipped, as in the Kalshi archive)."""
    rows = [r for r in store.iter_jsonl(LOG, skip_bad=True) if isinstance(r, dict) and r.get("day")]
    return sorted(rows, key=lambda r: (str(r.get("day")), str(r.get("ts"))))


def _when(ts: Any) -> datetime | None:
    """A record's stamp, or None when it cannot be read (a line written by hand, cut short)."""
    if not ts:
        return None
    try:
        return parse_iso(str(ts))
    except ValueError:
        return None


def spent_in_month(rows: Iterable[Mapping[str, Any]], when: datetime) -> float:
    month = ensure_utc(when).strftime("%Y-%m")
    return round(sum((float(r.get("cost_usd") or 0.0) for r in rows if str(r.get("day", ""))[:7] == month), 0.0), 4)


def due(rows: list[dict[str, Any]], cfg: AnalystConfig, now: datetime, readers: bool) -> tuple[bool, str]:
    """Whether this tick takes the day's reading, and why not when it does not."""
    now = ensure_utc(now)
    today = now.date()
    if not cfg.enabled:
        return False, "disabled in config/analyst.yaml"
    if not readers:
        return False, "no book reads it"
    if not is_business_day(today, "US"):
        return False, "not a US business day"
    if now.time() < cfg.start_utc:
        return False, f"before {cfg.start_utc.strftime('%H:%M')} UTC"
    mine = [r for r in rows if r.get("day") == today.isoformat()]
    if any(r.get("status") == "ok" for r in mine):
        return False, "today's reading is on file"
    if len(mine) >= MAX_ATTEMPTS:
        return False, f"{len(mine)} attempts today, the most allowed"
    stamps = [t for t in (_when(r.get("ts")) for r in mine) if t is not None]
    last = max(stamps) if stamps else None
    if last is not None and now - last < RETRY_AFTER:
        return False, "the last attempt was less than 30 minutes ago"
    spent = spent_in_month(rows, now)
    if spent >= cfg.monthly_budget_usd:
        return False, f"the month's budget is spent ({spent:.2f} of {cfg.monthly_budget_usd:.2f} $)"
    return True, "due"


def scrub(text: str, secret: str | None, limit: int = MAX_TEXT) -> str:
    """A message for the archive or a log: the secret replaced FIRST, then the text cut to ``limit`` - cut first, a
    key split by the cut would survive the replacement (found in review)."""
    if secret:
        text = text.replace(secret, "***")
    return text[:limit]


def ledger_rows(path: Path | None) -> list[dict[str, Any]]:
    """The attempts written to the ledger, oldest first; a line cut short is skipped."""
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("day") and row.get("ts"):
            rows.append(row)
    return rows


def _append_ledger(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())


def restore_from_ledger(store: StateStore, path: Path | None) -> int:
    """Put back into the archive every attempt of the ledger the archive does not have - a tick that failed after the
    call, or a push that never landed, discarded the data branch's working tree with the line in it. The attempt was
    paid: the attempt cap and the budget must see it, and an ok reading must not be bought twice. Returns how many
    lines were put back, each in the month file of its own day."""
    have = {(str(r.get("day")), str(r.get("ts"))) for r in records(store)}
    restored = 0
    for row in ledger_rows(path):
        key = (str(row["day"]), str(row["ts"]))
        if key in have:
            continue
        store.append_jsonl(LOG, row, ts=_when(row["ts"]) or datetime.combine(date.fromisoformat(key[0]), time(12), UTC))
        have.add(key)
        restored += 1
    return restored


def maybe_read(
    store: StateStore,
    cfg: AnalystConfig,
    now: datetime,
    send: Transport | None,
    readers: bool,
    ledger: Path | None = None,
    secret: str | None = None,
) -> dict[str, Any] | None:
    """The tick's step: take the day's reading when it is due and archive the attempt, whatever happened - in the
    archive and in the ``ledger`` (outside the data branch's working tree, which a failed tick discards), whose lines
    the archive lacks are put back first. None without a key (nothing runs, nothing is written); otherwise a short
    summary for the tick's log. ``secret`` is scrubbed from every message written."""
    restored = restore_from_ledger(store, ledger) if ledger is not None else 0
    if send is None:
        return None
    now = ensure_utc(now)
    rows = records(store)
    ok, why = due(rows, cfg, now, readers)
    if not ok:
        out: dict[str, Any] = {"status": "not due", "why": why}
        if restored:
            out["restored"] = restored
        return out
    usages: list[dict[str, Any]] = []
    record: dict[str, Any] = {
        "day": now.date().isoformat(),
        "ts": iso(now),
        "model": cfg.model,
        "prompt_version": prompt_version(cfg),
        "status": "failed",
        "error": None,
        "brief": None,
        "sources": [],
        "markets": {},
    }
    deadline = Deadline(cfg.timeout_s)
    try:
        brief = brief_call(send, cfg, now, usages, deadline)
        record.update(
            brief=brief["text"], sources=brief["sources"], brief_stop=brief["stop"], searches=brief["searches"]
        )
        markets = forecast_call(send, cfg, brief["text"], now, usages, deadline)
        record["markets"] = markets
        if markets:
            record["status"] = "ok"
        else:
            record["error"] = "the forecast named no market of the four"
    except AnalystError as exc:
        record["error"] = scrub(str(exc), secret)
    except Exception as exc:  # the reading is on the side of the books: whatever broke is written, never raised
        record["error"] = scrub(f"{type(exc).__name__}: {exc}", secret)
        log.error("analyst reading failed: %s", record["error"])
    record["usage"] = usages
    record["cost_usd"] = round(sum(call_cost(u, cfg) for u in usages), 4)
    store.append_jsonl(LOG, record, ts=now)
    if ledger is not None:
        try:
            _append_ledger(ledger, record)
        except OSError as exc:  # the archive has it; the ledger only guards against losing the archive's line
            log.warning("analyst ledger not written: %s", exc)
    summary: dict[str, Any] = {
        "status": record["status"],
        "error": record["error"],
        "markets": {m: v["forecast"] for m, v in record["markets"].items()},
        "cost_usd": record["cost_usd"],
    }
    if restored:
        summary["restored"] = restored
    return summary


def write_status(
    store: StateStore, now: datetime, key_set: bool, readers: bool, outcome: Mapping[str, Any] | None
) -> None:
    """What the runner's tick knew about the analyst: when it looked, whether it had a key (yes or no: never the
    key itself), whether a book reads it, and what the step did. The page reads this, so that an export made by a
    job without the key (the weekly job, the weekend job) still says what the runner can do."""
    step = None if outcome is None else {k: outcome[k] for k in ("status", "why", "error") if outcome.get(k)}
    store.write_json(
        STATUS_FILE,
        {"checked_at": iso(ensure_utc(now)), "key_set": bool(key_set), "readers": bool(readers), "step": step},
    )


def read_status(store: StateStore) -> dict[str, Any] | None:
    """The runner's last word on the analyst, or None (never written, or a file cut short)."""
    try:
        status = store.read_json(STATUS_FILE)
    except ValueError:
        return None
    return status if isinstance(status, dict) else None


# ---------------------------------------------------------------------------------------------- what the books read
@dataclass
class AnalystForecast:
    """The reading a decision of ``day`` reads, as the books' forecast mapping."""

    day: date
    reading_day: date | None
    reading_ts: str | None
    model: str | None
    values: dict[str, float] = field(default_factory=dict)
    confidence: dict[str, str | None] = field(default_factory=dict)
    stale: bool = False
    note: str = ""

    @property
    def age_days(self) -> int | None:
        return None if self.reading_day is None else (self.day - self.reading_day).days

    def as_forecast(self) -> dict[str, float | None]:
        out: dict[str, float | None] = dict(self.values)
        for m, level in self.confidence.items():
            out[f"{m}:confidence"] = float(CONFIDENCE_LEVELS.index(level) + 1) if level in CONFIDENCE_LEVELS else None
        out[KEY_AGE] = None if self.age_days is None else float(self.age_days)
        return out


def forecast_on(rows: Iterable[Mapping[str, Any]], day: date) -> AnalystForecast | None:
    """The newest good reading taken on ``day`` or before it (never after: the decision of day D does not read a
    reading of D+1); None when there is none at all - no decision, the book holds what it has (cash). A reading
    more than ``STALE_DAYS`` old is no view: every market 0, the books close."""
    good = [r for r in rows if r.get("status") == "ok" and str(r.get("day", "")) <= day.isoformat()]
    if not good:
        return None
    last = max(good, key=lambda r: (str(r.get("day")), str(r.get("ts"))))
    reading_day = date.fromisoformat(str(last["day"]))
    markets = last.get("markets") or {}
    out = AnalystForecast(
        day=day, reading_day=reading_day, reading_ts=str(last.get("ts") or ""), model=str(last.get("model") or "")
    )
    age = (day - reading_day).days
    if age > STALE_DAYS:
        out.values = dict.fromkeys(MARKETS, 0.0)
        out.stale = True
        out.note = f"the analyst's last reading is {age} days old (more than {STALE_DAYS}): no view, the books close"
        return out
    for m in MARKETS:
        entry = markets.get(m)
        if isinstance(entry, dict) and isinstance(entry.get("forecast"), int | float):
            out.values[m] = float(entry["forecast"])
            out.confidence[m] = entry.get("confidence")
    shown = ", ".join(f"{m} {_signed(v)}" for m, v in out.values.items()) or "no market"
    out.note = f"the analyst's reading of {reading_day.isoformat()} ({_age_words(age)}): {shown}"
    return out


def forecast_for(store: StateStore, day: date) -> AnalystForecast | None:
    return forecast_on(records(store), day)


def _signed(x: float | None) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x:+.0f}" if float(x).is_integer() else f"{x:+.1f}"


def _age_words(age: int) -> str:
    return "today" if age == 0 else "yesterday" if age == 1 else f"{age} days old"


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")  # fmt: skip


def _long_day(d: date) -> str:
    """'16 October': a date in a sentence, whatever the locale of the runner."""
    return f"{d.day} {MONTHS[d.month - 1]}"


def lead_text(forecast: Mapping[str, float | None], sleeves: Mapping[str, float]) -> str:
    """The first words of a Claude book's rationale: which reading, and what it says of the book's market."""
    age = forecast.get(KEY_AGE)
    if age is None:
        return "No reading of the Claude analyst: no forecast. "
    if int(age) > STALE_DAYS:
        return f"The Claude analyst's last reading is {int(age)} days old: no view, the book closes. "
    when = _age_words(int(age))
    parts = []
    for market in sleeves:
        value = forecast.get(market)
        words = MARKET_WORDS.get(market, market)
        if value is None:
            parts.append(f"no forecast for {words} in it")
            continue
        level = forecast.get(f"{market}:confidence")
        confidence = (
            f", confidence {CONFIDENCE_LEVELS[int(level) - 1]}" if level is not None and 1 <= level <= 3 else ""
        )
        parts.append(f"{words} {_signed(value)} out of 20{confidence}")
    return f"The Claude analyst's reading of the news ({when}): {'; '.join(parts)}. "


def _state(cfg: AnalystConfig, key_set: bool, view: AnalystForecast | None, spent: float) -> tuple[str, str]:
    """The analyst's state: a word the page names it by (off, waiting, on, stale, budget, disabled), and a sentence
    that reads on its own - whether readings are taken, and what the books read meanwhile."""
    if view is None or view.reading_day is None:
        then = "the books stay in cash"
    elif view.stale:
        then = view.note
    else:
        then = f"the books read the reading of {_long_day(view.reading_day)} ({_age_words(view.age_days or 0)})"
    if not cfg.enabled:
        return "disabled", f"Turned off in config/analyst.yaml: no new reading; {then}."
    if not key_set:
        return "off", f"The runner has no ANTHROPIC_API_KEY secret, so no reading is taken; {then}."
    if spent >= cfg.monthly_budget_usd:
        return (
            "budget",
            f"This month's budget is spent ({spent:.2f} of {cfg.monthly_budget_usd:.2f} $): no reading until next "
            f"month; {then}.",
        )
    if view is None:
        return (
            "waiting",
            f"The first reading comes at the first tick after {cfg.start_utc.strftime('%H:%M')} UTC on a US business "
            "day; until then the books stay in cash.",
        )
    return ("stale" if view.stale else "on"), f"{then}."


def block(store: StateStore, cfg: AnalystConfig, day: date, books: list[dict[str, Any]], key_present: bool) -> dict:
    """What the page shows of the analyst: whether it runs (the runner's own word on the key, else the exporting
    process's), what the books read today, the newest reading (each market's forecast with its reasons and risk, the
    sources, the brief), the last attempt when it failed, the month's spend against the budget, the books and the
    rule."""
    rows = records(store)
    good = [r for r in rows if r.get("status") == "ok"]
    reading = good[-1] if good else None
    last = rows[-1] if rows else None
    now = datetime.combine(day, time(12, 0), tzinfo=UTC)
    status = read_status(store)
    key_set = bool(status.get("key_set")) if status is not None else key_present
    view = forecast_on(rows, day)
    spent = spent_in_month(rows, now)
    state, state_text = _state(cfg, key_set, view, spent)
    sources = [
        {"url": url, "title": str(s.get("title") or "")[:MAX_TEXT]}
        for s in (reading.get("sources") or [] if reading else [])
        if isinstance(s, dict) and (url := _web_url(s.get("url")))
    ]
    return {
        "configured": key_set or bool(rows),
        "key_set": key_set,
        "checked_at": status.get("checked_at") if status else None,
        "state": state,
        "state_text": state_text[:1].upper() + state_text[1:],
        "enabled": cfg.enabled,
        "model": cfg.model,
        "prompt_version": prompt_version(cfg),
        "budget_usd": cfg.monthly_budget_usd,
        "spent_month_usd": spent,
        "readings": len(good),
        "first_day": good[0]["day"] if good else None,
        "today": None
        if view is None
        else {
            "day": day.isoformat(),
            "reading_day": None if view.reading_day is None else view.reading_day.isoformat(),
            "age_days": view.age_days,
            "stale": view.stale,
            "values": view.values,
        },
        "reading": None
        if reading is None
        else {
            "day": reading.get("day"),
            "ts": reading.get("ts"),
            "model": reading.get("model"),
            "prompt_version": reading.get("prompt_version"),
            "markets": reading.get("markets") or {},
            "sources": sources[:12],
            "n_sources": len(sources),
            "searches": reading.get("searches"),
            "brief": str(reading.get("brief") or "")[:6000],
            "cost_usd": reading.get("cost_usd"),
        },
        "last_attempt": None
        if last is None or last is reading
        else {
            "day": last.get("day"),
            "ts": last.get("ts"),
            "status": last.get("status"),
            "error": str(last.get("error") or "")[:MAX_TEXT] or None,
        },
        "books": books,
        "rule": {
            "start_utc": cfg.start_utc.strftime("%H:%M"),
            "max_searches": cfg.max_searches,
            "stale_days": STALE_DAYS,
            "max_attempts": MAX_ATTEMPTS,
            "text": (
                f"Once a day on a US business day, after {cfg.start_utc.strftime('%H:%M')} UTC: {cfg.model} searches "
                "the news since the previous business day's morning, at least 48 hours back (at most "
                f"{cfg.max_searches} searches), and writes a brief of the facts with their sources; a second call "
                "reads the brief alone and gives oil, gold, the S&P 500 and long Treasuries a forecast from \u221220 "
                "to +20 for the next five sessions. A book reads the newest reading of its decision day or the "
                f"{STALE_DAYS} days before; older, it closes. No backtest can exist: a model trained on the past knows "
                "the prices a replay would ask it to forecast. Paper only; the prior is that it has no edge; never "
                "promoted."
            ),
        },
        "disclaimer": "A language model's reading of the news for a paper-trading study: not advice, not a verdict.",
    }
