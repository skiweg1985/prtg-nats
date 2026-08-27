"""Running one probe action over one probe, or over a selection of them.

The single-probe jobs came first and stay the common case: somebody on a detail
page presses a button, and if it fails, the job failed - with a cause and a
recommended action the interface can show. That case is left exactly as it was.

A selection of twelve probes is the other case, and there the same failure must
not throw the other eleven away. So the fan-out catches per probe, records an
outcome for each, and hands the runner the ``succeeded``/``failed`` shape it
already grades a sensor rollout by.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.errors import AppError
from app.domain.enums import LogLevel
from app.workers.context import JobContext

# What one probe action does to one probe. Every handler in this package that
# can be asked for a selection is written as one of these.
ProbeAction = Callable[[JobContext, str], Awaitable[dict[str, Any]]]


def targets(context: JobContext) -> list[str]:
    """The probes this job is for.

    ``probes`` when a selection asked for it, ``probe`` when a detail page did.
    Both shapes stay valid so a job queued before this existed still runs.
    """
    selection = context.payload.get("probes")
    if selection:
        return [str(entry) for entry in selection]
    return [str(context.payload["probe"])]


async def over_targets(context: JobContext, action: ProbeAction) -> dict[str, Any]:
    """Apply ``action`` to every probe the job declared."""
    names = targets(context)
    if len(names) == 1:
        # One probe, one job: let the error out. Wrapped in a result it would
        # become "0 of 1 succeeded" on the job page, and the reason - which is
        # the only thing the operator came for - would be a line in the log.
        return await action(context, names[0])

    await context.log("jobs.probe.fanout_started", params={"probes": len(names)})

    succeeded: list[str] = []
    failed: list[dict[str, str]] = []
    for username in names:
        if context.cancelled:
            await context.log("jobs.cancelled", level=LogLevel.WARNING)
            break
        try:
            await action(context, username)
        except AppError as error:
            await context.log(
                "jobs.probe.action_failed",
                level=LogLevel.ERROR,
                params={"probe": username, "reason": error.code},
                target=username,
                raw=error.details,
            )
            failed.append(
                {
                    "probe": username,
                    "code": error.code,
                    "details": error.details or "",
                }
            )
        except Exception as exc:
            await context.log(
                "jobs.probe.action_failed",
                level=LogLevel.ERROR,
                params={"probe": username, "reason": "internal.unexpected"},
                target=username,
                raw=f"{type(exc).__name__}: {exc}",
            )
            failed.append(
                {
                    "probe": username,
                    "code": "internal.unexpected",
                    "details": str(exc),
                }
            )
        else:
            succeeded.append(username)

    await context.log(
        "jobs.probe.fanout_finished",
        params={"succeeded": len(succeeded), "failed": len(failed)},
    )
    return {"probes": names, "succeeded": succeeded, "failed": failed}
