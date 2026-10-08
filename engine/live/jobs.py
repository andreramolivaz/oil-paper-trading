"""The jobs the GitHub Actions workflows call (brief §5, §14, §15).

Every job is idempotent and degrades gracefully:

* `update` (every 30 minutes during ICE hours) refreshes prices, marks the account, evaluates stops and
  breakers on the intraday bars, and applies the health gate (stale data -> no new risk, leverage back to 1x).
* `eod` (after the ICE settlement) refreshes every source, archives the curve snapshot, takes the day's
  decision through the shared session, produces the forecasts and writes the derived state files. It runs at
  most once per London trading date.
* `weekly` refreshes the slow sources, refits the regime model, recomputes the S20 weights, re-runs the
  validation report (which is what moves a strategy's lifecycle) and compacts the state.
* `reset` archives the current epoch and restarts from 10 000 $.
* `alerts` opens or closes the GitHub issues the monitoring module detects.

* `tick` is what the scheduler actually runs: it works out by itself what is due (which sources to refresh,
  whether a settled London date still has no end-of-day, whether a book owes its daily decision) and does it.
  GitHub fires schedules hours late; a job that decides what to do from the clock it is started at, instead of
  from the clock it was meant to start at, cannot be made wrong by that.

Exit codes used by the CLI: 0 ok or nothing-to-do, 1 error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date
from typing import Any

import pandas as pd

from engine.core.calendar import is_business_day
from engine.core.events import Bar
from engine.core.timeutil import (
    ensure_utc,
    is_after_settlement,
    is_ice_session_open,
    iso,
    london_date,
    now_utc,
    parse_iso,
    to_london,
)
from engine.live.runner import LiveRunner

log = logging.getLogger(__name__)

OK, ERROR, SKIP = 0, 1, 3


@dataclass
class JobOutcome:
    status: str  # ok | skipped | failed
    message: str
    detail: dict[str, Any]

    @property
    def exit_code(self) -> int:
        # A guard that says "nothing to do" is not a failure. It used to exit 3, GitHub painted the run red,
        # and a red cross that means "too early" teaches everyone to ignore red crosses.
        return {"ok": OK, "skipped": OK, "failed": ERROR}[self.status]


# ---------------------------------------------------------------------------------------------- update
def update(runner: LiveRunner, job_id: str, force: bool = False) -> JobOutcome:
    """Intraday tick: refresh prices, feed new bars, mark the account, enforce the health gate."""
    now = now_utc()
    if not force and not is_ice_session_open(now):
        return JobOutcome("skipped", f"mercato chiuso (Londra {to_london(now):%Y-%m-%d %H:%M})", {})
    rec = runner.start_run("update", job_id, london_date(now))
    if rec is None:
        return JobOutcome("skipped", "run già completata con questo job id", {"job_id": job_id})
    try:
        fetched = _fetch(runner, ["prices"])
        md = runner.market_data(refresh=True)
        session = runner.session(md=md, refresh=True)
        gate_msg = runner.apply_health_gate(session)
        bars = _intraday_bars(md, session, limit=8)
        fills = 0
        vol = runner.market_vol(session)
        for bar in bars:
            fills += session.on_bar(bar, vol_annual=vol)
        snap = session.master.mark(
            prices=_last_prices(md, session),
            ts=now,
            source=str(md.meta.get("prices_source", "n/d")),
            asof=_price_asof(md),
        )
        runner.store.append_jsonl("equity", snap.to_dict(), ts=now)
        session.save()
        detail = {
            "bars": len(bars),
            "fills": fills,
            "equity": snap.equity,
            "leverage": snap.leverage,
            "status": str(snap.status),
            "health": gate_msg,
            "sources": fetched,
        }
        runner.finish_run(rec, "ok", gate_msg, detail)
        return JobOutcome("ok", gate_msg, detail)
    except Exception as exc:
        log.exception("update failed")
        runner.finish_run(rec, "failed", str(exc))
        return JobOutcome("failed", str(exc), {})


# ---------------------------------------------------------------------------------------------- eod
def eod(runner: LiveRunner, job_id: str, day: date | None = None, force: bool = False) -> JobOutcome:
    """After the ICE settlement: the day's decision, the forecasts and the derived state files."""
    now = now_utc()
    # Default to the latest London date whose settlement has ALREADY happened. The old default was "today", so a
    # schedule fired after midnight London (GitHub runs them hours late) asked for a day that had not settled
    # yet, was told "too early" and skipped - two days out of three.
    target = day or latest_settled_date(now)
    if not force:
        if not is_after_settlement(now) and target >= london_date(now):
            return JobOutcome(
                "skipped",
                f"prima del settlement ICE (Londra {to_london(now):%H:%M}, serve dopo le 19:35)",
                {},
            )
        if runner.ran_ok_for_date("eod", target):
            return JobOutcome("skipped", f"fine giornata già elaborata per {target}", {})
    rec = runner.start_run("eod", job_id, target)
    if rec is None:
        return JobOutcome("skipped", "run già completata con questo job id", {"job_id": job_id})
    try:
        # only what the first system reads: the desk keeps its own tables fresh from the tick, and asking Yahoo
        # for them twice in the same quarter of an hour is how an address gets rate limited
        fetched = _fetch(runner, LEGACY_GROUPS)
        md = runner.market_data(refresh=True)
        session = runner.session(md=md, refresh=True)
        runner.apply_health_gate(session)
        res = session.run_day(target)
        forecasts = _forecasts(runner, session, target)
        _risk(runner, session, monte_carlo=False, stress=False)
        runner.write_regime_file(session)
        runner.write_strategies_file(session)
        session.save()
        detail = {
            "day": target.isoformat(),
            "signals": res.signals,
            "orders": res.orders,
            "fills": res.fills,
            "regime": res.regime,
            "rolled": None if res.rolled is None else f"{res.rolled[0]}->{res.rolled[1]}",
            "skipped": res.skipped,
            "forecasts": forecasts,
            "sources": fetched,
        }
        msg = (
            f"{res.signals} segnali, {res.orders} ordini, regime «{res.regime}»"
            if res.skipped is None
            else f"nessuna decisione: {res.skipped}"
        )
        runner.finish_run(rec, "ok", msg, detail)
        return JobOutcome("ok", msg, detail)
    except Exception as exc:
        log.exception("eod failed")
        runner.finish_run(rec, "failed", str(exc))
        return JobOutcome("failed", str(exc), {})


