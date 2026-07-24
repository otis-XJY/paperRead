"""Pure helpers for chronological paper progress checkpoints."""

from __future__ import annotations

import re

from time_utils import DEFAULT_LAST_DATE, format_utc_timestamp, newer_timestamp


def advance_contiguous_cursor(current_date, candidates, history):
    """Advance only across consecutive handled papers in publication order."""
    cursor = format_utc_timestamp(current_date) or DEFAULT_LAST_DATE
    history_set = set(history or [])
    history_base_set = {re.sub(r"v\d+$", "", item) for item in history_set}
    ordered = sorted(
        candidates or [],
        key=lambda item: (
            format_utc_timestamp(item.get("published", "")) or DEFAULT_LAST_DATE,
            item.get("id", ""),
        ),
    )
    for candidate in ordered:
        published = format_utc_timestamp(candidate.get("published", ""))
        if not published or not newer_timestamp(published, cursor):
            continue
        paper_id = candidate.get("id", "")
        paper_base = re.sub(r"v\d+$", "", paper_id)
        if paper_id not in history_set and paper_base not in history_base_set:
            break
        cursor = published
    return cursor
