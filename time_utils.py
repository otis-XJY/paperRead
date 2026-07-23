"""Shared timestamp normalization for workflow notifications and state."""

from datetime import datetime, timezone


DEFAULT_LAST_DATE = "2000-01-01T00:00:00Z"


def parse_utc_timestamp(value):
    """Parse an ISO timestamp or OAI-PMH date as an aware UTC datetime."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc_timestamp(value) -> str:
    """Return a canonical UTC timestamp for state and paper records."""
    parsed = value if isinstance(value, datetime) else parse_utc_timestamp(value)
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def newer_timestamp(left, right) -> bool:
    """Compare timestamps by instant rather than by source string format."""
    left_dt = parse_utc_timestamp(left)
    right_dt = parse_utc_timestamp(right)
    return left_dt is not None and (right_dt is None or left_dt > right_dt)
