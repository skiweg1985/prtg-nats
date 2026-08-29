"""Identifiers.

ULIDs everywhere: sortable by creation time, safe in a URL, and they make a
job list ordered by primary key also ordered by age without a second index.
"""

from __future__ import annotations

from ulid import ULID


def new_id() -> str:
    return str(ULID())
