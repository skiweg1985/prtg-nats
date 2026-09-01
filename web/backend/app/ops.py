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
    from app.infrastructure.probe_helper import ProbeHelperClient


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


async def _cmd_export_tool(args: argparse.Namespace) -> None:
    """Emit a signed tool envelope for the legacy shell deployment path."""
    from app.core.errors import RuntimeStateError
    from app.infrastructure.helper_signing import HelperSigner
    from app.infrastructure.tool_catalog import ToolCatalog, build_tool_envelope

    settings = _settings()
    artifact = ToolCatalog(settings.tool_source_dir).select(args.tool, args.platform)
    if artifact.version != args.expected_version:
        raise RuntimeStateError(
            params={"path": str(artifact.path)},
            details=(
                f"the release image contains {artifact.name} {artifact.version}, "
                f"not the requested {args.expected_version}"
            ),
        )
    envelope = build_tool_envelope(artifact)
    signature = HelperSigner(settings).sign(envelope.encode("utf-8"))
    # First line is the protocol argument; every following byte is the exact
    # signed payload. Neither is a secret.
    sys.stdout.write(f"{signature}\n{envelope}")


async def _cmd_check_tool(args: argparse.Namespace) -> None:
    """Verify one release artifact without signing or changing runtime state."""
    from app.core.errors import RuntimeStateError
    from app.infrastructure.tool_catalog import ToolCatalog

    artifact = ToolCatalog(_settings().tool_source_dir).select(args.tool, args.platform)
    if artifact.version != args.expected_version:
        raise RuntimeStateError(
            params={"path": str(artifact.path)},
            details=(
                f"the release image contains {artifact.name} {artifact.version}, "
                f"not the requested {args.expected_version}"
            ),
        )
    artifact.read_verified()
    print(
        f"OK tool={artifact.name} version={artifact.version} "
        f"platform={artifact.platform} sha256={artifact.sha256} size={artifact.size}"
    )


async def _cmd_tool_policy(args: argparse.Namespace) -> None:
    """Resolve whether one platform must receive managed or system bytes."""
    from app.core.errors import RuntimeStateError
    from app.infrastructure.tool_catalog import ToolCatalog

    catalog = ToolCatalog(_settings().tool_source_dir)
    release_version = catalog.version(args.tool)
    if release_version != args.expected_version:
        raise RuntimeStateError(
            params={"path": str(_settings().tool_source_dir)},
            details=(
                f"the release image contains {args.tool} {release_version}, "
                f"not the requested {args.expected_version}"
            ),
        )
    fallback_minimum = catalog.system_fallback_minimum(args.tool)
    if fallback_minimum != args.expected_fallback_minimum:
        raise RuntimeStateError(
            params={"path": str(_settings().tool_source_dir)},
            details=(
                f"the release policy requires system {args.tool} "
                f">={fallback_minimum}, not >={args.expected_fallback_minimum}"
            ),
        )
    if catalog.has_managed_artifact(args.tool, args.platform):
        catalog.select(args.tool, args.platform).read_verified()
        print("managed")
        return
    catalog.validate_system_fallback(args.tool, args.platform)
    print("system")


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


async def _cmd_overlay(args: argparse.Namespace) -> None:
    from app.infrastructure.docker import DockerAdapter
    from app.services.overlay import OverlayService

    settings = _settings()
    service = OverlayService(
        settings, _helper_client(settings), DockerAdapter(settings.docker_socket)
    )

    if args.action == "enable":
        enabled = await service.enable(
            endpoint_host=args.endpoint,
            subnet=args.subnet,
            default_mode=args.default_mode,
            port=args.port,
        )
        print(f"The overlay is on. The hub answers on {enabled.endpoint}/udp.")
        print(f"Hub address: {enabled.hub_address}")
        print("Open that port, then put a probe on it:")
        print("  sudo ./prtg-nats overlay add USER")
        return

    if args.action == "disable":
        await service.disable()
        print("The overlay hub is stopped.")
        print("Every probe keeps its address and key, and still reaches this")
        print('host the ordinary way. Probes left in mode "on" reach NATS only')
        print('through the tunnel - put them back with "overlay mode USER auto".')
        return

    if args.action == "init":
        public_key = service.initialise()
        print(f"Overlay hub key: {public_key}")
        print(f"Configuration:   {settings.runtime_dir / 'overlay' / 'prtgnats0.conf'}")
        return

    if args.action == "status":
        hub = service.status()
        print(f"Enabled:     {'yes' if hub.enabled else 'no'}")
        print(f"Endpoint:    {hub.endpoint or '-'}")
        print(f"Subnet:      {hub.subnet}")
        print(f"Hub address: {hub.hub_address}")
        print(f"Hub key:     {hub.hub_public_key or '-'}")
        print(f"Interface:   {_interface_state(hub.interface_up)}")
        print(f"Default:     {hub.default_mode}")
        if not hub.peers:
            print("\nNo probe is on the overlay yet.")
            return
        print(f"\n  {'PROBE':28} {'ADDRESS':16} MODE")
        for peer in hub.peers:
            print(f"  {peer.nats_username:28} {peer.address:16} {peer.mode}")
        return

    if args.action == "add":
        added = await service.attach(args.username, args.mode)
        print(f"{args.username} is on the overlay at {added.address} ({added.mode}).")
        return

    if args.action == "mode":
        changed = await service.set_mode(args.username, args.mode, force=args.force)
        print(f"{args.username} is now in mode {changed.mode} ({changed.summary}).")
        return

    if args.action == "remove":
        await service.detach(args.username, force=args.force)
        print(f"{args.username} is off the overlay.")
        return

    if args.action == "show":
        state = await service.refresh(args.username)
        print(f"Probe:      {state.nats_username}")
        print(f"Mode:       {state.mode}")
        print(f"Address:    {state.address or '-'}")
        print(f"Endpoint:   {state.endpoint or '-'}")
        print(f"Interface:  {'up' if state.interface_up else 'down'}")
        age = "never" if state.handshake_age is None else f"{state.handshake_age}s ago"
        print(f"Handshake:  {age}")
        print(f"NATS path:  {'tunnel' if state.route_active else 'direct'}")
        print(f"Direct was: {state.direct_ok}")
        return


