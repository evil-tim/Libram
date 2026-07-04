"""Date and timezone utilities."""

from datetime import datetime
from zoneinfo import ZoneInfo


def convert_to_timezone_aware(date_str: str, timezone_str: str) -> datetime:
    """Convert a date string to a timezone-aware datetime object.

    Args:
        date_str: Date string in format YYYY-MM-DDTHH:MM:SS
        timezone_str: IANA timezone name (e.g., 'UTC', 'Asia/Manila')

    Returns:
        timezone-aware datetime object
    """
    # parse the date string into components
    dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
    year = dt.year
    month = dt.month
    day = dt.day
    hour = dt.hour
    minute = dt.minute
    second = dt.second
    return datetime(
        year, month, day, hour, minute, second, tzinfo=ZoneInfo(timezone_str)
    )
