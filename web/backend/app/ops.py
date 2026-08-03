"""The recovery command line.

``python -m app.ops`` drives the same native services the web platform uses -
one implementation, two entry points. This exists for the situations the web
interface cannot cover: initialising a machine before the platform runs,
scripting in CI, and recovery when the platform itself is what broke.

It is deliberately small. Anything with a workflow - deployments, onboarding,
reconciliation - lives in the web interface; this handles the primitives.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings


def _fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def _settings() -> Settings:
    from app.core.config import get_settings

    return get_settings()


async def _cmd_init(_: argparse.Namespace) -> None:
    from app.infrastructure.docker import DockerAdapter
    from app.services.provisioning import ProvisioningService

    settings = _settings()
    provisioning = ProvisioningService(settings, DockerAdapter(settings.docker_socket))
    provisioning.initialise_runtime()
    print(f"Runtime initialised for {settings.runtime_dir}")
    print("Secrets were written to root-only files; nothing was printed.")


async def _cmd_verify(args: argparse.Namespace) -> None:
    from app.services.verification import StackVerification

    checks = await StackVerification(_settings()).run(live=not args.offline)
    failed = 0
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        print(f"  {mark}  {check.name:20} {check.detail}")
        failed += 0 if check.ok else 1
    if failed:
        _fail(f"{failed} check(s) failed")
    print("All checks passed.")


async def _cmd_renew_certificate(_: argparse.Namespace) -> None:
    from app.infrastructure.docker import DockerAdapter, StackContainer
    from app.services.provisioning import ProvisioningService

    settings = _settings()
    docker = DockerAdapter(settings.docker_socket)
    provisioning = ProvisioningService(settings, docker)
    provisioning.renew_server_certificate()
    provisioning.publish_public_material()
    print("Server certificate renewed; the previous pair is archived.")
    state = await docker.inspect(StackContainer.NATS) if docker.available else None
    if state is not None and state.running:
        await docker.restart(StackContainer.NATS)
        state = await docker.wait_healthy(StackContainer.NATS)
        print(f"NATS restarted, health: {state.health or 'unknown'}")
    else:
        print("NATS is not running here; it activates the pair on its next start.")


async def _cmd_backup(_: argparse.Namespace) -> None:
    from app.infrastructure.docker import DockerAdapter
    from app.services.provisioning import ProvisioningService

    settings = _settings()
    docker = DockerAdapter(settings.docker_socket)
    if not docker.available:
        _fail(
            "the Docker socket is not reachable; the backup reads the volume through it"
        )
    result = await ProvisioningService(settings, docker).backup_jetstream()
    print(f"JetStream backup created: {result.archive}")
    print(f"SHA-256: {result.sha256}")
    print("Copy it to protected backup storage; backups/ is excluded from Git.")


async def _cmd_user(args: argparse.Namespace) -> None:
    from app.infrastructure.docker import DockerAdapter
    from app.infrastructure.nats_runtime import NatsRuntime
    from app.services.provisioning import ProvisioningService

    settings = _settings()
    provisioning = ProvisioningService(settings, DockerAdapter(settings.docker_socket))
    nats = NatsRuntime(settings)

    if args.action == "list":
        for account in nats.list_accounts():
            role = "core/shared" if account.is_shared else "mpp"
            print(f"  {account.username:28} {role:12} {account.credential_path}")
        return

    if args.action == "add":
        await provisioning.create_account(args.username)
        print(f"NATS account {args.username} created.")
        print(f"Credential file: {settings.credential_dir / (args.username + '.env')}")
        return

    if args.action == "delete":
        await provisioning.delete_account(args.username)
        print(f"NATS account {args.username} deleted.")
        return

    if args.action == "show":
        password = nats.read_password(args.username)
        print(f"Username: {args.username}")
        print(f"Password: {password}")
        print("Treat the password as a secret; it was shown on explicit request.")
        return

    if args.action == "rotate":
        await _rotate(args.username, server_only=args.server_only)
        return


async def _rotate(username: str, *, server_only: bool) -> None:
    """Rotate server-side, then reconfigure the enrolled probe.

    The same sequence the web platform's rotation job runs; here it prints the
    probe helper's own answers instead of writing a job log.
    """
    from app.domain import probe_config
    from app.infrastructure.docker import DockerAdapter
    from app.infrastructure.nats_runtime import NatsRuntime
    from app.infrastructure.probe_helper import (
        ProbeConnection,
        ProbeHelperClient,
        SshHelperTransport,
    )
    from app.infrastructure.runtime_files import RuntimeFileStore
    from app.services.provisioning import ProvisioningService

    settings = _settings()
    runtime = RuntimeFileStore(settings)
    provisioning = ProvisioningService(settings, DockerAdapter(settings.docker_socket))

    await provisioning.rotate_account(username)
    print(f"Server-side credentials for {username} rotated.")

    if server_only or username not in runtime.list_probe_usernames():
        print("No enrolled probe to reconfigure; update the remaining client yourself.")
        return

    inventory = runtime.read_probe(username)
    site = runtime.site_settings()
    if not site.nats_fqdn:
        _fail("NATS_FQDN is not configured")
        raise AssertionError  # unreachable; _fail exits

    values = probe_config.ProbeConfigValues(
        probe_id=inventory.probe_id or probe_config.generate_probe_id(),
        access_key=runtime.read_access_key(username)
        or probe_config.default_access_key(
            inventory.probe_name or probe_config.default_probe_name(inventory.ssh_host)
        ),
        probe_name=inventory.probe_name
        or probe_config.default_probe_name(inventory.ssh_host),
        nats_host=site.nats_fqdn,
        nats_port=site.nats_port,
        nats_user=username,
        nats_password=NatsRuntime(settings).read_password(username),
    )
    rendered = probe_config.render_probe_config(
        settings.template_dir / "mpprobe-config.yaml.template", values
    )

    helper = ProbeHelperClient(
        SshHelperTransport(
            key_path=settings.ssh_key_path,
            known_hosts_path=settings.ssh_known_hosts_path,
            connect_timeout=settings.ssh_connect_timeout_seconds,
        ),
        default_timeout=settings.ssh_command_timeout_seconds,
    )
    connection = ProbeConnection(
        nats_username=username, host=inventory.ssh_host, port=inventory.ssh_port
    )
    transaction = probe_config.new_transaction_id()
    try:
        # write_config opens the transaction; the rendered configuration
        # already carries the new credentials.
        await helper.write_config(connection, transaction, rendered)
        response = await helper.activate(connection, transaction)
        print(response.raw.strip())
        response = await helper.commit(connection, transaction)
        print(response.raw.strip())
    except Exception:
        try:
            await helper.rollback(connection, transaction)
            print("The probe restored its previous configuration.", file=sys.stderr)
        except Exception:  # noqa: S110 - the original error is the story
            pass
        raise
    print(f"Probe {username} reconfigured with the new credentials.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="prtg-nats-ops",
        description="Recovery operations for the PRTG-NATS stack. "
        "Regular administration happens in the web interface.",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="installation directory (default: PRTG_NATS_WEB_PROJECT_DIR or CWD)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="initialise runtime/ on a fresh installation")

    verify = commands.add_parser("verify", help="check the stack")
    verify.add_argument("--offline", action="store_true", help="skip the live checks")

    commands.add_parser("renew-certificate", help="renew the server certificate")
    commands.add_parser("backup", help="create a consistent JetStream backup")

    user = commands.add_parser("user", help="manage NATS accounts")
    user_actions = user.add_subparsers(dest="action", required=True)
    user_actions.add_parser("list")
    for name in ("add", "delete", "show"):
        sub = user_actions.add_parser(name)
        sub.add_argument("username")
    rotate = user_actions.add_parser("rotate")
    rotate.add_argument("username")
    rotate.add_argument(
        "--server-only",
        action="store_true",
        help="rotate on the server without reconfiguring the probe",
    )

    args = parser.parse_args(argv)

    project_dir = args.project_dir or os.environ.get("PRTG_NATS_WEB_PROJECT_DIR")
    if project_dir:
        os.environ["PRTG_NATS_WEB_PROJECT_DIR"] = str(project_dir)
    else:
        os.environ["PRTG_NATS_WEB_PROJECT_DIR"] = str(Path.cwd())

    handlers = {
        "init": _cmd_init,
        "verify": _cmd_verify,
        "renew-certificate": _cmd_renew_certificate,
        "backup": _cmd_backup,
        "user": _cmd_user,
    }
    try:
        asyncio.run(handlers[args.command](args))
    except SystemExit:
        raise
    except ModuleNotFoundError as exc:
        # The subcommands import their dependencies lazily, so an interpreter
        # without them gets this far and would otherwise report a bare
        # "No module named httpx" - true, and useless to whoever ran setup.
        _fail(
            f"the backend dependencies are missing here ({exc.name}). "
            "These commands run in the prtg-nats-web-api container; "
            "start the stack, or install web/backend into this interpreter."
        )
    except Exception as exc:
        details = getattr(exc, "details", None)
        _fail(str(details or exc))


if __name__ == "__main__":
    main()
