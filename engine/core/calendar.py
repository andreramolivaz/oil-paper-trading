"""Exchange calendars and futures expiry rules.

ICE Brent (verified on ice.com, 2026-10-05): trading ceases on the last business day of the second month
preceding the contract month (March contract -> last business day of January). If that day is the business
day before Christmas Day or New Year's Day, trading ceases one business day earlier.

NYMEX WTI (CME rulebook): trading ceases 3 business days before the 25th calendar day of the month preceding
the contract month; if the 25th is not a business day, 3 business days before the last business day preceding
the 25th.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import cache

MONTH_CODES = "FGHJKMNQUVXZ"  # Jan..Dec
CODE_TO_MONTH = {c: i + 1 for i, c in enumerate(MONTH_CODES)}


def _easter(year: int) -> date:
    """Anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """UK/US style: Saturday -> Friday? No: UK moves to Monday; we use 'next Monday' for weekend holidays."""
    if d.weekday() == 5:
        return d + timedelta(days=2)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


@cache
def uk_holidays(year: int) -> frozenset[date]:
    """England & Wales bank holidays (ICE Futures Europe follows these for Brent business days)."""
    easter = _easter(year)
    hols = {
        _observed(date(year, 1, 1)),
        easter - timedelta(days=2),  # Good Friday
        easter + timedelta(days=1),  # Easter Monday
        _nth_weekday(year, 5, 0, 1),  # Early May
        _last_weekday(year, 5, 0),  # Spring
        _last_weekday(year, 8, 0),  # Summer
    }
    xmas = date(year, 12, 25)
    boxing = date(year, 12, 26)
    # Christmas/Boxing substitute days
    if xmas.weekday() == 5 or xmas.weekday() == 6:
        hols |= {date(year, 12, 27), date(year, 12, 28)}
    elif boxing.weekday() == 5:
        hols |= {xmas, date(year, 12, 28)}
    else:
        hols |= {xmas, boxing}
    # one-off UK holidays in the range we care about
    extras = {
        2011: [date(2011, 4, 29)],
        2012: [date(2012, 6, 5)],
        2022: [date(2022, 6, 3), date(2022, 9, 19)],
        2023: [date(2023, 5, 8)],
    }
    for d in extras.get(year, []):
        hols.add(d)
    if year == 2012:
        hols.discard(_last_weekday(2012, 5, 0))
        hols.add(date(2012, 6, 4))
    if year == 2022:
        hols.discard(_last_weekday(2022, 5, 0))
        hols.add(date(2022, 6, 2))
    return frozenset(hols)


@cache
def us_holidays(year: int) -> frozenset[date]:
    """NYSE/NYMEX-style US holidays (good enough for WTI expiry and EIA release shifts)."""

    def obs(d: date) -> date:
        if d.weekday() == 5:
            return d - timedelta(days=1)
        if d.weekday() == 6:
            return d + timedelta(days=1)
        return d

    easter = _easter(year)
    hols = {
        obs(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # MLK
        _nth_weekday(year, 2, 0, 3),  # Presidents
        easter - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial
        obs(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        obs(date(year, 12, 25)),
    }
    if year >= 2022:
        hols.add(obs(date(year, 6, 19)))  # Juneteenth
    return frozenset(hols)


def is_business_day(d: date, market: str = "ICE") -> bool:
    if d.weekday() >= 5:
        return False
    hols = uk_holidays(d.year) if market == "ICE" else us_holidays(d.year)
    return d not in hols


def add_business_days(d: date, n: int, market: str = "ICE") -> date:
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d += timedelta(days=step)
        if is_business_day(d, market):
            remaining -= 1
    return d


def last_business_day(year: int, month: int, market: str = "ICE") -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while not is_business_day(d, market):
        d -= timedelta(days=1)
    return d


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def ice_brent_expiry(contract_year: int, contract_month: int) -> date:
    """Last trading day of the ICE Brent future for the given delivery month."""
    y, m = _shift_month(contract_year, contract_month, -2)
    d = last_business_day(y, m, "ICE")
    # Christmas / New Year exception: if d is the business day before 25 Dec or 1 Jan, one business day earlier.
    for holiday in (date(d.year, 12, 25), date(d.year + 1, 1, 1), date(d.year, 1, 1)):
        prev_bd = holiday - timedelta(days=1)
        while not is_business_day(prev_bd, "ICE"):
            prev_bd -= timedelta(days=1)
        if d == prev_bd:
            return add_business_days(d, -1, "ICE")
    return d


def nymex_wti_expiry(contract_year: int, contract_month: int) -> date:
    """Last trading day of the NYMEX WTI future for the given delivery month."""
    y, m = _shift_month(contract_year, contract_month, -1)
    d25 = date(y, m, 25)
    anchor = d25
    while not is_business_day(anchor, "US"):
        anchor -= timedelta(days=1)
    return add_business_days(anchor, -3, "US")


def contract_code(root: str, year: int, month: int) -> str:
    """E.g. contract_code('BZ', 2026, 12) -> 'BZZ26'."""
    return f"{root}{MONTH_CODES[month - 1]}{year % 100:02d}"


def parse_contract_code(code: str) -> tuple[str, int, int]:
    """'BZZ26' -> ('BZ', 2026, 12). Two-digit years are mapped to 2000-2099."""
    root, mcode, yy = code[:-3], code[-3], int(code[-2:])
    return root, 2000 + yy, CODE_TO_MONTH[mcode]


def expiry_for(root: str, year: int, month: int) -> date:
    if root == "BZ":
        return ice_brent_expiry(year, month)
    if root == "CL":
        return nymex_wti_expiry(year, month)
    if root in {"RB", "HO"}:
        # NYMEX RBOB/HO: last business day of the month preceding the delivery month.
        y, m = _shift_month(year, month, -1)
        return last_business_day(y, m, "US")
    raise ValueError(f"unknown root {root}")


def listed_months(root: str, asof: date, n: int, roll_buffer_days: int = 0) -> list[tuple[int, int]]:
    """The first n contract months whose expiry is at least `roll_buffer_days` after `asof`.

    With roll_buffer_days=0 the expiring contract is still the front on its last trading day.
    """
    out: list[tuple[int, int]] = []
    y, m = asof.year, asof.month
    # start a couple of months back to be safe, then filter by expiry
    y, m = _shift_month(y, m, -1)
    while len(out) < n:
        if (expiry_for(root, y, m) - asof).days >= roll_buffer_days:
            out.append((y, m))
        y, m = _shift_month(y, m, 1)
    return out


def front_month(root: str, asof: date, roll_buffer_days: int = 0) -> tuple[int, int]:
    return listed_months(root, asof, 1, roll_buffer_days)[0]


def nth_month(root: str, asof: date, n: int, roll_buffer_days: int = 0) -> tuple[int, int]:
    """1-based: n=1 is the front month."""
    return listed_months(root, asof, n, roll_buffer_days)[n - 1]


def december_contract(root: str, asof: date, years_ahead: int = 1) -> tuple[int, int]:
    """The nearest December contract at least `years_ahead` Decembers away that is still listed."""
    y = asof.year
    cands = [(yy, 12) for yy in range(y, y + years_ahead + 3)]
    cands = [c for c in cands if expiry_for(root, *c) > asof]
    return cands[max(0, years_ahead - 1)]
