"""A backup stops NATS. Nothing may stop it from starting it again.

The JetStream backup takes the server down for the length of the copy, and the
restart is in a finally block for exactly that reason. A finally block is not
enough on its own: cancel the job - or restart the backend while the backup
runs - and the cancellation reaches the restart too, so the backbone stays
down with nobody left to bring it up. Every probe loses its connection until
somebody notices.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.config import Settings
from app.infrastructure.docker import StackContainer
from app.services.provisioning import ProvisioningService


class SlowDocker:
    """A stack whose volume copy takes long enough to be interrupted."""

    def __init__(self, *, copy_seconds: float = 5.0) -> None:
        self.available = True
        self.running = True
        self.stopped = 0
        self.started = 0
        self._copy_seconds = copy_seconds
        self.copy_started = asyncio.Event()
        self.start_entered = asyncio.Event()

    async def inspect(self, container: StackContainer) -> Any:
        running = self.running

        class _State:
            exists = True

        _State.running = running  # type: ignore[attr-defined]
        return _State()

    async def stop(self, container: StackContainer) -> None:
        self.stopped += 1
        self.running = False

    async def start(self, container: StackContainer) -> None:
        # The real one talks to the Docker daemon, so it suspends - which is
        # what makes it interruptible in the first place.
        self.start_entered.set()
        await asyncio.sleep(0.05)
        self.started += 1
        self.running = True

    async def wait_healthy(self, container: StackContainer) -> None:
        return None

    async def read_volume_archive(self, volume: str, target: Any) -> None:
        self.copy_started.set()
        await asyncio.sleep(self._copy_seconds)
        target.write(b"never gets here")


async def test_a_cancelled_backup_still_brings_nats_back(
    settings: Settings,
) -> None:
    """One cancellation, taken while the volume is being copied."""
    docker = SlowDocker()
    service = ProvisioningService(settings, docker)  # type: ignore[arg-type]

    backup = asyncio.ensure_future(service.backup_jetstream())
    await asyncio.wait_for(docker.copy_started.wait(), timeout=5)
    backup.cancel()

    with pytest.raises(asyncio.CancelledError):
        await backup

    assert docker.stopped == 1
    assert docker.started == 1
    assert docker.running is True


async def test_a_second_cancellation_does_not_reach_the_restart(
    settings: Settings,
) -> None:
    """The one that used to leave the backbone down.

    A shutdown that cancels twice - or any wait_for around the job - reaches
    the restart at its own suspension point, and the container stays stopped
    with nothing left to start it. Shielded, the restart finishes on its own
    even though the cancellation carries on immediately.
    """
    docker = SlowDocker()
    service = ProvisioningService(settings, docker)  # type: ignore[arg-type]

    backup = asyncio.ensure_future(service.backup_jetstream())
    await asyncio.wait_for(docker.copy_started.wait(), timeout=5)
    backup.cancel()
    await asyncio.wait_for(docker.start_entered.wait(), timeout=5)
    backup.cancel()

    with pytest.raises(asyncio.CancelledError):
        await backup

    # The shielded restart outlives the task it was started from.
    for _ in range(50):
        if docker.started:
            break
        await asyncio.sleep(0.02)

    assert docker.started == 1
    assert docker.running is True


async def test_a_backup_that_was_not_running_is_not_started_by_the_cleanup(
    settings: Settings,
) -> None:
    """A stack the operator had stopped stays stopped."""
    docker = SlowDocker(copy_seconds=0)
    docker.running = False
    service = ProvisioningService(settings, docker)  # type: ignore[arg-type]

    await service.backup_jetstream()

    assert docker.stopped == 0
    assert docker.started == 0


async def test_the_archive_of_a_cancelled_backup_is_not_left_behind(
    settings: Settings,
) -> None:
    """A half-written archive would look like a backup that can be restored."""
    docker = SlowDocker()
    service = ProvisioningService(settings, docker)  # type: ignore[arg-type]

    backup = asyncio.ensure_future(service.backup_jetstream())
    await asyncio.wait_for(docker.copy_started.wait(), timeout=5)
    backup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await backup

    assert list(settings.backup_dir.glob("*.tar.gz")) == []
