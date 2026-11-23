"""Within-file duplicate detection.

Shared between the check that reports duplicates and the pipeline that drops
them, so the figure in the report cannot disagree with what was stored.
"""

from __future__ import annotations

from app.adapters import CanonicalRow


def partition_duplicates(
    rows: list[CanonicalRow],
) -> tuple[list[CanonicalRow], list[CanonicalRow]]:
    """Split into (kept, dropped) on the (campaign_key, date) grain.

    First occurrence wins. Rows arrive in file order, so "first" means the
    earliest line — explainable to anyone who opens the source file.
    """
    seen: set[tuple[str, str]] = set()
    kept: list[CanonicalRow] = []
    dropped: list[CanonicalRow] = []

    for row in rows:
        key = (row.campaign_key, row.date.isoformat())
        if key in seen:
            dropped.append(row)
        else:
            seen.add(key)
            kept.append(row)

    return kept, dropped
