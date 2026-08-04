"""A reload NATS refused must not read as success.

NATS answers SIGHUP whatever it decides, and the Docker API answers 200 for
delivering the signal. If the new configuration is not reloadable - a changed
server name is the common case - the refusal goes to the container log and
nowhere else. Creating an account then looks like it worked, and the probe
that needs it gets "authorization violation" until somebody reads that log.

Found exactly that way, on a real installation, after the server name changed.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import NatsReloadRefusedError
from app.infrastructure.docker import StackContainer
from app.infrastructure.nats import NatsServerState
from app.services import provisioning as provisioning_module
from app.services.provisioning import ProvisioningService


class FakeDocker:
    """Delivers the signal and says so, which is all the real one knows."""

    def __init__(self) -> None:
        self.available = True
        self.reloads = 0

    async def inspect(self, container: StackContainer) -> Any:
        class _State:
            exists = True
            running = True

        return _State()

    async def reload_config(self, container: StackContainer) -> None:
        self.reloads += 1


@pytest.fixture(autouse=True)
def quick_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait for the timestamp, without the wait.

    The refusal is only decided after the full verification window, so a test
    for it otherwise spends that window sleeping.
    """
    monkeypatch.setattr(provisioning_module, "RELOAD_VERIFY_INTERVAL", 0.0)


@pytest.fixture
def patched_load_time(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Control what /varz reports for config_load_time."""
    values: list[str | None] = []

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def fetch_state(self) -> NatsServerState:
            value = values[0] if len(values) == 1 else values.pop(0)
            return NatsServerState(available=True, config_load_time=value)

    monkeypatch.setattr(provisioning_module, "NatsMonitoringClient", FakeClient)
    return values


@pytest.fixture
def silent_monitoring(monkeypatch: pytest.MonkeyPatch) -> None:
    """A monitoring endpoint that answers nothing at all."""

    class UnavailableClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def fetch_state(self) -> NatsServerState:
            return NatsServerState(available=False)

    monkeypatch.setattr(provisioning_module, "NatsMonitoringClient", UnavailableClient)


async def test_a_refused_reload_is_an_error_not_a_shrug(
    settings: Settings, patched_load_time: list[str | None]
) -> None:
    """The timestamp does not move, so the configuration was not applied."""
    patched_load_time.append("2026-08-03T07:10:37Z")  # never changes
    docker = FakeDocker()
    service = ProvisioningService(settings, docker)  # type: ignore[arg-type]

    with pytest.raises(NatsReloadRefusedError) as raised:
        await service._reload_server()

    assert docker.reloads == 1
    assert "restart" in (raised.value.details or "")


async def test_an_applied_reload_passes_quietly(
    settings: Settings, patched_load_time: list[str | None]
) -> None:
    patched_load_time.extend(["2026-08-03T07:10:37Z", "2026-08-03T07:48:32Z"])
    docker = FakeDocker()
    service = ProvisioningService(settings, docker)  # type: ignore[arg-type]

    await service._reload_server()

    assert docker.reloads == 1


async def test_without_docker_nothing_is_claimed(settings: Settings) -> None:
    """The files are already correct; the change lands on the next start."""

    class NoDocker:
        available = False

    service = ProvisioningService(settings, NoDocker())  # type: ignore[arg-type]
    await service._reload_server()  # does not raise


async def test_an_unverifiable_reload_is_not_reported_as_a_refusal(
    settings: Settings, silent_monitoring: None
) -> None:
    """Not knowing is not the same as knowing it failed.

    An installation whose monitoring port is unreachable answers nothing
    before the reload and nothing after, and the comparison can never show a
    change. Read as a refusal - which is what it did - every account created,
    rotated or deleted ended as an error while the files and the signal were
    both fine.
    """
    docker = FakeDocker()
    service = ProvisioningService(settings, docker)  # type: ignore[arg-type]

    await service._reload_server()  # does not raise

    assert docker.reloads == 1


async def test_a_reload_that_answers_late_still_counts(
    settings: Settings, patched_load_time: list[str | None]
) -> None:
    """The signal travels through the daemon; the first look can be early."""
    patched_load_time.extend(
        [
            "2026-08-03T07:10:37Z",  # before
            "2026-08-03T07:10:37Z",  # not applied yet
            "2026-08-03T07:10:37Z",
            "2026-08-03T07:48:32Z",  # applied
        ]
    )
    docker = FakeDocker()
    service = ProvisioningService(settings, docker)  # type: ignore[arg-type]

    await service._reload_server()

    assert docker.reloads == 1
