"""Where this installation stands, and whether it can move.

Three versions have to be told apart, and the whole feature rests on not
confusing them:

* **running** - the commit the image was built from, stamped in at build time.
  A container has no checkout to ask.
* **checkout** - what the working tree on the host is at. Somebody may have
  pulled without rebuilding, and then this is ahead of what is running.
* **remote** - what the branch has at its tip.

Running behind checkout is a state of its own, and a common one: it is what a
`git pull` without a rebuild leaves behind. Naming it is more useful than
folding it into "up to date", which is what a single version number would have
done.

Everything here that touches git goes through the updater container. This
process cannot do it itself - it mounts the runtime volume and the Docker
socket, and the checkout is on the host.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.infrastructure.docker import (
    UPDATER_IMAGE,
    ComposeProject,
    DockerAdapter,
    UpdaterCommand,
)
from app.persistence.models.updates import StackVersion

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What the updater reported about checkout and branch."""

    branch: str
    head: str
    dirty: bool
    remote_head: str
    reachable: bool
    error: str
    commits: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class Readiness:
    """Whether this installation can update itself at all, and why not."""

    available: bool
    reason: str | None = None


class StackUpdateService:
    def __init__(self, settings: Settings, docker: DockerAdapter) -> None:
        self._settings = settings
        self._docker = docker

    # --- Can this installation do it at all? --------------------------------

    async def readiness(self) -> Readiness:
        """The structural preconditions, each with the name of what is missing.

        Reported rather than raised: the interface asks this to decide whether
        to offer the page, and "no" is a normal answer, not an error.
        """
        if not self._docker.available:
            return Readiness(False, "docker_socket_missing")
        project = await self._docker.compose_project()
        if project is None:
            return Readiness(False, "checkout_not_found")
        if not await self._docker.image_exists(UPDATER_IMAGE):
            return Readiness(False, "updater_image_missing")
        return Readiness(True)

    async def project(self) -> ComposeProject | None:
        return await self._docker.compose_project()

    # --- Asking the updater -------------------------------------------------

    async def probe(self, *, branch: str | None = None) -> ProbeResult:
        """Run the read-only half of the updater and parse what it says."""
        project = await self._docker.compose_project()
        if project is None:
            raise RuntimeError("the checkout could not be located")

        # No branch configured and none asked for: let the updater use the one
        # the checkout is on. Passing an empty argument instead would have it
        # look up a branch called "", which fails in a way that reads like the
        # repository is unreachable.
        target = branch or self._settings.update_branch
        arguments = (target,) if target else ()
        run = await self._docker.run_updater(
            UpdaterCommand.PROBE,
            arguments,
            project=project,
            # Unique per run: two checks must never collide over a name, and a
            # leftover from a crashed run must not block the next one.
            name=f"prtg-nats-updater-probe-{int(datetime.now(UTC).timestamp())}",
        )
        return _parse_probe(run.output, fallback_branch=target or "HEAD")

    # --- The cached answer the interface reads ------------------------------

    async def record(self, db: AsyncSession, result: ProbeResult) -> StackVersion:
        """Store the latest look. One row, replaced rather than appended."""
        row = await db.scalar(select(StackVersion).limit(1))
        if row is None:
            row = StackVersion()
            db.add(row)
        row.branch = result.branch
        row.checkout_commit = result.head
        row.checkout_dirty = result.dirty
        row.remote_commit = result.remote_head
        row.reachable = result.reachable
        row.error = result.error
        row.commits = result.commits
        row.checked_at = datetime.now(UTC)
        await db.flush()
        return row

    async def cached(self, db: AsyncSession) -> StackVersion | None:
        row: StackVersion | None = await db.scalar(select(StackVersion).limit(1))
        return row

    # --- What the page shows ------------------------------------------------

    def running_commit(self) -> str:
        return self._settings.git_commit

    @staticmethod
    def state(*, running: str, checkout: str, remote: str, reachable: bool) -> str:
        """One word for the situation, decided in one place.

        The order of these tests is the point, and the first version had it
        wrong in a way that only showed on a real installation: it answered
        "unknown" whenever the image carried no version stamp, and stopped
        there. An operator whose branch had moved on was told the version was
        unknown and offered nothing - the one thing they could have acted on
        was the thing being withheld.

        A missing stamp makes exactly one statement uncertain, "the running
        code is behind the checkout". It says nothing about whether the branch
        has moved, which is a comparison between two things that are both
        known. So that question is asked first now, and the stamp only decides
        how confidently the rest can be described.
        """
        # A repository that did not answer, before any comparison against it -
        # otherwise a broken deploy key reads as "up to date".
        if not reachable:
            return "unreachable"
        # Something new on the branch. Needs no stamp: both sides of this
        # comparison come from the checkout and the remote. Ahead of the
        # rebuild case on purpose - an update does the rebuild too, so
        # offering it is the more useful answer when both are true.
        if remote and checkout and remote != checkout:
            return "update_available"
        # A checkout ahead of the running image: pulled, not rebuilt.
        if running and checkout and checkout != running:
            return "rebuild_pending"
        # Level with the branch, but what is running cannot be established.
        if not running:
            return "unknown"
        return "current"


def _parse_probe(output: str, *, fallback_branch: str) -> ProbeResult:
    """The updater writes one JSON object to stdout and its noise to stderr.

    Both arrive here through the container log, so the object is found rather
    than assumed to be the whole text - a warning about an unpinned host key
    would otherwise make the whole reading unparseable.
    """
    payload: dict[str, Any] | None = None
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        break

    if payload is None:
        return ProbeResult(
            branch=fallback_branch,
            head="",
            dirty=False,
            remote_head="",
            reachable=False,
            error=output.strip()[-500:] or "the updater reported nothing",
            commits=[],
        )

    commits = payload.get("commits")
    return ProbeResult(
        branch=str(payload.get("branch") or fallback_branch),
        head=str(payload.get("head") or ""),
        dirty=bool(payload.get("dirty")),
        remote_head=str(payload.get("remote_head") or ""),
        reachable=bool(payload.get("reachable")),
        error=str(payload.get("error") or ""),
        commits=commits if isinstance(commits, list) else [],
    )
