"""Update this installation from its own checkout.

The one job that does not survive to report its own result. `docker compose
up` replaces prtg-nats-web-api, so this handler runs until the handover and
then stops existing; workers/stack_recovery.py finishes the job on the way
back up.

The step list is built around the one boundary that matters:

    preflight -> backup -> fetch -> checkout -> build | recreate -> settle
                                                      ^
                                       everything left of this can be undone

Up to and including ``build`` nothing has been replaced, so a failure puts the
checkout back and leaves the running stack alone. ``recreate`` migrates the
database and swaps the containers, and after it there is no way back that this
code could take: an older image against a newer schema does not start at all -
Alembic cannot find the revision the database names, and the container ends up
in a restart loop. That is why the backup happens first and why the failure
message names it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import StackUpdateBlockedError, StackUpdateUnavailableError
from app.domain.enums import LogLevel
from app.infrastructure.docker import UpdaterCommand
from app.persistence.models.updates import StackUpdate
from app.services.provisioning import ProvisioningService
from app.services.stack_update import StackUpdateService
from app.workers.context import JobContext

JOB_TYPE = "stack.update"
STEPS: tuple[str, ...] = (
    "preflight",
    "backup",
    "fetch",
    "checkout",
    "build",
    "recreate",
    "settle",
)

# How long to follow the updater's log before giving up on it. Generous: a
# cold build pulls base images and compiles the frontend.
_BUILD_TIMEOUT_SECONDS = 40 * 60
_POLL_SECONDS = 2.0

# What the updater prefixes a phase announcement with. A contract between the
# two rather than the caller recognising status wording, which would break the
# progress display the first time somebody improved a sentence.
_PHASE_MARKER = "::phase"


async def run(context: JobContext) -> dict[str, Any]:
    service = StackUpdateService(context.settings, context.docker)

    await context.step("preflight")
    readiness = await service.readiness()
    if not readiness.available:
        raise StackUpdateUnavailableError(params={"reason": readiness.reason or ""})

    project = await service.project()
    if project is None:
        raise StackUpdateUnavailableError(params={"reason": "checkout_not_found"})

    await context.log(
        "jobs.stack.checkout_found", params={"path": str(project.working_dir)}
    )

    probe = await service.probe()
    if not probe.reachable:
        raise StackUpdateBlockedError(
            params={"reason": "unreachable"}, details=probe.error
        )
    if probe.dirty:
        # Never over uncommitted work. The update would either refuse halfway
        # through or take somebody's changes with it, and both are worse than
        # saying so now.
        raise StackUpdateBlockedError(params={"reason": "checkout_dirty"})
    if not probe.remote_head:
        raise StackUpdateBlockedError(params={"reason": "branch_missing"})
    if probe.remote_head == probe.head:
        raise StackUpdateBlockedError(params={"reason": "already_current"})

    target = probe.remote_head
    previous = probe.head
    await context.log(
        "jobs.stack.target",
        params={"from": previous[:12], "to": target[:12], "branch": probe.branch},
    )

    # The backup is the only thing standing between a failed migration and a
    # rebuild from scratch, so it happens before anything moves.
    await context.step("backup")
    provisioning = ProvisioningService(context.settings, context.docker)
    export = await asyncio.to_thread(provisioning.export_runtime)
    await context.log(
        "jobs.stack.backup_taken",
        params={"archive": export.archive, "sha256": export.sha256},
    )

    await context.step("fetch")
    await context.log("jobs.stack.handover")

    # From here the updater drives. One container for the whole sequence: it
    # has to keep going after this process is gone, and a container per step
    # would stop at the step that kills its caller.
    container_id = await context.docker.create_updater(
        UpdaterCommand.APPLY,
        (probe.branch, target, previous),
        project=project,
        name=f"prtg-nats-updater-{context.job.id}",
    )

    record = StackUpdate(
        job_id=context.job.id,
        container_id=container_id,
        commit_from=previous,
        commit_to=target,
        branch=probe.branch,
        checkout_dir=str(project.working_dir),
    )
    context.db.add(record)
    # Committed before the container starts, not after. The window between
    # starting the updater and recording it is the one moment in which a crash
    # would leave a container nobody knows about, rebuilding an installation
    # with no job left to report it.
    await context.db.flush()
    await context.db.commit()

    await context.docker.start_container(container_id)
    await context.step("checkout")

    # Follow along for as long as this process exists. The build is the long
    # part and it runs while the API is still up, so most of the update is
    # watched live; the rest is filled in by the recovery.
    await _follow(context, record)

    # Only reached if the updater finished before it got to replacing this
    # container - a failed build, most likely. The recovery handles the other
    # way out, which is this process not being here any more.
    exit_code = await context.docker.container_exit_code(container_id)
    if exit_code == 0:
        return {"commit": target, "branch": probe.branch}

    record.settled = True
    await context.db.flush()
    reason = "build_failed" if exit_code == 2 else "updater_failed"
    await context.log(
        "jobs.stack.rolled_back" if exit_code == 2 else "jobs.stack.update_failed",
        level=LogLevel.ERROR,
        params={"code": str(exit_code), "commit": previous[:12]},
    )
    raise StackUpdateBlockedError(
        params={"reason": reason},
        details=f"the updater exited with code {exit_code}",
    )


async def _follow(context: JobContext, record: StackUpdate) -> None:
    """Copy the updater's output into the job log until it stops or we do.

    "Or we do" is the normal ending. The updater recreates the stack, this
    container goes away mid-sentence, and the remaining lines are picked up
    from the same container after the restart - which is why the cursor is
    written down as it goes rather than at the end.
    """
    waited = 0.0
    seen = 0
    while waited < _BUILD_TIMEOUT_SECONDS:
        code = await context.docker.container_exit_code(record.container_id)
        output = await context.docker.container_logs(record.container_id)
        lines = output.splitlines()
        if len(lines) > seen:
            await _report(context, lines[seen:])
            seen = len(lines)
            record.log_cursor = seen
            await context.db.flush()
        if code is not None:
            return
        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS

    await context.log("jobs.stack.timeout", level=LogLevel.WARNING)


async def _report(context: JobContext, lines: list[str]) -> None:
    """Turn a batch of updater output into steps and one readable line.

    Two things are wrong with simply appending the batch. The steps stop
    advancing at the handover, so every line from the build onwards is filed
    under whatever step was current when this process stopped calling step() -
    a log that says the build happened while "moving the checkout" was in
    progress. And a batch logged under one fixed message reads as eight
    identical rows saying "output from the updater", with the only thing worth
    reading folded away behind a disclosure control.

    So the phase markers drive the step list, and the last real line of the
    batch becomes the visible text. The whole batch stays as the technical
    detail, which is what an operator opens when the summary is not enough.
    """
    visible: list[str] = []
    for line in lines:
        marker = line.strip()
        if marker.startswith(_PHASE_MARKER):
            phase = marker[len(_PHASE_MARKER) :].strip()
            # Only steps this job declared. The updater and the step list are
            # edited in different files, and a typo in one should not put the
            # job into a step nobody can render.
            if phase in STEPS:
                await context.step(phase)
            continue
        visible.append(line)

    body = "\n".join(visible).strip()
    if not body:
        return

    # The last line that says something, as the summary. Build output ends on
    # whatever the tool last printed, and that is the most recent news.
    headline = next((line.strip() for line in reversed(visible) if line.strip()), "")
    await context.log(
        "jobs.stack.updater_output",
        params={"line": headline[:200]},
        raw=body[-8000:],
    )
