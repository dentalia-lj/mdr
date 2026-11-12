"""One reading of a document date, shared by VALIDATE and GATE.

VALIDATE decides whether a date is sane with it, and GATE writes the
`validity_from` / `validity_to` columns with it. They must read a value the same
way, or a date VALIDATE accepts could reach the column as nothing.
"""
from __future__ import annotations

import calendar
import datetime
import re

#: The ISO shape a reading carries, before the calendar is consulted.
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_date(v):
    """A `datetime.date`, or None when `v` is no date at all.

    A day past the end of its month moves to that month's last day (Denis,
    2026-11-12). Sirona's declarations print "April 31st, 2020" (doc 1502), a
    day that does not exist: the date is meant, only miswritten, and the
    browser's date field cannot hold 31.04 for the reviewer to keep it. Only
    days 29 to 31 move; day 0 or 32 and a month outside 1 to 12 are misreads and
    stay unparseable, so VALIDATE still flags them `date-insane`. The evidence
    row keeps the value as read.
    """
    if v is None:
        return None
    if not isinstance(v, str):
        return v  # already a date or datetime (a row read back from `document`)
    try:
        return datetime.date.fromisoformat(v)
    except ValueError:
        pass
    m = _ISO.match(v)
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    if year < 1 or not 1 <= month <= 12 or not 29 <= day <= 31:
        return None
    last = calendar.monthrange(year, month)[1]
    if day <= last:
        return None  # a real day that fromisoformat refused: not ours to guess
    return datetime.date(year, month, last)