# ---------------------------------------------------------------------------------------------- tick
LEGACY_GROUPS = ["prices", "volatility_macro", "fundamentals", "positioning", "news"]  # what the first system reads
DAILY_GROUPS = [*LEGACY_GROUPS, "desk"]  # everything daily: the weekly job, which also feeds the desk backtest
TICK_GROUPS = ["desk_intraday"]
# The daily tables the desk reads (engine/desk/data.py), by the vehicle whose decision needs them fresh. The
# first of each tuple is the probe: its last successful download says how old that vehicle's tables are. A
# refresh asks for these tables and nothing else. The whole "prices" group is about sixty requests to Yahoo,
# nearly all for curves the desk never opens, and Yahoo answers HTTP 429 for a long while to an address that
# asks too much - which on a tick costs the bars the brokers fill and mark against.
DESK_VEHICLE_ENTRIES: dict[str, tuple[str, ...]] = {
    "BNO": ("bno_daily", "brent_front", "bz_contracts"),
    "MCL": ("cl_contracts", "wti_front", "uso_daily"),
}
DESK_CONTEXT_ENTRIES = ("hormuz", "bab_el_mandeb", "wti_curve_hist")  # slow tables: one attempt a day
DAILY_MAX_AGE_HOURS = 24.0
CONTEXT_MAX_AGE_HOURS = 24.0
RED_RETRY_HOURS = 1.0  # a table whose last download failed, with no decision waiting on it
US_CLOSE_REFRESH_NY = (16, 15)  # the first tick after this downloads the official closes of the day, once
LEGACY_EOD_ATTEMPTS = 3


def _failed_runs(runner: LiveRunner, job: str, day: date) -> int:
    target = day.isoformat()
    return sum(
        1
        for row in runner.store.iter_jsonl("runs")
        if row.get("job") == job and row.get("london_date") == target and row.get("status") == "failed"
    )


def latest_settled_date(now: Any) -> date:
    """The most recent London weekday whose ICE settlement (19:30) is already behind ``now``."""
    local = to_london(now)
    day = local.date()
    if not is_after_settlement(now):
        day -= pd.Timedelta(days=1).to_pytimedelta()
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1).to_pytimedelta()
    return day


