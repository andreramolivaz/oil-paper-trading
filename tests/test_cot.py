"""Tests for engine.data.adapters.cot (CFTC Socrata, CFTC f_disagg.txt, ICE COTHist yearly CSVs).

Offline tests replay real snapshots captured on 2026-10-05 (tests/fixtures/cot/README.md) through fake clients;
Socrata paging ($limit/$offset) and the ICE year loop (404 -> skipped year) are exercised for real.
Network tests are opt-in (``pytest -m network``).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from engine.core.errors import DataUnavailable
from engine.data.adapters.cot import (
    CFTC_DISAGG_TXT_URL,
    COT_FRAME_COLUMNS,
    ICE_MARKETS,
    ICE_URL_TEMPLATE,
    SOCRATA_SELECT,
    SOCRATA_URL,
    CftcAdapter,
    CftcFileAdapter,
    IceCotAdapter,
    cftc_holidays,
    cot_published_at,
    cot_release_day,
    parse_ice_cot_csv,
)
from engine.data.market_data import COT_COLUMNS

FIX = Path(__file__).parent / "fixtures" / "cot"
SOCRATA_FIXTURE = "socrata_72hh-3qpy_067651_since_2026-06-09.json"
NOT_FOUND = DataUnavailable("GET x failed after 4 attempts: 404 Client Error: Not Found for url: x")
TODAY = date(2026, 10, 5)


def _socrata_rows() -> list[dict[str, Any]]:
    return list(json.loads((FIX / SOCRATA_FIXTURE).read_text(encoding="utf-8")))


class FakeJsonClient:
    """Replays a Socrata JSON page list honouring $limit/$offset."""

    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    def get_json(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        params = dict(params or {})
        self.calls.append({"url": url, **params})
        offset, limit = int(params["$offset"]), int(params["$limit"])
        return self.rows[offset : offset + limit]


class FakeBytesClient:
    """Serves bytes per URL; a stored exception is raised instead (simulates HttpClient failures)."""

    def __init__(self, replies: dict[str, bytes | Exception]):
        self.replies = replies
        self.urls: list[str] = []

    def get_bytes(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> bytes:
        self.urls.append(url)
        reply = self.replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _ice(replies: dict[int, bytes | Exception], today: date = TODAY) -> tuple[IceCotAdapter, FakeBytesClient]:
    client = FakeBytesClient({ICE_URL_TEMPLATE.format(year=y): r for y, r in replies.items()})
    return IceCotAdapter(client=client, today=lambda: today), client  # type: ignore[arg-type]


def _assert_contract_frame(f: pd.DataFrame, market: str) -> None:
    assert list(f.columns) == COT_FRAME_COLUMNS == [c for c in COT_COLUMNS if c != "period"] + ["published_at"]
    assert f.index.name == "period" and f.index.tz is None
    assert f.index.is_monotonic_increasing and f.index.is_unique
    assert (f.index == f.index.normalize()).all()
    assert (f["market"] == market).all()
    for c in ("oi", "mm_long", "mm_short", "mm_net", "prod_long", "prod_short", "swap_long", "swap_short"):
        assert str(f[c].dtype) == "int64", c
    assert (f["mm_net"] == f["mm_long"] - f["mm_short"]).all()
    assert str(f["published_at"].dt.tz) == "UTC"
    assert (f["published_at"] > f.index.tz_localize(UTC)).all()
    assert f.attrs["units"] == "contracts"


# ---------------------------------------------------------------------------------------------------------------
# published_at rule
# ---------------------------------------------------------------------------------------------------------------
def test_release_day_regular_week_is_friday() -> None:
    assert cot_release_day(date(2026, 9, 29)) == date(2026, 10, 2)
    # 15:30 New York = 19:30 UTC in summer, 20:30 UTC in winter
    assert cot_published_at(date(2026, 9, 29)) == datetime(2026, 10, 2, 19, 30, tzinfo=UTC)
    assert cot_published_at(date(2026, 1, 13)) == datetime(2026, 1, 16, 20, 30, tzinfo=UTC)


def test_release_day_friday_holiday_moves_to_monday() -> None:
    # Christmas Friday 2026-12-25 -> Monday 2026-12-28 (official schedule: "December 28*")
    assert cot_release_day(date(2026, 12, 22)) == date(2026, 12, 28)
    assert cot_published_at(date(2026, 12, 22)) == datetime(2026, 12, 28, 20, 30, tzinfo=UTC)
    # Juneteenth Friday 2026-06-19 -> Monday 2026-06-22; Independence Day observed Friday 2026-07-03 -> 2026-07-06
    assert cot_release_day(date(2026, 6, 16)) == date(2026, 6, 22)
    assert cot_release_day(date(2026, 6, 30)) == date(2026, 7, 6)


def test_release_day_midweek_holiday_moves_to_monday_but_monday_holiday_does_not() -> None:
    assert cot_release_day(date(2025, 12, 30)) == date(2026, 1, 5)  # New Year's Day Thursday
    assert cot_release_day(date(2026, 11, 24)) == date(2026, 11, 30)  # Thanksgiving Thursday
    assert cot_release_day(date(2026, 11, 10)) == date(2026, 11, 16)  # Veterans Day Wednesday (federal only)
    assert cot_release_day(date(2026, 1, 20)) == date(2026, 1, 23)  # MLK Monday: no delay
    assert cot_release_day(date(2026, 3, 31)) == date(2026, 4, 3)  # Good Friday is not a federal holiday


def test_release_day_handles_monday_and_wednesday_report_dates() -> None:
    # CFTC moves the report date when Tuesday is a holiday: 2012-12-24 (Mon), 2007-01-03 (Wed, Ford mourning day)
    assert cot_release_day(date(2012, 12, 24)) == date(2012, 12, 28)
    assert cot_release_day(date(2007, 1, 3)) == date(2007, 1, 5)
    assert cot_release_day(date(2023, 7, 3)) == date(2023, 7, 7)


def test_release_days_match_official_2026_schedule() -> None:
    official = {
        1: [5, 9, 16, 23, 30],
        2: [6, 13, 20, 27],
        3: [6, 13, 20, 27],
        4: [3, 10, 17, 24],
        5: [1, 8, 15, 22, 29],
        6: [5, 12, 22, 26],
        7: [6, 10, 17, 24, 31],
        8: [7, 14, 21, 28],
        9: [4, 11, 18, 25],
        10: [2, 9, 16, 23, 30],
        11: [6, 16, 20, 30],
        12: [4, 11, 18, 28],
    }
    expected = [date(2026, m, d) for m in sorted(official) for d in official[m]]
    periods = [date(2025, 12, 30) + timedelta(weeks=i) for i in range(len(expected))]
    assert [cot_release_day(p) for p in periods] == expected


def test_cftc_holidays_follow_the_federal_calendar() -> None:
    hols = cftc_holidays(2026)
    assert date(2026, 11, 11) in hols and date(2026, 4, 3) not in hols
    assert date(2026, 12, 25) in hols and date(2026, 1, 1) in hols
    assert date(2023, 11, 10) in cftc_holidays(2023)  # Veterans Day observed (Nov 11 is a Saturday)


# ---------------------------------------------------------------------------------------------------------------
# CFTC Socrata
# ---------------------------------------------------------------------------------------------------------------
def test_socrata_fetch_parses_fixture_and_paginates() -> None:
    client = FakeJsonClient(_socrata_rows())
    res = CftcAdapter(client=client, page_size=5).fetch()  # type: ignore[arg-type]

    assert [c["$offset"] for c in client.calls] == [0, 5, 10, 15]
    assert all(c["url"] == SOCRATA_URL and c["$limit"] == 5 for c in client.calls)
    assert all(c["$where"] == "cftc_contract_market_code='067651'" for c in client.calls)
    assert all(c["$order"] == "report_date_as_yyyy_mm_dd" for c in client.calls)
    assert client.calls[0]["$select"] == ",".join(SOCRATA_SELECT)
    assert "prod_merc_positions_long," in client.calls[0]["$select"]  # real field name: no _all suffix
    assert "swap__positions_short_all" in client.calls[0]["$select"]  # real field name: double underscore

    f = res.frame
    assert res.source == "cftc_socrata" and res.approx is False
    _assert_contract_frame(f, "wti")
    assert len(f) == 17
    assert f.index[0] == pd.Timestamp("2026-06-09") and f.index[-1] == pd.Timestamp("2026-09-29")
    assert set(f.index.weekday) == {1}
    last = f.loc[pd.Timestamp("2026-09-29")]
    assert last["oi"] == 1_878_576
    assert last["mm_long"] == 209_028 and last["mm_short"] == 129_436 and last["mm_net"] == 79_592
    assert last["prod_long"] == 615_887 and last["prod_short"] == 296_348
    assert last["swap_long"] == 113_558 and last["swap_short"] == 575_715
    assert last["published_at"] == pd.Timestamp("2026-10-02 19:30", tz=UTC)
    assert res.meta["pages"] == 4 and res.meta["rows"] == 17 and res.meta["market_code"] == "067651"
    assert res.meta["names"] == ["WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE"]
    assert res.meta["first"] == "2026-06-09" and res.meta["last"] == "2026-09-29"
    assert res.asof == datetime(2026, 9, 29, tzinfo=UTC)


def test_socrata_published_at_holiday_weeks_in_fixture() -> None:
    f = CftcAdapter(client=FakeJsonClient(_socrata_rows())).fetch().frame  # type: ignore[arg-type]
    assert f.loc[pd.Timestamp("2026-06-16"), "published_at"] == pd.Timestamp("2026-06-22 19:30", tz=UTC)
    assert f.loc[pd.Timestamp("2026-06-30"), "published_at"] == pd.Timestamp("2026-07-06 19:30", tz=UTC)
    assert f.loc[pd.Timestamp("2026-06-23"), "published_at"] == pd.Timestamp("2026-06-26 19:30", tz=UTC)


def test_socrata_single_page_when_short() -> None:
    client = FakeJsonClient(_socrata_rows())
    CftcAdapter(client=client).fetch("067651")  # type: ignore[arg-type]
    assert len(client.calls) == 1 and client.calls[0]["$limit"] == 50000


def test_socrata_empty_or_malformed_raises() -> None:
    with pytest.raises(DataUnavailable, match="no rows"):
        CftcAdapter(client=FakeJsonClient([])).fetch()  # type: ignore[arg-type]
    rows = [{k: v for k, v in r.items() if k != "m_money_positions_long_all"} for r in _socrata_rows()]
    with pytest.raises(DataUnavailable, match="lack fields"):
        CftcAdapter(client=FakeJsonClient(rows)).fetch()  # type: ignore[arg-type]


def test_socrata_drops_rows_with_null_values_and_rejects_duplicates() -> None:
    rows = _socrata_rows()
    rows[3] = {**rows[3], "open_interest_all": None}
    res = CftcAdapter(client=FakeJsonClient(rows)).fetch()  # type: ignore[arg-type]
    assert len(res.frame) == 16 and res.meta["dropped"] == ["2026-06-30"]
    assert pd.Timestamp("2026-06-30") not in res.frame.index

    rows = _socrata_rows()
    with pytest.raises(DataUnavailable, match="duplicate"):
        CftcAdapter(client=FakeJsonClient([*rows, rows[-1]])).fetch()  # type: ignore[arg-type]


def test_socrata_unknown_code_gets_slug_label() -> None:
    rows = [
        {**r, "cftc_contract_market_code": "06765T", "market_and_exchange_names": "BRENT LAST DAY - NYMEX"}
        for r in _socrata_rows()
    ]
    res = CftcAdapter(client=FakeJsonClient(rows)).fetch("06765T")  # type: ignore[arg-type]
    assert (res.frame["market"] == "brent_last_day").all()


# ---------------------------------------------------------------------------------------------------------------
# CFTC current-week text file
# ---------------------------------------------------------------------------------------------------------------
def test_cftc_file_fetch_current_matches_socrata_values() -> None:
    client = FakeBytesClient({CFTC_DISAGG_TXT_URL: (FIX / "f_disagg_2026-09-29_trimmed.txt").read_bytes()})
    res = CftcFileAdapter(client=client).fetch_current()  # type: ignore[arg-type]
    f = res.frame
    assert res.source == "cftc_file" and client.urls == [CFTC_DISAGG_TXT_URL]
    _assert_contract_frame(f, "wti")
    assert len(f) == 1 and f.index[0] == pd.Timestamp("2026-09-29")
    row = f.iloc[0]
    # same numbers as the Socrata row of 2026-09-29: verifies the header-less field positions
    assert row["oi"] == 1_878_576 and row["mm_long"] == 209_028 and row["mm_short"] == 129_436
    assert row["mm_net"] == 79_592 and row["prod_long"] == 615_887 and row["prod_short"] == 296_348
    assert row["swap_long"] == 113_558 and row["swap_short"] == 575_715
    assert row["published_at"] == pd.Timestamp("2026-10-02 19:30", tz=UTC)
    assert res.meta["names"] == ["WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE"]


def test_cftc_file_other_code_and_missing_code() -> None:
    client = FakeBytesClient({CFTC_DISAGG_TXT_URL: (FIX / "f_disagg_2026-09-29_trimmed.txt").read_bytes()})
    res = CftcFileAdapter(client=client).fetch_current("06765T")  # type: ignore[arg-type]
    assert (res.frame["market"] == "brent_last_day").all() and res.frame["oi"].iloc[0] == 243_324
    with pytest.raises(DataUnavailable, match="no futures-only row"):
        CftcFileAdapter(client=client).fetch_current("999999")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------------------
# ICE
# ---------------------------------------------------------------------------------------------------------------
def test_ice_year_loop_skips_404_years_and_keeps_futures_only() -> None:
    replies: dict[int, bytes | Exception] = dict.fromkeys(range(2011, 2027), NOT_FOUND)
    replies[2011] = (FIX / "COTHist2011_trimmed.csv").read_bytes()
    replies[2026] = (FIX / "COTHist2026_trimmed.csv").read_bytes()
    adapter, client = _ice(replies)
    res = adapter.fetch("brent")

    assert client.urls == [ICE_URL_TEMPLATE.format(year=y) for y in range(2011, 2027)]
    f = res.frame
    assert res.source == "ice_cot" and res.approx is False
    _assert_contract_frame(f, "brent")
    assert len(f) == 12  # 2 weeks of 2011 + 10 weeks of 2026, Combined rows excluded
    assert f.index[0] == pd.Timestamp("2011-01-04") and f.index[-1] == pd.Timestamp("2026-09-29")
    assert set(f.index.weekday) == {1}
    first = f.loc[pd.Timestamp("2011-01-04")]
    assert first["oi"] == 870_690  # FutOnly row (the Combined row of the same date has 878 891)
    assert first["mm_long"] == 133_347 and first["mm_short"] == 11_814 and first["mm_net"] == 121_533
    assert first["swap_short"] == 56_589  # read through the 'Swap__Positions_Short_All' header spelling
    assert first["published_at"] == pd.Timestamp("2011-01-07 20:30", tz=UTC)
    last = f.loc[pd.Timestamp("2026-09-29")]
    assert last["oi"] == 2_578_636
    assert last["mm_long"] == 311_738 and last["mm_short"] == 116_275 and last["mm_net"] == 195_463
    assert last["prod_long"] == 902_012 and last["prod_short"] == 1_242_289
    assert last["swap_long"] == 386_885 and last["swap_short"] == 85_509
    assert last["published_at"] == pd.Timestamp("2026-10-02 19:30", tz=UTC)
    assert res.meta["years"] == [2011, 2026] and res.meta["missing_years"] == list(range(2012, 2026))
    assert res.meta["market"] == "brent" and res.meta["market_name"] == ICE_MARKETS["brent"]
    assert res.meta["first"] == "2011-01-04" and res.meta["last"] == "2026-09-29"


def test_ice_gasoil_and_full_name_argument() -> None:
    replies: dict[int, bytes | Exception] = {2026: (FIX / "COTHist2026_trimmed.csv").read_bytes()}
    adapter, client = _ice(replies)
    res = adapter.fetch("gasoil", first_year=2026)
    f = res.frame
    _assert_contract_frame(f, "gasoil")
    assert len(f) == 10 and client.urls == [ICE_URL_TEMPLATE.format(year=2026)]
    last = f.loc[pd.Timestamp("2026-09-29")]
    assert last["oi"] == 749_721 and last["mm_long"] == 93_584 and last["mm_short"] == 24_239
    assert last["mm_net"] == 69_345

    adapter2, _ = _ice(replies)
    res2 = adapter2.fetch(ICE_MARKETS["gasoil"], first_year=2026)
    pd.testing.assert_frame_equal(res2.frame, f)


def test_ice_years_follow_today() -> None:
    replies: dict[int, bytes | Exception] = {2026: (FIX / "COTHist2026_trimmed.csv").read_bytes()}
    adapter, client = _ice(replies, today=date(2026, 1, 2))
    adapter.fetch("brent", first_year=2026)
    assert client.urls == [ICE_URL_TEMPLATE.format(year=2026)]
    with pytest.raises(DataUnavailable, match="after the current year"):
        adapter.fetch("brent", first_year=2027)


def test_ice_all_missing_or_unknown_market_raises() -> None:
    adapter, _ = _ice(dict.fromkeys(range(2025, 2027), NOT_FOUND))
    with pytest.raises(DataUnavailable, match="no futures-only rows"):
        adapter.fetch("brent", first_year=2025)
    adapter, _ = _ice({2026: (FIX / "COTHist2026_trimmed.csv").read_bytes()})
    with pytest.raises(DataUnavailable, match="ICE WTI Crude Futures"):
        adapter.fetch("ICE WTI Crude Futures - ICE Futures Europe", first_year=2026)


def test_ice_non_404_failure_propagates() -> None:
    replies: dict[int, bytes | Exception] = {
        2025: DataUnavailable("GET x failed after 4 attempts: x -> HTTP 503"),
        2026: (FIX / "COTHist2026_trimmed.csv").read_bytes(),
    }
    adapter, _ = _ice(replies)
    with pytest.raises(DataUnavailable, match="503"):
        adapter.fetch("brent", first_year=2025)


def test_parse_ice_csv_header_variants() -> None:
    text = (FIX / "COTHist2011_trimmed.csv").read_text(encoding="utf-8-sig")
    recs, seen = parse_ice_cot_csv(text, ICE_MARKETS["brent"])
    assert len(recs) == 2 and seen == set(ICE_MARKETS.values())
    assert recs["swap_short"].tolist() == ["56589", "11374"] or recs["swap_short"].iloc[0] == "56589"
    # a header without the swap column is rejected, an empty file too
    with pytest.raises(DataUnavailable, match="header lacks"):
        parse_ice_cot_csv("Market_and_Exchange_Names,As_of_Date_In_Form_YYMMDD\nfoo,260101\n", "foo")
    with pytest.raises(DataUnavailable, match="empty"):
        parse_ice_cot_csv("", "foo")


# ---------------------------------------------------------------------------------------------------------------
# network (opt-in)
# ---------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def live(request: pytest.FixtureRequest) -> None:
    """Skip the network tests in the default offline run (OPT_OFFLINE=1 without ``-m network``)."""
    selected = "network" in str(request.config.getoption("-m") or "")
    if os.environ.get("OPT_OFFLINE") == "1" and not selected:
        pytest.skip("network tests are opt-in: run `pytest -m network`")


@pytest.mark.network
def test_network_cftc_socrata_wti_history(live: None) -> None:
    res = CftcAdapter().fetch()
    f = res.frame
    _assert_contract_frame(f, "wti")
    assert f.index[0] == pd.Timestamp("2006-06-13") and f.index[-1] >= pd.Timestamp("2026-09-29")
    assert len(f) >= 1060
    assert f.loc[pd.Timestamp("2026-09-29"), "mm_net"] == 79_592
    assert set(res.meta["names"]) >= {
        "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",
        "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
    }
    # continuity across the 2022-02 relabelling: consecutive weekly rows, no overlap
    assert pd.Timestamp("2022-02-01") in f.index and pd.Timestamp("2022-02-08") in f.index


@pytest.mark.network
def test_network_cftc_file_current_week(live: None) -> None:
    res = CftcFileAdapter().fetch_current()
    _assert_contract_frame(res.frame, "wti")
    assert len(res.frame) == 1 and res.frame.index[0] >= pd.Timestamp("2026-09-29")


@pytest.mark.network
def test_network_ice_brent_full_history_from_2011(live: None) -> None:
    res = IceCotAdapter().fetch("brent")
    f = res.frame
    _assert_contract_frame(f, "brent")
    assert f.index[0] == pd.Timestamp("2011-01-04") and f.index[-1] >= pd.Timestamp("2026-09-29")
    assert res.meta["missing_years"] == [] and res.meta["years"][0] == 2011
    assert f.loc[pd.Timestamp("2026-09-29"), "mm_net"] == 195_463
    assert len(f) >= 820


@pytest.mark.network
def test_network_ice_gasoil_recent_and_2010_missing(live: None) -> None:
    res = IceCotAdapter().fetch("gasoil", first_year=2025)
    _assert_contract_frame(res.frame, "gasoil")
    assert res.frame.index[-1] >= pd.Timestamp("2026-09-29") and len(res.frame) >= 90
    with pytest.raises(DataUnavailable, match="404"):
        IceCotAdapter().client.get_bytes(ICE_URL_TEMPLATE.format(year=2010))
