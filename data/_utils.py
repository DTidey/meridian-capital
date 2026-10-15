"""Small helpers shared by the data modules."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current UTC time as a naive datetime: the same value as the deprecated datetime.utcnow."""
    return datetime.now(UTC).replace(tzinfo=None)
