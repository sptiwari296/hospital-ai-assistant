"""One place that answers "what time is it?" in the hospital's timezone.

Tools never call datetime.now() directly. Tests freeze the clock so results such as
"upcoming appointments" stay the same next month.
"""
import os
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

_frozen: datetime | None = None


def now() -> datetime:
    """Current hospital-local time (naive datetime, same convention as the database)."""
    if _frozen is not None:
        return _frozen
    return datetime.now(ZoneInfo(os.getenv("HOSPITAL_TIMEZONE", "Asia/Kolkata"))).replace(tzinfo=None)


def today() -> date:
    return now().date()


@contextmanager
def frozen_at(moment: datetime):
    global _frozen
    previous, _frozen = _frozen, moment
    try:
        yield
    finally:
        _frozen = previous
