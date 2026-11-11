"""NSE regular-session calendar used by the 09:15 executable-list lock.

The published 2026 holiday table is the same table the VPS news producer uses.
Weekdays alone are not sessions: exchange holidays must never create an entry
window. The table must be extended before 2027 and exceptional special sessions
remain excluded unless the plan is explicitly changed.
"""
from __future__ import annotations

from datetime import date

CALENDAR_YEAR = 2026
HOLIDAYS = frozenset({
    date(2026, 1, 15), date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26),
    date(2026, 3, 31), date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1),
    date(2026, 5, 28), date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2),
    date(2026, 10, 20), date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
})


def is_regular_session(day: date) -> bool:
    """True only for a weekday in a supported year that is not an NSE holiday.

    Outside the published calendar year the answer is False: an unknown holiday
    must never open an entry window. Extend CALENDAR_YEAR and HOLIDAYS together.
    """
    if not calendar_supported(day):
        return False
    return day.weekday() < 5 and day not in HOLIDAYS


def calendar_supported(day: date) -> bool:
    return day.year == CALENDAR_YEAR
