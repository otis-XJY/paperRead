"""Deterministic paper-title deduplication helpers.

The rules here are deliberately local and explainable: no LLM calls and no
network access are needed to decide whether two titles represent a duplicate.
"""

import re
import unicodedata
from difflib import SequenceMatcher


TITLE_SIMILARITY_THRESHOLD = 0.90


def normalize_title(title):
    """Normalize superficial title differences for deterministic comparison."""
    text = unicodedata.normalize("NFKC", str(title or "")).casefold()
    tokens = re.findall(r"[\w]+", text, flags=re.UNICODE)
    return " ".join(tokens)


def title_similarity(left, right):
    """Return a conservative title similarity score from 0.0 to 1.0."""
    left_normalized = normalize_title(left)
    right_normalized = normalize_title(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0

    character_ratio = SequenceMatcher(
        None, left_normalized, right_normalized
    ).ratio()
    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    token_jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(character_ratio, token_jaccard)


def _record_sort_key(record):
    """Prefer the oldest record; keep a deterministic order when time is absent."""
    created_at = str(record.get("created_at") or "")
    return (not bool(created_at), created_at, str(record.get("id") or ""))


def find_duplicate_groups(records, threshold=TITLE_SIMILARITY_THRESHOLD):
    """Group title records whose normalized/estimated similarity meets threshold.

    Each result is a list ordered as ``[kept_record, duplicate_record, ...]``.
    """
    records = sorted(
        (record for record in records if str(record.get("title") or "").strip()),
        key=_record_sort_key,
    )
    groups = []
    for record in records:
        # Compare only with the kept (oldest) record of each group. This avoids
        # transitive chains that could otherwise remove two titles that are not
        # themselves at least 90% similar.
        group = next(
            (
                candidate
                for candidate in groups
                if title_similarity(candidate[0]["title"], record["title"]) >= threshold
            ),
            None,
        )
        if group is None:
            groups.append([record])
        else:
            group.append(record)
    return [group for group in groups if len(group) > 1]
