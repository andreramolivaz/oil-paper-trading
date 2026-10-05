"""engine.data.adapters.rigs: link discovery on the real Baker Hughes page + parser tests on synthetic workbooks.

``tests/fixtures/rigs/na-rig-count_2026-10-05.html`` is the real page (HTTP 200, 2026-10-05 15:57 UTC). The two
workbooks are SYNTHETIC - they reproduce the real sheet names, header rows and column layouts with the real
numbers of the current weeks, because the published files are 7.3 MB and 0.7 MB (see the fixtures' README).
Network tests are opt-in (``pytest -m network``) and tolerate the 503s the site serves from this container.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from engine.core.errors import DataUnavailable
from engine.data.adapters.rigs import (
    RIG_COLUMNS,
    BakerHughesAdapter,
    find_workbook_links,
    parse_rig_workbook,
    release_timestamp,
)

FIX = Path(__file__).parent / "fixtures" / "rigs"
PAGE = FIX / "na-rig-count_2026-10-05.html"
LONG_WB = FIX / "nam_weekly_long_SYNTHETIC.xlsx"
WIDE_WB = FIX / "us_oil_gas_split_wide_SYNTHETIC.xlsx"
PAGE_URL = "https://rigcount.bakerhughes.com/na-rig-count"
CURRENT_URL = "https://rigcount.bakerhughes.com/static-files/07b97f18-e525-4a1a-946a-973a03b0ae79"
ARCHIVE_URL = "https://rigcount.bakerhughes.com/static-files/48162dfc-eb21-4612-8b01-72743e3ed420"


class FakeResponse:
    def __init__(self, body: bytes):
        self.content = body
        self.status_code = 200

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class FakeClient:
    def __init__(self, replies: dict[str, bytes | Exception]):
        self.replies = replies
        self.urls: list[str] = []

    def _reply(self, url: str) -> bytes:
        self.urls.append(url)
        reply = self.replies.get(url)
        if reply is None:
            raise DataUnavailable(f"GET {url} -> HTTP 503")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> FakeResponse:
        return FakeResponse(self._reply(url))

    def get_bytes(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> bytes:
        return self._reply(url)


# ------------------------------------------------------------------------------------------------ link discovery
def test_find_workbook_links_on_the_real_page():
    links = find_workbook_links(PAGE.read_text(errors="replace"), PAGE_URL)
    titles = [link.title for link in links]
    # the current weekly report comes first (its title carries the release date), then the archives
    assert titles[0] == "10-02-2026 North_America Rig_Count Report.xlsx"
    assert links[0].report_date == date(2026, 10, 2)
    assert links[0].url == CURRENT_URL
    assert links[0].extension == ".xlsx"
    assert titles[1].startswith("08-29-2025")  # the previous "new report" workbook
    assert ARCHIVE_URL in [link.url for link in links]
    archive = next(link for link in links if link.url == ARCHIVE_URL)
    assert archive.extension == ".xlsb" and archive.report_date is None
    # pivot tables, by-state and average workbooks are filtered out
    assert not any("Pivot" in t or "by State" in t or "Average" in t for t in titles)
    assert not any("Workover" in t for t in titles)


def test_find_workbook_links_on_an_empty_page():
    assert find_workbook_links("<html><body>503</body></html>", PAGE_URL) == []


# --------------------------------------------------------------------------------------------------- the parsers
def test_parse_long_layout_real_numbers():
    frame = parse_rig_workbook(LONG_WB.read_bytes(), ".xlsx")
    assert frame.attrs["layout"] == "long" and frame.attrs["sheet"] == "NAM Weekly"
    assert list(frame.columns) == [*RIG_COLUMNS, "published_at"]
    assert frame.index.name == "week" and frame.index.tz is None
    assert list(frame.index.strftime("%Y-%m-%d")) == ["2026-09-18", "2026-09-25", "2026-10-02"]
    # Canada rows must not leak into the US count: total = oil + gas + misc of the US rows only
    assert frame.loc["2026-10-02", "rigs_oil"] == 456
    assert frame.loc["2026-10-02", "rigs_gas"] == 133
    assert frame.loc["2026-10-02", "rigs_total"] == 598
    assert frame.loc["2026-09-25", "rigs_total"] == 599
    # published_at = Friday 13:00 New York -> 17:00 UTC in October (EDT)
    assert frame.loc["2026-10-02", "published_at"] == pd.Timestamp("2026-10-02 17:00:00+00:00")


def test_parse_wide_layout_real_numbers():
    frame = parse_rig_workbook(WIDE_WB.read_bytes(), ".xlsx")
    assert frame.attrs["layout"] == "wide" and frame.attrs["sheet"] == "US Oil & Gas Split"
    assert frame.loc["2024-03-28", "rigs_oil"] == 506
    assert frame.loc["2024-03-28", "rigs_gas"] == 112
    assert frame.loc["2024-03-28", "rigs_total"] == 621
    assert frame.index.is_monotonic_increasing


def test_parse_rejects_garbage():
    with pytest.raises(DataUnavailable, match="cannot open"):
        parse_rig_workbook(b"<html>503 Service Unavailable</html>", ".xlsx")


def test_parse_rejects_a_workbook_without_a_weekly_sheet():
    import io

    bio = io.BytesIO()
    with pd.ExcelWriter(bio) as xw:
        pd.DataFrame({"foo": [1, 2], "bar": [3, 4]}).to_excel(xw, sheet_name="Other", index=False)
    with pytest.raises(DataUnavailable, match="no sheet with a weekly US rig count"):
        parse_rig_workbook(bio.getvalue(), ".xlsx")


def test_release_timestamp_handles_dst():
    assert release_timestamp(date(2026, 10, 2)) == pd.Timestamp("2026-10-02 17:00:00+00:00")  # EDT
    assert release_timestamp(date(2026, 1, 2)) == pd.Timestamp("2026-01-02 18:00:00+00:00")  # EST


# ---------------------------------------------------------------------------------------------------- the adapter
def test_fetch_merges_the_archive_history_under_the_current_report():
    client = FakeClient(
        {
            PAGE_URL: PAGE.read_bytes(),
            CURRENT_URL: LONG_WB.read_bytes(),
            ARCHIVE_URL: WIDE_WB.read_bytes(),
        }
    )
    res = BakerHughesAdapter(client=client).fetch()  # type: ignore[arg-type]
    assert res.source == "baker_hughes"
    frame = res.frame
    # the archive's older weeks are prepended, the current report wins on the overlap
    assert list(frame.index.strftime("%Y-%m-%d")) == [
        "2024-03-15",
        "2024-03-22",
        "2024-03-28",
        "2026-09-18",
        "2026-09-25",
        "2026-10-02",
    ]
    assert frame.loc["2026-10-02", "rigs_total"] == 598
    assert frame.loc["2024-03-28", "rigs_total"] == 621
    roles = {w["role"]: w["layout"] for w in res.meta["workbooks"]}
    assert roles == {"current": "long", "history": "wide"}
    assert res.meta["published_at_rule"].startswith("publication Friday 13:00")
    assert res.meta["last_week"] == "2026-10-02"


def test_fetch_without_history_only_reads_the_current_report():
    client = FakeClient({PAGE_URL: PAGE.read_bytes(), CURRENT_URL: LONG_WB.read_bytes()})
    res = BakerHughesAdapter(client=client).fetch(history=False)  # type: ignore[arg-type]
    assert len(res.frame) == 3
    assert client.urls == [PAGE_URL, CURRENT_URL]


def test_fetch_skips_an_unreadable_workbook_and_uses_the_next_one():
    client = FakeClient(
        {
            PAGE_URL: PAGE.read_bytes(),
            CURRENT_URL: b"<html>503</html>",  # the newest report is broken
            "https://rigcount.bakerhughes.com/static-files/e98bcf83-c458-4a88-8f35-4ac4d77628bb": LONG_WB.read_bytes(),
        }
    )
    res = BakerHughesAdapter(client=client).fetch(history=False)  # type: ignore[arg-type]
    assert len(res.frame) == 3
    assert res.meta["failed"] and "10-02-2026" in res.meta["failed"][0]


def test_fetch_raises_when_the_page_is_unreachable():
    with pytest.raises(DataUnavailable, match="Baker Hughes page"):
        BakerHughesAdapter(client=FakeClient({})).fetch()  # type: ignore[arg-type]


def test_fetch_raises_when_every_workbook_fails():
    client = FakeClient({PAGE_URL: PAGE.read_bytes()})
    with pytest.raises(DataUnavailable, match="no usable workbook"):
        BakerHughesAdapter(client=client).fetch()  # type: ignore[arg-type]


def test_fetch_raises_when_the_page_has_no_workbook_link():
    client = FakeClient({PAGE_URL: b"<html><body>manutenzione</body></html>"})
    with pytest.raises(DataUnavailable, match="no rig-count workbook link"):
        BakerHughesAdapter(client=client).fetch()  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------ network (opt-in)
@pytest.fixture(scope="module")
def live(request: pytest.FixtureRequest) -> None:
    """Skip the network tests in the default offline run (OPT_OFFLINE=1 without ``-m network``)."""
    selected = "network" in str(request.config.getoption("-m") or "")
    if os.environ.get("OPT_OFFLINE") == "1" and not selected:
        pytest.skip("network tests are opt-in: run `pytest -m network`")


@pytest.mark.network
def test_network_baker_hughes_current_week(live: None):
    """The real site (it served 503/timeouts before 2026-10-05: a clean DataUnavailable is an accepted outcome)."""
    try:
        res = BakerHughesAdapter().fetch()
    except DataUnavailable as e:
        pytest.xfail(f"rigcount.bakerhughes.com unreachable: {e}")
    frame = res.frame
    assert len(frame) > 1500
    assert frame.index[0].year <= 1990
    last = frame.iloc[-1]
    assert 200 < last["rigs_oil"] < 2000
    assert last["rigs_total"] >= last["rigs_oil"] + last["rigs_gas"]
    assert frame.index[-1] >= pd.Timestamp("2026-09-25")