def _source_checked_at(runner: LiveRunner, source: str) -> Any:
    for entry in runner.health().get("sources", []):
        if str(entry.get("source")) == source and str(entry.get("status")) != "red":
            return parse_iso(entry.get("last_success_at") or entry.get("checked_at"))
    return None


def _source_attempted_at(runner: LiveRunner, source: str) -> Any:
    """When ``source`` was last ASKED for, whatever the answer was (None: never)."""
    for entry in runner.health().get("sources", []):
        if str(entry.get("source")) == source:
            return parse_iso(entry.get("checked_at") or entry.get("last_success_at"))
    return None


def _desk_daily_entries(runner: LiveRunner, desk: Any, now: Any) -> tuple[set[str], str]:
    """The daily tables to download on this tick and why (an empty set: none).

    Per vehicle: when its tables were never downloaded or the last attempt failed; when a book owes a decision
    they cannot serve yet (today's row is read at the decision time, not before); once after the US close, for
    the official closes the terminal shows overnight and an expired option settles on; and when they are a day
    old, as a safety net. The slow context tables are attempted once a day.
    """
    from engine.desk.live import NEW_YORK, decision_day
    from engine.desk.vehicles import get_vehicle

    now = ensure_utc(now)
    local = now.astimezone(NEW_YORK)
    close = local.replace(hour=US_CLOSE_REFRESH_NY[0], minute=US_CLOSE_REFRESH_NY[1], second=0, microsecond=0)
    after_close = is_business_day(local.date(), "US") and local >= close
    trading = set(desk.vehicles())
    entries: set[str] = set()
    reasons: list[str] = []

    def hours_since(when: Any) -> float:
        return float((now - ensure_utc(when)).total_seconds()) / 3600.0

    for vehicle_id, names in DESK_VEHICLE_ENTRIES.items():
        owed, day, decision_ts = False, None, None
        if vehicle_id in trading:
            vehicle = get_vehicle(vehicle_id)
            day = decision_day(vehicle, now)
            owed = any(desk.state.last_decision_day.get(b.id) != day.isoformat() for b in desk.books_on(vehicle_id))
            hour, minute = vehicle.decision_time_ny
            decision_ts = pd.Timestamp(
                year=day.year, month=day.month, day=day.day, hour=hour, minute=minute, tz=NEW_YORK
            )
        checked = _source_checked_at(runner, names[0])  # the last download that worked (None: never, or red now)
        attempted = _source_attempted_at(runner, names[0])
        reason = ""
        if checked is None and attempted is None:
            reason = "da scaricare"
        elif checked is None:
            # the last attempt failed: a decision that is waiting asks again at every tick, the rest once an hour
            if owed or hours_since(attempted) >= RED_RETRY_HOURS:
                reason = "ultimo tentativo non riuscito"
        elif hours_since(checked) > DAILY_MAX_AGE_HOURS:
            reason = f"vecchi di {hours_since(checked):.0f} ore"
        elif after_close and ensure_utc(checked) < close.astimezone(UTC):
            reason = "chiusure ufficiali del giorno"
        elif owed and day is not None and decision_ts is not None and pd.Timestamp(checked) < decision_ts:
            reason = f"decisione del {day.isoformat()} da prendere"
        if reason:
            entries |= set(names)
            reasons.append(f"{vehicle_id}: {reason}")
    attempted = _source_attempted_at(runner, DESK_CONTEXT_ENTRIES[0])
    if attempted is None or hours_since(attempted) > CONTEXT_MAX_AGE_HOURS:
        entries |= set(DESK_CONTEXT_ENTRIES)
        reasons.append("contesto: una volta al giorno")
    return entries, "; ".join(reasons)


