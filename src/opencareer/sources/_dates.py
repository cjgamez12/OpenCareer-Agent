"""Date helpers shared by the job sources. Bad or missing values become None."""
from datetime import date, datetime, timezone


def parse_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):          # Lever: milliseconds since 1970
        seconds = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        try:
            return datetime.fromisoformat(str(value)[:19])
        except ValueError:
            return None


def parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