def _interface_state(up: bool | None) -> str:
    """Three answers, because "cannot tell" is not "down"."""
    if up is None:
        return "unknown (this container cannot read it)"
    return "up" if up else "down"


def _helper_client(settings: Settings) -> ProbeHelperClient:
    from app.infrastructure.probe_helper import ProbeHelperClient, SshHelperTransport

    return ProbeHelperClient(
        SshHelperTransport(
            key_path=settings.ssh_key_path,
            known_hosts_path=settings.ssh_known_hosts_path,
            connect_timeout=settings.ssh_connect_timeout_seconds,
        ),
        default_timeout=settings.ssh_command_timeout_seconds,
    )


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

    helper = _helper_client(settings)
    connection = ProbeConnection.for_probe(inventory)
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

    export_tool = commands.add_parser(
        "export-tool", help="emit one signed managed-tool envelope"
    )
    export_tool.add_argument("tool")
    export_tool.add_argument("platform")
    export_tool.add_argument("expected_version")

    check_tool = commands.add_parser(
        "check-tool", help="verify one managed-tool release artifact"
    )
    check_tool.add_argument("tool")
    check_tool.add_argument("platform")
    check_tool.add_argument("expected_version")

    overlay = commands.add_parser("overlay", help="the WireGuard overlay")
    overlay_actions = overlay.add_subparsers(dest="action", required=True)
    overlay_actions.add_parser("init", help="create the hub key and render its config")
    overlay_enable = overlay_actions.add_parser(
        "enable", help="turn the overlay on and start the hub"
    )
    overlay_enable.add_argument("endpoint", help="the address probes dial")
    overlay_enable.add_argument("--port", type=int, default=None)
    overlay_enable.add_argument("--subnet", default=None)
    overlay_enable.add_argument(
        "--default-mode", choices=("off", "auto", "on"), default=None
    )
    overlay_actions.add_parser("disable", help="stop the hub, keeping every peer")
    overlay_actions.add_parser("status", help="the hub and every peer")
    overlay_add = overlay_actions.add_parser("add", help="put a probe on the overlay")
    overlay_add.add_argument("username")
    overlay_add.add_argument(
        "--mode",
        choices=("off", "auto", "on"),
        default=None,
        help="when NATS traffic takes the tunnel (default: OVERLAY_DEFAULT_MODE)",
    )
    overlay_mode = overlay_actions.add_parser("mode", help="change a probe's mode")
    overlay_mode.add_argument("username")
    overlay_mode.add_argument("mode", choices=("off", "auto", "on"))
    overlay_mode.add_argument(
        "--force",
        action="store_true",
        help="switch off even when the probe answers only through the tunnel",
    )
    overlay_remove = overlay_actions.add_parser(
        "remove", help="take a probe off the overlay"
    )
    overlay_remove.add_argument("username")
    overlay_remove.add_argument("--force", action="store_true")
    overlay_show = overlay_actions.add_parser("show", help="one probe's overlay state")
    overlay_show.add_argument("username")

    tool_policy = commands.add_parser(
        "tool-policy", help="resolve the approved source for one platform"
    )
    tool_policy.add_argument("tool")
    tool_policy.add_argument("platform")
    tool_policy.add_argument("expected_version")
    tool_policy.add_argument("expected_fallback_minimum")

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
        "export-tool": _cmd_export_tool,
        "check-tool": _cmd_check_tool,
        "tool-policy": _cmd_tool_policy,
        "user": _cmd_user,
        "overlay": _cmd_overlay,
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
