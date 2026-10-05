from datetime import UTC, date, datetime

from engine.core.eventcal import EventCalendar


def test_weekly_eia_and_holiday_shift():
    cal = EventCalendar()
    # Week of Labor Day 2026 (Mon 7 Sep): WPSR shifts to Thursday 10 Sep 10:30 ET = 14:30 UTC
    evs = [
        e
        for e in cal.events_between(datetime(2026, 9, 6, tzinfo=UTC), datetime(2026, 9, 12, tzinfo=UTC))
        if e.event_id == "eia_wpsr"
    ]
    assert len(evs) == 1 and evs[0].ts == datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
    # Normal week: Wednesday 14 Oct 2026 10:30 ET = 14:30 UTC (EDT)
    evs = [
        e
        for e in cal.events_between(datetime(2026, 10, 11, tzinfo=UTC), datetime(2026, 10, 17, tzinfo=UTC))
        if e.event_id == "eia_wpsr"
    ]
    assert evs[0].ts == datetime(2026, 10, 14, 14, 30, tzinfo=UTC)


def test_hours_to_next_binary_and_window():
    cal = EventCalendar()
    ts = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)  # Tuesday noon
    hrs, ev = cal.hours_to_next_binary(ts)
    assert ev is not None and ev.event_id == "eia_wpsr" and abs(hrs - 26.5) < 1e-9
    assert not cal.in_event_window(ts, hours_before=24)
    assert cal.in_event_window(datetime(2026, 10, 14, 12, 0, tzinfo=UTC), hours_before=24)
    # 3 hours after the release still inside the window
    assert cal.in_event_window(datetime(2026, 10, 14, 17, 0, tzinfo=UTC), hours_before=24, hours_after=6)


def test_expiry_and_oneoff_and_season():
    cal = EventCalendar()
    evs = cal.events_between(datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))
    ids = {e.event_id for e in evs}
    assert "ice_brent_expiry_202612" in ids  # Dec-26 expires 30 Oct
    assert "fomc_2026_10" in ids
    assert cal.is_hurricane_season(date(2026, 9, 1)) and not cal.is_hurricane_season(date(2026, 1, 15))