def tick(runner: LiveRunner, job_id: str, legacy: bool = True) -> JobOutcome:
    """One scheduler tick: refresh what is due, let the desk fill, roll, decide and mark, then run the first
    system's end of day if a settled date is missing. Each part is independent: one failing does not stop the
    others."""
    from engine.desk.data import build_desk_data
    from engine.desk.live import build_desk, live_tick

    now = now_utc()
    rec = runner.start_run("tick", job_id, london_date(now))
    if rec is None:
        return JobOutcome("skipped", "run già completata con questo job id", {"job_id": job_id})
    detail: dict[str, Any] = {}
    try:
        desk = build_desk(runner.settings.state_dir, runner.risk)
        groups = list(TICK_GROUPS)
        entries, why = _desk_daily_entries(runner, desk, now)
        if entries:
            detail["daily_refresh"] = why
        if _options_due(runner, now):
            groups.append("options")
        if _weekly_alt_due(runner, now):
            groups.append("weekly_alt")
        fetched = _fetch(runner, groups, entries)
        # The downloads can take minutes: everything below is stamped with the time it actually happens, so an
        # order is never dated before the data it was decided on (it fills on the first bar AFTER its stamp).
        now = now_utc()
        detail["sources"] = _fetch_summary(fetched, groups, entries)
        detail["raw_snapshots_deleted"] = _compact_raw(runner, fetched)

        data = build_desk_data(runner.raw, runner.settings)
        report = live_tick(desk, data, now, blocked=_blocked_vehicles(runner))
        detail["desk"] = report.to_dict()
        try:
            from engine.desk.options import options_tick

            detail["options"] = options_tick(runner.settings.state_dir, runner.risk, data, runner.raw, now)
        except Exception as exc:  # the options book is independent of the linear books
            log.warning("options book skipped: %s", exc)
            detail["options"] = {"error": str(exc)[:200]}

        # The terminal shows the backtest beside the live books. The weekly job refreshes it; a desk that has
        # never had one computes it once here (about twenty seconds), so the page is whole from its first day.
        if {"BNO", "MCL"} <= set(data.series) and not _desk_backtest_on_file(runner):
            try:
                from engine.desk.report import run_desk_backtest

                run_desk_backtest(runner.raw, runner.settings, runner.risk, runner.store)
                detail["desk_backtest"] = "calcolato: mancava"
            except Exception as exc:  # a replay on the side must never cost the tick
                log.warning("desk backtest skipped: %s", exc)
                detail["desk_backtest"] = f"non riuscito: {str(exc)[:160]}"

        # The first system's end of day comes LAST: it takes minutes (features, regime, forecasts) and the books
        # must not wait for it. It is tried at most LEGACY_EOD_ATTEMPTS times per date: a date that keeps failing
        # must not cost five minutes of every tick for the rest of the day.
        if legacy:
            target = latest_settled_date(now)
            if not runner.ran_ok_for_date("eod", target):
                failed = _failed_runs(runner, "eod", target)
                if failed >= LEGACY_EOD_ATTEMPTS:
                    detail["legacy_eod"] = {
                        "day": target.isoformat(),
                        "status": "skipped",
                        "message": f"{failed} tentativi falliti",
                    }
                else:
                    outcome = eod(runner, f"{job_id}-eod", day=target, force=True)
                    detail["legacy_eod"] = {
                        "day": target.isoformat(),
                        "status": outcome.status,
                        "message": outcome.message,
                    }
        msg = report.message()
        runner.finish_run(rec, "ok", msg, detail)
        return JobOutcome("ok", msg, detail)
    except Exception as exc:
        log.exception("tick failed")
        runner.finish_run(rec, "failed", str(exc), detail)
        return JobOutcome("failed", str(exc), detail)


def _desk_backtest_on_file(runner: LiveRunner) -> bool:
    from engine.desk.live import DESK_DIR
    from engine.desk.report import BACKTEST_FILE

    return (runner.settings.state_dir / DESK_DIR / BACKTEST_FILE).exists()


# What each vehicle's daily decision cannot do without: every table of at least one of the listed sets must be
# healthy. A red table means today's download failed, so "today's row" may be this morning's running bar or
# missing altogether: bars are still fed, stops still work, marks still happen - only NEW decisions wait.
DESK_REQUIRES: dict[str, tuple[tuple[str, ...], ...]] = {
    "BNO": (("bno_daily",),),
    "MCL": (("cl_contracts",), ("wti_front",)),
}


def _blocked_vehicles(runner: LiveRunner) -> dict[str, str]:
    status = {str(s.get("source")): str(s.get("status")) for s in runner.health().get("sources", [])}
    blocked: dict[str, str] = {}
    for vehicle, alternatives in DESK_REQUIRES.items():
        usable = any(all(status.get(name, "red") != "red" for name in names) for names in alternatives)
        if not usable:
            names = " o ".join("+".join(a) for a in alternatives)
            blocked[vehicle] = f"fonte {names} non disponibile: nessuna nuova decisione finché non torna"
    return blocked


