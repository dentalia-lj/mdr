"""`app.dates.parse_date`: one reading of a document date for VALIDATE and GATE.

A printed day past the end of its month (Sirona's "April 31st, 2020", doc 1502)
moves to the month's last day (Denis, 2026-11-12). Everything else that is not an
ISO date stays unparseable, so VALIDATE still flags it `date-insane`.
"""
import datetime

import pytest

from app.dates import parse_date


@pytest.mark.parametrize("raw, expected", [
    ("2020-04-31", "2020-04-30"),     # doc 1502: April has 30 days
    ("2021-02-29", "2021-02-28"),     # not a leap year
    ("2021-02-30", "2021-02-28"),
    ("2024-02-30", "2024-02-29"),     # a leap year keeps the 29th
    ("2021-02-31", "2021-02-28"),
    ("2023-11-31", "2023-11-30"),
])
def test_a_day_past_the_month_end_moves_to_the_last_day(raw, expected):
    assert parse_date(raw) == datetime.date.fromisoformat(expected)


@pytest.mark.parametrize("raw", ["2024-03-15", "2024-02-29", "2020-04-30", "2020-12-31"])
def test_a_real_date_is_unchanged(raw):
    assert parse_date(raw) == datetime.date.fromisoformat(raw)


@pytest.mark.parametrize("raw", [
    "2020-04-32",           # no month has 32 days: a misread, not a month end
    "2020-04-00",
    "2020-13-01",           # no 13th month
    "2020-00-10",
    "2025",
    "2026-04",
    "12 February 2026",
    "n.a.",
    "",
    "2020-4-31",            # not ISO-shaped; left to the existing refusal
])
def test_anything_else_stays_unparseable(raw):
    assert parse_date(raw) is None


def test_none_and_real_dates_pass_through():
    assert parse_date(None) is None
    d = datetime.date(2022, 10, 28)
    assert parse_date(d) is d
