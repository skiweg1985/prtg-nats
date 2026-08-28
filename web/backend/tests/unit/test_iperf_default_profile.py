"""The profile called "default", and which endpoints reach a probe at all.

Two rules that only show themselves on the second endpoint, which is why both
went unnoticed while every installation had exactly one.

*"default" is an alias, not a copy.* It exists so a PRTG sensor object needs no
``--profile`` while there is nothing to tell apart. Written once and never
looked at again, it survives the endpoint it was made from: a second endpoint
leaves it standing for nothing in particular, a rotation leaves it holding a
password the far end no longer accepts, and a removal leaves it naming a host
that is gone. In each of those the sensor keeps reporting - against the wrong
thing, or against nothing.

*A rollout deploys what a probe is assigned.* Deploying the whole registry
instead would undo a revoke, and a revoke is the one operation whose entire
point is that a probe stops holding credentials. Only the first rollout to a
probe seeds that assignment, because that is what makes the promise "whoever
deploys the sensor deploys the credentials with it" hold for a new probe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.infrastructure.runtime_files import RuntimeFileStore
from app.workers.handlers.deploy_sensor import (
    DEFAULT_PROFILE,
    _deploy_endpoint_profiles,
    default_endpoint,
)

PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBg\n-----END PUBLIC KEY-----\n"
PROBE = "mpp-berlin"


@dataclass
class RecordingHelper:
    """The probe side, reduced to what these rules touch."""

    written: list[tuple[str, str, str]] = field(default_factory=list)
    removed: list[tuple[str, str]] = field(default_factory=list)

    async def write_profile(
        self, connection: Any, sensor: str, profile: str, content: str
    ) -> None:
        self.written.append((sensor, profile, content))

    async def remove_profile(self, connection: Any, sensor: str, profile: str) -> None:
        self.removed.append((sensor, profile))

    def profiles_written(self) -> list[str]:
        return [profile for _, profile, _ in self.written]

    def content_of(self, profile: str) -> str:
        return next(body for _, name, body in self.written if name == profile)


@dataclass
class Context:
    """Just enough JobContext for the endpoint half of a rollout."""

    runtime: RuntimeFileStore
    helper: RecordingHelper
    logs: list[dict[str, Any]] = field(default_factory=list)

    async def log(self, code: str, **kwargs: Any) -> None:
        self.logs.append({"code": code, **kwargs})

    async def step(self, name: str) -> None:
        return None

    def codes(self) -> list[str]:
        return [entry["code"] for entry in self.logs]


@dataclass
class Definition:
    """A sensor that measures against a managed endpoint."""

    name: str = "iperf-throughput"
    iperf_kind: str | None = "iperf3"


def write_endpoint(project_dir: Path, name: str) -> None:
    directory = project_dir / "runtime" / "iperf"
    (directory / f"{name}.env").write_text(
        f"IPERF_NAME={name}\n"
        f"IPERF_HOST={name}.example.test\n"
        "IPERF_PORT=5201\n"
        "IPERF_USERNAME=prtg-probe\n"
        f"IPERF_PASSWORD=secret-of-{name}\n"
        "IPERF_MANAGED=true\n"
        "IPERF_SSH_PORT=22\n",
        encoding="utf-8",
    )
    (directory / f"{name}.pem").write_text(PUBLIC_KEY, encoding="utf-8")


def assign(project_dir: Path, *endpoints: str) -> None:
    path = project_dir / "runtime" / "probes" / f"{PROBE}.iperf"
    if not endpoints:
        path.unlink(missing_ok=True)
        return
    path.write_text("\n".join(endpoints) + "\n", encoding="utf-8")


def build(settings: Settings) -> tuple[Context, RecordingHelper]:
    helper = RecordingHelper()
    return Context(runtime=RuntimeFileStore(settings), helper=helper), helper


async def deploy(context: Context, *, seed: bool) -> None:
    await _deploy_endpoint_profiles(
        context,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        Definition(),  # type: ignore[arg-type]
        PROBE,
        seed=seed,
    )


# --- The alias ---------------------------------------------------------------


async def test_one_endpoint_is_also_reachable_as_default(
    settings: Settings, project_dir: Path
) -> None:
    """The whole point of the alias: no --profile while nothing competes."""
    write_endpoint(project_dir, "berlin")
    context, helper = build(settings)

    await deploy(context, seed=True)

    assert helper.profiles_written() == ["berlin", DEFAULT_PROFILE]
    assert helper.content_of(DEFAULT_PROFILE) == helper.content_of("berlin")


async def test_a_second_endpoint_takes_the_alias_away(
    settings: Settings, project_dir: Path
) -> None:
    """Not "leave the old one" - it would keep pointing at berlin for good.

    That is the copy this whole file is about: a sensor object without
    --profile would go on measuring against berlin while the operator who
    added hamburg has no way of seeing why.
    """
    write_endpoint(project_dir, "berlin")
    write_endpoint(project_dir, "hamburg")
    context, helper = build(settings)

    await deploy(context, seed=True)

    assert set(helper.profiles_written()) == {"berlin", "hamburg"}
    assert helper.removed == [("iperf-throughput", DEFAULT_PROFILE)]
    assert "jobs.sensor.default_cleared" in context.codes()


async def test_the_alias_returns_when_one_endpoint_is_left_alone(
    settings: Settings, project_dir: Path
) -> None:
    """The rule holds in both directions, or it is not a rule."""
    write_endpoint(project_dir, "berlin")
    assign(project_dir, "berlin")
    context, helper = build(settings)

    await deploy(context, seed=False)

    assert DEFAULT_PROFILE in helper.profiles_written()
    assert helper.removed == []


async def test_the_last_endpoint_gone_takes_the_alias_with_it(
    settings: Settings, project_dir: Path
) -> None:
    """Nothing to deploy is still something to clean up.

    Without this the alias names a host that no longer answers, and the sensor
    reports a failed measurement rather than a missing endpoint.
    """
    context, helper = build(settings)

    await deploy(context, seed=True)

    assert helper.written == []
    assert helper.removed == [("iperf-throughput", DEFAULT_PROFILE)]
    assert "jobs.sensor.no_endpoints" in context.codes()


async def test_default_endpoint_names_the_one_this_probe_holds(
    settings: Settings, project_dir: Path
) -> None:
    """Registered is not the same as held, and the alias follows the second.

    A probe that was revoked has no default even while the installation still
    runs that endpoint for everybody else.
    """
    runtime = RuntimeFileStore(settings)
    write_endpoint(project_dir, "berlin")
    write_endpoint(project_dir, "hamburg")

    assign(project_dir)
    assert default_endpoint(runtime, PROBE) is None

    assign(project_dir, "berlin")
    assert default_endpoint(runtime, PROBE) == "berlin"

    assign(project_dir, "berlin", "hamburg")
    assert default_endpoint(runtime, PROBE) is None


# --- Which endpoints reach the probe -----------------------------------------


async def test_the_first_rollout_seeds_every_registered_endpoint(
    settings: Settings, project_dir: Path
) -> None:
    """A new probe holds nothing, and "deploy the sensor" has to leave it able
    to measure - otherwise it only reports credentials-unreadable."""
    write_endpoint(project_dir, "berlin")
    write_endpoint(project_dir, "hamburg")
    context, helper = build(settings)

    await deploy(context, seed=True)

    assert set(helper.profiles_written()) == {"berlin", "hamburg"}
    assert set(RuntimeFileStore(settings).assigned_iperf(PROBE)) == {
        "berlin",
        "hamburg",
    }


async def test_a_later_rollout_deploys_only_what_the_probe_is_assigned(
    settings: Settings, project_dir: Path
) -> None:
    """Otherwise every rollout of the sensor would undo the last revoke.

    berlin exists and this probe does not get it. It still gets an alias: two
    endpoints in the installation, but only one on this probe, and "default"
    is about what a sensor object here has to distinguish between.
    """
    write_endpoint(project_dir, "berlin")
    write_endpoint(project_dir, "hamburg")
    assign(project_dir, "hamburg")
    context, helper = build(settings)

    await deploy(context, seed=False)

    assert helper.profiles_written() == ["hamburg", DEFAULT_PROFILE]
    assert helper.content_of(DEFAULT_PROFILE) == helper.content_of("hamburg")


async def test_a_revoked_endpoint_does_not_come_back_with_the_next_rollout(
    settings: Settings, project_dir: Path
) -> None:
    """The sharp case: one endpoint, revoked from one probe.

    The assignment file is deleted with its last entry, so "no assignment" and
    "never touched" look the same on disk - which is why the caller says which
    of the two it is instead of the file being asked.
    """
    write_endpoint(project_dir, "berlin")
    assign(project_dir)
    context, helper = build(settings)

    await deploy(context, seed=False)

    assert helper.written == []
    assert RuntimeFileStore(settings).assigned_iperf(PROBE) == ()


async def test_an_assignment_that_outlived_its_endpoint_is_skipped(
    settings: Settings, project_dir: Path
) -> None:
    """Somebody forgot an endpoint while this probe was unreachable."""
    write_endpoint(project_dir, "berlin")
    assign(project_dir, "berlin", "hamburg")
    context, helper = build(settings)

    await deploy(context, seed=False)

    assert helper.profiles_written() == ["berlin", DEFAULT_PROFILE]