def _options_due(runner: LiveRunner, now: Any) -> bool:
    """Option chains are refreshed hourly while the US market is open, and on the tick that owes the options
    book its daily decision (the book only trades on quotes less than 45 minutes old)."""
    from engine.desk.live import NEW_YORK
    from engine.desk.options import decision_due

    local = ensure_utc(now).astimezone(NEW_YORK)
    if local.weekday() >= 5 or not (9 <= local.hour < 17):
        return False
    if decision_due(runner.settings.state_dir, ensure_utc(now), runner.settings.config_dir):
        return True
    checked = _source_checked_at(runner, "uso_options")
    return checked is None or (ensure_utc(now) - ensure_utc(checked)).total_seconds() > 3300


WEEKLY_ALT_PROBE = "insider_form4"


def _weekly_alt_due(runner: LiveRunner, now: Any) -> bool:
    """The weekly job owns the slow alternative data. A tick steps in only when the table has never been
    downloaded or has not been for more than a week, and then at most once a day (the free key allows 25 calls
    a day and the table needs 12)."""
    for entry in runner.health().get("sources", []):
        if str(entry.get("source")) != WEEKLY_ALT_PROBE:
            continue
        if str(entry.get("status")) == "green":
            return False
        success = parse_iso(entry.get("last_success_at"))
        if success is not None and (ensure_utc(now) - ensure_utc(success)).total_seconds() < 8 * 86_400:
            return False  # downloaded this week, whatever colour its quality checks gave it
        checked = parse_iso(entry.get("checked_at"))
        return checked is None or (ensure_utc(now) - ensure_utc(checked)).total_seconds() > 20 * 3600
    return True


# Tables that are downloaded whole at every tick: yesterday's snapshot is a subset of today's. One stamped copy
# is kept (plus `latest`); the option chains also keep the first snapshot of each ISO week, which is the only
# history of real option quotes this project will ever have.
EPHEMERAL_RAW = ("bno_intraday", "cl_intraday", "bz_intraday", "brent_intraday_1h")
WEEKLY_RAW = ("bno_options", "uso_options")


def _compact_raw(runner: LiveRunner, refreshed: dict[str, Any]) -> int:
    """Trim the raw archive of what this tick downloaded, so the data branch does not grow by megabytes a day.
    Daily tables keep their last two snapshots and the first of each ISO week (the weekly job's own policy)."""
    deleted = 0
    try:
        raw = runner.raw
        names = {str(s.get("source")) for s in refreshed.get("sources", []) if isinstance(s, dict)}
        for source in sorted(names | set(EPHEMERAL_RAW) | set(WEEKLY_RAW)):
            for key in raw.keys(source):
                if source in EPHEMERAL_RAW:
                    deleted += len(raw.compact(source, key, keep_last_n=1, keep_weekly=False))
                elif source in WEEKLY_RAW:
                    deleted += len(raw.compact(source, key, keep_last_n=1, keep_weekly=True))
                else:
                    deleted += len(raw.compact(source, key, keep_last_n=2, keep_weekly=True))
    except Exception as exc:
        log.warning("raw compaction skipped: %s", exc)
    return deleted


def _reset_books(runner: LiveRunner, book: str) -> list[str]:
    """Reset one desk book or all of them; returns the ids that were reset."""
    from engine.desk.live import build_desk

    desk = build_desk(runner.settings.state_dir, runner.risk)
    ts = now_utc()
    done: list[str] = []
    for book_id, b in desk.books.items():
        if book not in {"all", book_id}:
            continue
        b.broker.reset(ts)
        desk.state.last_decision_day.pop(book_id, None)
        desk.state.last_financing_day.pop(book_id, None)
        done.append(book_id)
    try:
        from engine.desk.options import reset_options_book

        if book in {"all", "opzioni"} and reset_options_book(runner.settings.state_dir, runner.risk, ts):
            done.append("opzioni")
    except Exception as exc:
        log.warning("options book reset skipped: %s", exc)
    desk.save()
    return done


