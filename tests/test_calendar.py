from datetime import date

from engine.core.calendar import (
    contract_code,
    december_contract,
    front_month,
    ice_brent_expiry,
    is_business_day,
    nth_month,
    nymex_wti_expiry,
    parse_contract_code,
    uk_holidays,
)


def test_ice_brent_expiry_rule_examples():
    # Verified against ICE: Nov-26 expired 2026-09-30; Dec-26 expires 2026-10-30; Mar-27 -> last bd of Jan 2027
    assert ice_brent_expiry(2026, 11) == date(2026, 9, 30)
    assert ice_brent_expiry(2026, 12) == date(2026, 10, 30)
    assert ice_brent_expiry(2027, 3) == date(2027, 1, 29)


def test_ice_brent_expiry_new_year_exception():
    # Feb-27: last bd of Dec 2026 is Thu 31 Dec, the business day before New Year's Day -> one bd earlier
    assert ice_brent_expiry(2027, 2) == date(2026, 12, 30)


def test_ice_brent_expiry_christmas_exception():
    # Feb-25: last bd of Dec 2024 is Tue 31 Dec, bd before 1 Jan 2025 -> 30 Dec 2024
    assert ice_brent_expiry(2025, 2) == date(2024, 12, 30)


def test_front_month_includes_expiry_day():
    assert contract_code("BZ", *front_month("BZ", date(2026, 9, 30))) == "BZX26"
    assert contract_code("BZ", *front_month("BZ", date(2026, 10, 1))) == "BZZ26"
    assert contract_code("BZ", *front_month("BZ", date(2026, 10, 5))) == "BZZ26"
    # with a 3-day roll buffer the front rolls early
    assert contract_code("BZ", *front_month("BZ", date(2026, 10, 28), roll_buffer_days=3)) == "BZF27"


def test_nth_month_and_december():
    assert contract_code("BZ", *nth_month("BZ", date(2026, 10, 5), 2)) == "BZF27"
    assert december_contract("BZ", date(2026, 10, 5), 1) == (2026, 12)
    assert december_contract("BZ", date(2026, 10, 5), 2) == (2027, 12)


def test_wti_expiry():
    # CME: Nov-26 WTI last trade 2026-10-20 (3 bd before 25 Oct, Sunday -> Fri 23 -> 20)
    assert nymex_wti_expiry(2026, 11) == date(2026, 10, 20)
    assert nymex_wti_expiry(2026, 12) == date(2026, 11, 20)


def test_codes_roundtrip():
    assert contract_code("BZ", 2026, 12) == "BZZ26"
    assert parse_contract_code("BZZ26") == ("BZ", 2026, 12)
    assert parse_contract_code("CLF27") == ("CL", 2027, 1)


def test_uk_holidays_2026():
    h = uk_holidays(2026)
    assert date(2026, 4, 3) in h and date(2026, 4, 6) in h  # Good Friday, Easter Monday
    assert date(2026, 12, 28) in h  # Boxing Day substitute
    assert not is_business_day(date(2026, 12, 25))
    assert is_business_day(date(2026, 10, 5))
