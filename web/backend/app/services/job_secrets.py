"""Values a job needs once and nothing may write down.

An administrator's password for a host we are about to enrol exists between the
operator's form and the one connection that uses it, and nowhere else. It must
not reach the job payload, because that is a row in the database, and it must
not reach the log, because that is read by more people than the form was.

So it is held here, in this process, keyed by job id, and taken - not read - by
the runner when the job starts. Process-wide rather than a field on the runner
because the two ends are different objects: an API request hands the value over
and a worker task collects it, and in a test the two are not even created by
the same code.
"""

from __future__ import annotations

_PENDING: dict[str, dict[str, str]] = {}


def hand(job_id: str, values: dict[str, str]) -> None:
    """Leave values for a job that has been created but not yet claimed."""
    if values:
        _PENDING[job_id] = dict(values)


def take(job_id: str) -> dict[str, str]:
    """Collect them, once. A second caller gets nothing, which is the point."""
    return _PENDING.pop(job_id, {})


def discard(job_id: str) -> None:
    """Drop values for a job that will never run - a creation that failed
    after the job row existed, or one cancelled before it was claimed."""
    _PENDING.pop(job_id, None)
