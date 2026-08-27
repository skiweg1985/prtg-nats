"""The updater methods, against a real daemon rather than a fake one.

Everything else about the update is tested with a stand-in for Docker, which
is right for the logic and useless for the part that actually worries me:
create_updater builds a container specification by hand - binds, working
directory, log driver, no labels - and hands it to somebody else's API. A fake
accepts any specification. The daemon is the only thing that can say whether
this one is valid, and the first time it says so should not be during a
customer's update.

Skipped without a socket, so it costs nothing where there is no Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.infrastructure.docker import (
    COMPOSE_PROJECT_LABEL,
    DockerAdapter,
    UpdaterCommand,
)

SOCKET = Path("/var/run/docker.sock")
# A stand-in for the parts that only need *a* container to exist. Pinned like
# everything else in the stack.
PROBE_IMAGE = "alpine:3.22"


def _image_present(reference: str) -> bool:
    """Whether an image is here, asked over the socket like everything else."""
    if not SOCKET.exists():
        return False
    try:
        import httpx

        transport = httpx.HTTPTransport(uds=str(SOCKET))
        with httpx.Client(transport=transport, base_url="http://localhost/v1.44") as c:
            return c.get(f"/images/{reference}/json", timeout=5.0).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not SOCKET.exists(), reason="no Docker socket; this checks the real API"
)


@pytest.fixture
def docker() -> DockerAdapter:
    return DockerAdapter(SOCKET)


async def test_the_daemon_accepts_the_container_we_describe(
    docker: DockerAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The specification create_updater builds is a valid one.

    Binds, working directory and log configuration together, as one request.
    Any of them malformed and the daemon refuses - which is the failure this
    exists to find here rather than halfway through an update.
    """
    import app.infrastructure.docker as docker_module

    monkeypatch.setattr(docker_module, "UPDATER_IMAGE", PROBE_IMAGE)
    project = docker_module.ComposeProject(
        name="prtg-nats", working_dir=tmp_path, config_file=None
    )

    container_id = await docker.create_updater(
        UpdaterCommand.PROBE,
        ("main",),
        project=project,
        name=f"prtg-nats-test-{tmp_path.name}",
    )
    try:
        assert container_id
    finally:
        await docker.remove_container(container_id)


async def test_the_updater_carries_no_compose_labels(
    docker: DockerAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the whole handover rests on.

    A project label would put this container in the list --remove-orphans
    collects, and the update would take down the thing driving it. Asserted
    against what the daemon actually stored, not against what we sent.
    """
    import app.infrastructure.docker as docker_module

    monkeypatch.setattr(docker_module, "UPDATER_IMAGE", PROBE_IMAGE)
    project = docker_module.ComposeProject(
        name="prtg-nats", working_dir=tmp_path, config_file=None
    )

    container_id = await docker.create_updater(
        UpdaterCommand.PROBE,
        ("main",),
        project=project,
        name=f"prtg-nats-test-labels-{tmp_path.name}",
    )
    try:
        labels = await docker._labels(container_id)
        assert COMPOSE_PROJECT_LABEL not in labels
    finally:
        await docker.remove_container(container_id)


@pytest.mark.skipif(
    not _image_present("prtg-nats-updater:current"),
    reason=(
        "the updater image is not built here; "
        "docker build -f web/updater/Dockerfile -t prtg-nats-updater:current ."
    ),
)
async def test_the_log_and_exit_code_outlive_the_container(
    docker: DockerAdapter, tmp_path: Path
) -> None:
    """What the recovery reads after the restart is still there to be read.

    A container created with AutoRemove would be gone the moment it ended,
    taking the only record of what the update did with it - and the recovery
    would have nothing to report but "the outcome is unknown". This is the
    check that the flag is off and that both halves survive the container.

    The real image, and the real command: probe against a directory that is
    not a checkout, which fails in the way it is meant to and writes a
    sentence about it.
    """
    import app.infrastructure.docker as docker_module

    project = docker_module.ComposeProject(
        name="prtg-nats", working_dir=tmp_path, config_file=None
    )

    container_id = await docker.create_updater(
        UpdaterCommand.PROBE,
        ("main",),
        project=project,
        name=f"prtg-nats-test-log-{tmp_path.name}",
    )
    try:
        await docker.start_container(container_id)
        exit_code = await docker.wait_container(container_id, timeout=60.0)

        assert exit_code != 0, "an empty directory is not a checkout"
        assert await docker.container_exit_code(container_id) == exit_code
        assert "not a git checkout" in await docker.container_logs(container_id)
    finally:
        await docker.remove_container(container_id)


async def test_the_checkout_is_found_through_the_compose_labels(
    docker: DockerAdapter,
) -> None:
    """compose_project() returns something or nothing, never nonsense.

    On a machine running the stack it finds the checkout; on a developer's
    laptop it finds no such container and says so. Both are correct answers,
    and the one thing that must not happen is a path that does not exist.
    """
    project = await docker.compose_project()

    if project is not None:
        assert project.working_dir.is_absolute()
        assert project.name == "prtg-nats"