# ---------------------------------------------------------------------------------------------- weekly
def weekly(runner: LiveRunner, job_id: str) -> JobOutcome:
    """Slow sources, regime refit, S20 weights, validation report (lifecycles) and state compaction."""
    rec = runner.start_run("weekly", job_id, london_date(now_utc()))
    if rec is None:
        return JobOutcome("skipped", "run già completata con questo job id", {"job_id": job_id})
    detail: dict[str, Any] = {}
    try:
        detail["sources"] = _fetch(runner, [*DAILY_GROUPS, "weekly_alt"])
        md = runner.market_data(refresh=True)
        session = runner.session(md=md, refresh=True)
        detail["weights"] = runner.update_weights(session)
        detail["regime_refit"] = _refit_regime(runner, session)
        detail["validation"] = _validation(runner, md, session)
        detail["risk"] = _risk(runner, session, monte_carlo=True, stress=True)
        detail["compaction"] = _compact(runner)
        runner.write_strategies_file(session)
        session.save()
        msg = "aggiornamento settimanale completato"
        runner.finish_run(rec, "ok", msg, detail)
        return JobOutcome("ok", msg, detail)
    except Exception as exc:
        log.exception("weekly failed")
        runner.finish_run(rec, "failed", str(exc), detail)
        return JobOutcome("failed", str(exc), detail)


# ---------------------------------------------------------------------------------------------- reset
def reset(runner: LiveRunner, job_id: str, actor: str = "unknown", book: str = "all") -> JobOutcome:
    """Archive the current epoch, flatten everything and restart from the initial capital.

    ``book`` is ``all`` (every desk book and the legacy master) or the id of one desk book.
    """
    rec = runner.start_run("reset", job_id, london_date(now_utc()))
    if rec is None:
        return JobOutcome("skipped", "reset già eseguito con questo job id", {"job_id": job_id})
    try:
        books_reset = _reset_books(runner, book)
        if book != "all":
            if not books_reset:
                runner.finish_run(rec, "failed", f"libro sconosciuto: {book}")
                return JobOutcome("failed", f"libro sconosciuto: {book}", {})
            msg = f"libro {book} riportato a {runner.risk.initial_capital:,.0f} $ da {actor}".replace(",", ".")
            runner.finish_run(rec, "ok", msg, {"actor": actor, "books": books_reset})
            return JobOutcome("ok", msg, {"actor": actor, "books": books_reset})
        md = runner.market_data(refresh=False) if runner.store.exists("health.json") else None
        session = runner.session(md=md) if md is not None else None
        ts = now_utc()
        before = runner.store.read_json("account.json") or {}
        epoch = session.master.reset(ts) if session is not None else None
        if session is not None:
            for broker in session.shadows.values():
                broker.reset(ts)
            session.state.processed_days = []
            session.state.last_day = None
            session.save()
        detail = {
            "actor": actor,
            "previous_epoch": before.get("epoch"),
            "previous_equity": before.get("last_equity"),
            "new_epoch": None if epoch is None else epoch.epoch + 1,
            "books": books_reset,
        }
        msg = f"conto riportato a {runner.risk.initial_capital:,.0f} $ da {actor}".replace(",", ".")
        runner.finish_run(rec, "ok", msg, detail)
        return JobOutcome("ok", msg, detail)
    except Exception as exc:
        log.exception("reset failed")
        runner.finish_run(rec, "failed", str(exc))
        return JobOutcome("failed", str(exc), {})


# ---------------------------------------------------------------------------------------------- alerts
def alerts(runner: LiveRunner, repo: str) -> JobOutcome:
    from engine.monitoring.alerts import Alerter, GhClient, detect_alerts

    found = detect_alerts(runner.store, now_utc())
    client = GhClient(repo) if repo else None
    actions = Alerter(client, runner.store).raise_alerts(found)
    msg = f"{len(found)} avvisi attivi" if found else "nessun avviso"
    return JobOutcome("ok", msg, {"alerts": [a.key for a in found], "actions": actions})


# ---------------------------------------------------------------------------------------------- helpers
def _fetch(runner: LiveRunner, groups: list[str] | None, entries: set[str] | None = None) -> dict[str, Any]:
    """Refresh the sources (whole groups, plus single tables by name); a failure degrades the health, it never
    fails the job."""
    try:
        from engine.data.fetch import Fetcher

        fetcher = Fetcher(runner.settings, runner.raw, runner.store)
        return dict(fetcher.run_all(groups, entries or None) or {})
    except Exception as exc:
        log.error("fetch failed (%s): continuing with the data already on disk", exc)
        return {"error": str(exc)}


