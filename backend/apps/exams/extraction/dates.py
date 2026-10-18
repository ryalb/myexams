import re
from datetime import date, datetime

from django.utils import timezone

_DATETIME = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:[\s\-–|]*(\d{2}):(\d{2})(?::(\d{2}))?)?")


def parse_br_datetime(text: str | None) -> datetime | None:
    """'25/08/2026 - 07:01:57' (ou só a data) -> datetime com fuso local."""
    match = _DATETIME.search(text or "")
    if not match:
        return None
    day, month, year, hour, minute, second = match.groups()
    try:
        naive = datetime(
            int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0)
        )
    except ValueError:
        return None
    return timezone.make_aware(naive)


def parse_br_date(text: str | None) -> date | None:
    parsed = parse_br_datetime(text)
    return parsed.date() if parsed else None


def age_on(birth: date | None, on: date) -> int | None:
    if birth is None:
        return None
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years
