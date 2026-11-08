"""Start the real backend with the demo's fakes in place, then seed it once.

Run by run.sh from web/backend, with the demo virtual environment. Everything
is configured through the environment run.sh sets; see the README beside this
file.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).resolve().parent
STATE = Path(os.environ["DEMO_STATE_DIR"]).resolve()
API_PORT = int(os.environ["DEMO_API_PORT"])
NATS_PORT = int(os.environ["DEMO_NATS_PORT"])
NATS_MONITOR_PORT = int(os.environ["DEMO_NATS_MONITOR_PORT"])
NATS_CONTAINER = os.environ["DEMO_NATS_CONTAINER"]

sys.path.insert(0, str(DEMO_DIR))
sys.path.insert(0, str(Path.cwd()))

import prepare  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
running_commit, running_version = prepare.prepare_state(STATE, REPO)
os.environ.setdefault("DEMO_RUNNING_COMMIT", running_commit)
os.environ.setdefault("DEMO_RUNNING_VERSION", running_version)

# The settings are read once, on first use, so they go into the environment
# before anything from the application is imported. "development" is what
# keeps the API docs on and production-only refusals off; the accounts are
# real ones, so no development banner appears.
os.environ.update(
    {
        "PRTG_NATS_WEB_ENVIRONMENT": "development",
        "PRTG_NATS_WEB_PROJECT_DIR": str(STATE / "project"),
        "PRTG_NATS_WEB_ASSET_DIR": str(STATE / "assets"),
        "PRTG_NATS_WEB_RUNTIME_DIR": str(STATE / "runtime"),
        "PRTG_NATS_WEB_SESSION_COOKIE_SECURE": "false",
        "PRTG_NATS_WEB_NATS_MONITORING_URL": f"http://127.0.0.1:{NATS_MONITOR_PORT}",
        # Never a real socket: every Docker call is answered by FakeDocker.
        "PRTG_NATS_WEB_DOCKER_SOCKET": str(STATE / "no-docker.sock"),
        "PRTG_NATS_WEB_INVENTORY_SYNC_INTERVAL_SECONDS": "30",
        "PRTG_NATS_WEB_UPDATE_CHECK_INTERVAL_SECONDS": "900",
        "PRTG_NATS_WEB_JOB_WORKER_COUNT": "4",
        "PRTG_NATS_WEB_UPDATE_BRANCH": "dev",
        "PRTG_NATS_WEB_GIT_COMMIT": os.environ.get("DEMO_RUNNING_COMMIT", ""),
        "PRTG_NATS_WEB_GIT_REF": "dev",
        "PRTG_NATS_WEB_GIT_VERSION": os.environ.get("DEMO_RUNNING_VERSION", ""),
    }
)

import fakes  # noqa: E402
import seed  # noqa: E402
import uvicorn  # noqa: E402
import world  # noqa: E402


def install_fakes() -> tuple[fakes.FakeWorld, fakes.FakeDocker]:
    """Put the fakes where the application looks for the real thing.

    Module attributes rather than product hooks: the backend has no switch
    for this, and the demo must not need one.
    """
    from app.infrastructure import iperf_helper
    from app.infrastructure import overlay as overlay_module
    from app.services import management_routes

    fake_world = fakes.FakeWorld(
        STATE / "fake-hosts.json", fakes.helper_digest(STATE / "assets")
    )
    fakes.FakeHelperTransport.world = fake_world
    management_routes.SshHelperTransport = fakes.FakeHelperTransport  # type: ignore[misc,assignment]
    iperf_helper.SshHelperTransport = fakes.FakeHelperTransport  # type: ignore[misc,assignment]

    from app.api.v1.routes import iperf as iperf_routes
    from app.workers.handlers import iperf_provisioning

    iperf_routes.scan_host_keys = fakes.scan_host_keys  # type: ignore[assignment]
    iperf_provisioning.install_access = fakes.install_access  # type: ignore[assignment]

    # The hub interface would exist on the host the stack runs on. Here it is
    # up exactly when the overlay is on, which is what a working hub shows.
    overlay_module.OverlayRuntime.interface_up = (  # type: ignore[method-assign]
        lambda self: self.settings().enabled
    )

    docker = fakes.FakeDocker(NATS_CONTAINER, STATE / "checkout", STATE / "runtime")
    docker.install()
    fakes.install_verification(NATS_PORT, STATE / "runtime")
    return fake_world, docker


def from_workstation(app: Any) -> Any:
    """Requests from the browser arrive from Vite on loopback. In production
    the proxy names the real client, and the audit trail shows it; here the
    interface's requests are given the administrator's documentation address
    instead of 127.0.0.1, so audit screenshots look like a real network."""

    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            headers = list(scope.get("headers") or [])
            if not any(name == b"x-forwarded-for" for name, _ in headers):
                headers.append((b"x-forwarded-for", world.ADMIN_SOURCE.encode()))
                scope = dict(scope, headers=headers)
        await app(scope, receive, send)

    return wrapped


async def heartbeats(base_url: str, fake_world: fakes.FakeWorld) -> None:
    """What the return channel service on each probe sends every few minutes.

    Through the probe channel route itself, with the identity header the
    proxy would set from the client certificate.
    """
    import httpx

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        while True:
            for spec in world.PROBES:
                probe = fake_world.state["probes"].get(spec.username) or {}
                if (
                    not probe.get("reachable")
                    or probe.get("channel_service") != "active"
                ):
                    continue
                with contextlib.suppress(httpx.HTTPError):
                    await client.post(
                        "/probe/v1/records",
                        json={
                            "records": [],
                            "buffer": {"bytes": 0, "limit": 20 * 1024 * 1024},
                        },
                        headers={"X-Probe-Identity": f"CN={spec.username}"},
                    )
            await asyncio.sleep(120)


async def main() -> None:
    fake_world, docker = install_fakes()

    from app.api.v1.routes.prtg import get_prtg_transport
    from app.main import app

    app.dependency_overrides[get_prtg_transport] = fakes.prtg_transport

    config = uvicorn.Config(
        from_workstation(app),
        host="127.0.0.1",
        port=API_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    while not server.started:
        if serving.done():
            await serving
            return
        await asyncio.sleep(0.1)

    base_url = f"http://127.0.0.1:{API_PORT}"
    background = [
        asyncio.create_task(heartbeats(base_url, fake_world)),
        asyncio.create_task(
            fakes.NatsClients(
                STATE / "runtime", "127.0.0.1", NATS_PORT, fake_world
            ).run_forever()
        ),
    ]
    marker = STATE / "seeded"
    try:
        if not marker.exists():
            await seed.seed(base_url, STATE, fake_world, docker)
            marker.write_text(uuid.uuid4().hex + "\n", encoding="utf-8")
            logging.getLogger("demo").warning("demo seeded")
        await seed.after_start(base_url, STATE, fake_world, docker)
    except Exception:
        logging.getLogger("demo").exception("seeding failed")
    (STATE / "ready").write_text("ready\n", encoding="utf-8")
    await serving
    for task in background:
        task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
