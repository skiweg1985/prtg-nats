"""Overlay jobs: putting a probe on the tunnel, changing its mode, taking it off.

Jobs rather than synchronous endpoints for the reason everything that touches
a probe is one: the exchange can hang on a host that stopped answering, and
"who moved berlin-01 to tunnel-only, and when" is a question the audit trail
should be able to answer.

The work itself is in OverlayService. These handlers add the steps, the log
and the fan-out, so the command line and the interface do the same thing.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.domain.enums import LogLevel
from app.services.overlay import OverlayService
from app.workers.context import JobContext
from app.workers.handlers.fanout import over_targets

ATTACH_STEPS: tuple[str, ...] = ("check_helper", "configure_probe", "record_peer")
ATTACH_JOB_TYPE = "overlay.attach"

MODE_STEPS: tuple[str, ...] = ("configure_probe", "record_peer")
MODE_JOB_TYPE = "overlay.mode"

DETACH_STEPS: tuple[str, ...] = ("restore_access", "remove_tunnel", "record_peer")
DETACH_JOB_TYPE = "overlay.detach"

REFRESH_STEPS: tuple[str, ...] = ("collect_state",)
REFRESH_JOB_TYPE = "overlay.refresh"


def _service(context: JobContext) -> OverlayService:
    return OverlayService(context.settings, context.helper)


async def attach(context: JobContext) -> dict[str, Any]:
    return await over_targets(context, attach_one)


async def attach_one(context: JobContext, username: str) -> dict[str, Any]:
    service = _service(context)
    mode = str(context.payload.get("mode") or "") or None

    await context.step("check_helper")
    await context.step("configure_probe")
    state = await service.attach(username, mode)

    await context.step("record_peer")
    await context.log(
        "jobs.overlay.attached",
        params={
            "probe": username,
            "address": state.address or "-",
            "mode": state.mode,
        },
        target=username,
    )
    await _warn_if_degraded(context, state.mode, state.summary, username)
    return {"probe": username, "address": state.address, "mode": state.mode}


async def set_mode(context: JobContext) -> dict[str, Any]:
    return await over_targets(context, set_mode_on)


async def set_mode_on(context: JobContext, username: str) -> dict[str, Any]:
    service = _service(context)
    mode = str(context.payload.get("mode") or "auto")
    force = bool(context.payload.get("force"))

    await context.step("configure_probe")
    state = await service.set_mode(username, mode, force=force)

    await context.step("record_peer")
    await context.log(
        "jobs.overlay.mode_changed",
        params={"probe": username, "mode": state.mode, "path": state.summary},
        target=username,
    )
    await _warn_if_degraded(context, state.mode, state.summary, username)
    return {"probe": username, "mode": state.mode, "path": state.summary}


async def detach(context: JobContext) -> dict[str, Any]:
    return await over_targets(context, detach_one)


async def detach_one(context: JobContext, username: str) -> dict[str, Any]:
    service = _service(context)
    force = bool(context.payload.get("force"))

    await context.step("restore_access")
    await context.step("remove_tunnel")
    await service.detach(username, force=force)

    await context.step("record_peer")
    await context.log(
        "jobs.overlay.detached", params={"probe": username}, target=username
    )
    return {"probe": username}


async def refresh(context: JobContext) -> dict[str, Any]:
    return await over_targets(context, refresh_one)


async def refresh_one(context: JobContext, username: str) -> dict[str, Any]:
    service = _service(context)

    await context.step("collect_state")
    try:
        state = await service.refresh(username)
    except AppError as error:
        await context.log(
            "jobs.overlay.unreachable",
            level=LogLevel.WARNING,
            params={"probe": username, "reason": error.code},
            target=username,
            raw=error.details,
        )
        raise

    await context.log(
        "jobs.overlay.state",
        params={"probe": username, "mode": state.mode, "path": state.summary},
        target=username,
    )
    await _warn_if_degraded(context, state.mode, state.summary, username)
    return {"probe": username, "mode": state.mode, "path": state.summary}


async def _warn_if_degraded(
    context: JobContext, mode: str, summary: str, username: str = ""
) -> None:
    """Say so when a mode and what the probe is doing have come apart.

    A probe in "on" without a handshake reaches NATS through nothing at all,
    and one in "auto" that is on the tunnel means somebody's ordinary path is
    down - both look healthy in a list of green rows, which is exactly why
    they are worth a line.
    """
    if mode == "on" and summary in {"down", "no_handshake"}:
        await context.log(
            "jobs.overlay.tunnel_only_without_tunnel",
            level=LogLevel.WARNING,
            params={"probe": username},
            target=username or None,
        )
    elif mode == "auto" and summary == "tunnel":
        await context.log(
            "jobs.overlay.on_the_fallback_path",
            level=LogLevel.WARNING,
            params={"probe": username},
            target=username or None,
        )