def _fetch_summary(fetched: dict[str, Any], groups: list[str], entries: set[str] | None = None) -> dict[str, Any]:
    """What a tick records about its downloads: counts and the names that failed, not the whole report (the
    run log gets one line per tick, 48 a day; the full status of every source is in ``health.json``)."""
    asked = [*groups, *sorted(entries or ())]
    if "error" in fetched:
        return {"groups": asked, "error": str(fetched["error"])[:300]}
    failed = [str(s.get("source")) for s in fetched.get("sources", []) if str(s.get("status")) == "red"]
    return {
        "groups": asked,
        "ok": fetched.get("ok"),
        "failed": fetched.get("failed"),
        "overall": fetched.get("overall"),
        "red": failed,
    }


def _price_asof(md: Any) -> Any:
    pub = md.published_at.get("prices") if md is not None else None
    if pub is None or len(pub) == 0:
        return None
    return ensure_utc(pd.Timestamp(pub.iloc[-1]).to_pydatetime())


def _last_prices(md: Any, session: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if md is None or md.prices.empty:
        return out
    close = md.prices["brent_front_close"].dropna()
    if close.empty:
        return out
    day = pd.Timestamp(close.index[-1]).date()
    out[session.front_code(day)] = float(close.iloc[-1])
    return out


def _intraday_bars(md: Any, session: Any, limit: int = 8) -> list[Bar]:
    """The newest intraday bars of the front contract not yet processed by the master broker."""
    if md is None or md.intraday is None or md.intraday.empty:
        return []
    frame = md.intraday.sort_index()
    cols = {str(c).lower(): str(c) for c in frame.columns}
    close_col = cols.get("close")
    if close_col is None:
        return []
    bars: list[Bar] = []
    for ts, row in frame.tail(limit).iterrows():
        when = ensure_utc(pd.Timestamp(ts).to_pydatetime())
        day = to_london(when).date()
        symbol = session.front_code(day)
        last = session.master.state.last_bar_ts.get(f"{symbol}|1h")
        if last is not None and when <= ensure_utc(last):
            continue
        close = float(row[close_col])
        if not pd.notna(close) or close <= 0:
            continue
        o = float(row[cols["open"]]) if "open" in cols and pd.notna(row[cols["open"]]) else close
        hi = float(row[cols["high"]]) if "high" in cols and pd.notna(row[cols["high"]]) else max(o, close)
        lo = float(row[cols["low"]]) if "low" in cols and pd.notna(row[cols["low"]]) else min(o, close)
        bars.append(
            Bar(
                symbol=symbol,
                ts=when,
                open=o,
                high=max(hi, o, close),
                low=min(lo, o, close),
                close=close,
                interval="1h",
                source=str(md.meta.get("intraday_source", "yahoo")),
                asof=when,
            )
        )
    return bars


def _forecasts(runner: LiveRunner, session: Any, day: date) -> dict[str, Any]:
    """Run the forecaster ensemble, archive the forecasts and resolve the ones whose horizon elapsed."""
    try:
        from engine.forecast.archive import ForecastArchive
        from engine.forecast.benchmarks import FuturesCurveForecaster, RandomWalkForecaster
        from engine.forecast.ensemble import StackingEnsemble
        from engine.forecast.garch import GarchForecaster
        from engine.forecast.quantile_gbm import QuantileGbmForecaster
    except Exception as exc:
        log.warning("forecast modules unavailable (%s): skipped", exc)
        return {"skipped": str(exc)}
    try:
        from engine.core.timeutil import settlement_ts

        features = session.features_at(day)
        price_series = features["px_front"].dropna()
        if price_series.empty:
            price_series = features["px"].dropna()
        price = float(price_series.iloc[-1])
        curve = session.curve_row(day)
        asof = settlement_ts(day)  # frame.attrs['asof'] is an ISO string, the forecasters want a datetime
        models: list[Any] = [RandomWalkForecaster(), FuturesCurveForecaster()]
        for cls in (GarchForecaster, QuantileGbmForecaster):
            try:
                models.append(cls())
            except Exception as exc:  # pragma: no cover - optional model
                log.warning("forecaster %s unavailable: %s", cls.__name__, exc)
        ensemble = StackingEnsemble(models)
        state = runner.store.read_json("forecast_ensemble.json")
        if state:
            ensemble.from_state(state)
        out: dict[str, Any] = {}
        archive = ForecastArchive(runner.store)
        produced = []
        for model in [*models, ensemble]:
            try:
                fc = model.predict(features, asof, price, curve)
            except Exception as exc:
                log.info("forecaster %s skipped: %s", getattr(model, "name", model), exc)
                continue
            produced.extend(fc.values())
            out[getattr(model, "name", "model")] = {h: round(q.median, 2) for h, q in fc.items()}
        if produced:
            archive.record(produced, run_id=iso(now_utc()) or "")
        prices = session.md.prices["brent_front_close"].dropna()
        resolved = archive.resolve(prices)
        runner.store.write_json("forecast_ensemble.json", ensemble.to_state())
        runner.store.write_json(
            "forecasts_summary.json",
            {
                "generated_at": iso(now_utc()),
                "asof": iso(asof),
                "price": price,
                "models": out,
                "track_record": archive.track_record(),
            },
        )
        return {"models": list(out), "n_forecasts": len(produced), "resolved": resolved}
    except Exception as exc:
        log.warning("forecasts failed: %s", exc)
        return {"error": str(exc)}


def _risk(runner: LiveRunner, session: Any, monte_carlo: bool, stress: bool) -> dict[str, Any]:
    """Write state/risk.json. The end-of-day job keeps it cheap; the weekly one adds ruin and stress."""
    try:
        from engine.monitoring.risk_report import write_risk_file

        # Historical analogs want the LONGEST real series: the EIA Brent spot starts in 1987 and is what makes
        # the Gulf War test possible at all (brief §6), while the futures only start in 2007. No splicing: a
        # spliced series would carry a junction that never traded.
        prices = None
        if session is not None and not session.md.prices.empty:
            candidates = [session.md.prices.get(c) for c in ("brent_spot", "brent_front_close", "brent_cont")]
            usable = [c.dropna() for c in candidates if c is not None and not c.dropna().empty]
            prices = max(usable, key=len) if usable else None
        payload = write_risk_file(
            runner.store, runner.risk, prices=prices, run_monte_carlo=monte_carlo, run_stress=stress
        )
        return {
            "var": bool(payload.get("var")),
            "ruin": None if not payload.get("ruin") else payload["ruin"].get("prob_ruin"),
            "unavailable": payload.get("unavailable", []),
        }
    except Exception as exc:
        log.warning("risk report skipped: %s", exc)
        return {"skipped": str(exc)}


def _refit_regime(runner: LiveRunner, session: Any) -> dict[str, Any]:
    try:
        last = pd.Timestamp(session.md.prices.index[-1]).date()
        features = session.features_at(last)
        model = session.regime_model
        if hasattr(model, "fit"):
            model.fit(features)
        cache = runner.store.path("models/regime.pkl")
        if hasattr(model, "save_file"):
            cache.parent.mkdir(parents=True, exist_ok=True)
            model.save_file(cache)
        return {"ok": True, "rows": len(features)}
    except Exception as exc:
        log.warning("regime refit skipped: %s", exc)
        return {"ok": False, "error": str(exc)}


def _validation(runner: LiveRunner, md: Any, session: Any) -> dict[str, Any]:
    try:
        from engine.report.backtest_report import run_validation

        return dict(
            run_validation(
                md=md,
                strategies=session.strategies,
                risk=runner.risk,
                store=runner.store,
                out_dir=runner.store.path("reports"),
            )
            or {}
        )
    except Exception as exc:
        log.warning("validation skipped: %s", exc)
        return {"skipped": str(exc)}


def _compact(runner: LiveRunner) -> dict[str, Any]:
    """Keep the data branch small: trim rotated logs and old raw snapshots (brief §5)."""
    out: dict[str, Any] = {}
    for name, keep in (("equity", 24), ("equity_shadows", 24), ("signals", 24), ("trades", 60), ("runs", 12)):
        try:
            deleted = runner.store.compact_jsonl(name, keep)
            if deleted:
                out[name] = [p.name for p in deleted]
        except Exception as exc:
            out[name] = f"error: {exc}"
    # Raw snapshots: every run re-downloads whole series, so daily vintages are mostly duplicates. Keep the
    # last two plus the first snapshot of each ISO week, which preserves the revision history (the GPR index is
    # recomputed when the file grows, for instance) without growing the data branch by a megabyte a day.
    deleted_raw = 0
    try:
        raw = runner.raw
        for source in raw.sources():
            for key in raw.keys(source):
                deleted_raw += len(raw.compact(source, key, keep_last_n=2, keep_weekly=True))
        out["raw_snapshots_deleted"] = deleted_raw
    except Exception as exc:
        out["raw"] = f"error: {exc}"
    return out
